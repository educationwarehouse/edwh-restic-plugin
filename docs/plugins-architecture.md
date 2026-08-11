# Plugin architecture: custom repositories & event notifications

Status: **design proposal**, not implemented. Decisions taken so far are recorded in
[§10 Decisions](#10-decisions-taken).

## 1. Goals and non-goals

Two extension points, one discovery mechanism:

1. **Repository plugins** — add a restic-supported backup target that this tool does not
   ship (e.g. Azure, rest-server, Google Cloud Storage) from an external package.
2. **Notifier plugins** — react to backup/restore/check lifecycle events over ntfy,
   Discord, a generic webhook, healthchecks.io, and so on.

Explicit non-goals:

- **No middleware layer.** Nothing may intercept and mutate the restic command line.
  `captain-hooks/*` scripts are already the "run arbitrary logic around a backup" escape
  hatch; a second, more powerful one would make every failure unattributable.
- **No async or queued delivery.** Notification is synchronous, sequential, and
  best-effort. Backups run under cron; the process exits when the task ends, so a
  background queue would silently drop messages.
- **No message template language.** Notifiers get an `Event` object and a default
  `format()` they may override in Python.

### Naming

`captain-hooks/` already means "backup scripts, one per target" in this codebase
(`repositories/__init__.py:28`, `get_scripts`). The new mechanism is therefore called
**events** and **notifiers**, never "hooks". Reusing "hook" would make every future bug
report ambiguous.

## 2. Current state: what already works and what blocks reuse

`RepositoryRegistrations` (`repositories/__init__.py:406`) is already most of a plugin
registry — priority heap, aliases, lazy discovery, `@register()` decorator. Four defects
block third-party use.

### 2.1 Discovery is hardcoded to the package directory

`_find_items()` (`repositories/__init__.py:495`) globs `Path(__file__).parent/"*.py"`. An
installed external package can never be found. Fix in §3.

### 2.2 `registrations.get()` does not trigger discovery

```python
def get(self, name: str) -> typing.Type[Repository] | None:
    return self._aliases.get(name)   # repositories/__init__.py:452-453
```

`_aliases` is only populated by `push()`, which only runs from `_find_items()`, which is
only reached via the `queue` property. `get()` bypasses `queue` entirely. This works today
purely because `cli_repo` happens to call `to_ordered_dict()` first (`tasks.py:31`) before
`registrations.get()` (`tasks.py:43`). Any other caller — including a future
`notify`/`healthcheck` task — gets `None` from a correctly registered repository.

**Fix:** `get()` must touch `self.queue` first, same as `to_ordered_dict()`.

### 2.3 The abstract surface is twice as wide as it needs to be

`Repository` declares six abstract members: `setup`, `prepare_for_restic`, `uri`, `wipe`,
`bucket`, `prepare_rclone_config`. Only the first three are needed to perform a backup.
The other three exist solely for the `wipe` (`tasks.py:340`) and `move` (`tasks.py:355`)
tasks.

Two pieces of evidence that this already hurts internally:

- The `check_abstract_methode` task (`tasks.py:389`) exists only to find subclasses that
  forgot one.
- `tests/test_repository_detection.py:31` defines `DummyRepostiory` implementing exactly
  `setup` and `prepare_for_restic` — the test author's implicit model of the minimum
  surface, which the base class contradicts.

**Fix:** keep `setup`, `prepare_for_restic`, `uri` abstract. Demote `wipe`, `bucket`,
`prepare_rclone_config` to concrete base methods raising `UnsupportedOperation`, and have
`wipe`/`move` catch it and print `repository 'x' does not support move`. A third-party
repository then costs ~30 lines instead of ~80, and `check_abstract_methode` shrinks to
checking three names.

### 2.4 Failure events are structurally impossible today

This is the blocker for the entire notification feature, and it is not obvious.

```python
files = self.get_scripts(target, verb)   # calls sys.exit(255) on no match — line 222
...
if worst_status_code := max(file_codes) > 0:
    exit(worst_status_code)              # line 301
```

Both are bare process exits from inside library code. No `except` or `finally` in a caller
can run, so **no `backup.failed` notification can ever be delivered** for the two most
common failure modes. Notification depends on converting these into exceptions first.

While there: `worst_status_code := max(file_codes) > 0` is a precedence bug. The walrus
binds the *comparison result*, so the process exits `1` for every failure regardless of
the real script exit code, and the variable name is a lie. Intended:

```python
if (worst := max(file_codes)) > 0:
    raise ResticScriptError(files, file_codes, worst)
```

## 3. Shared discovery

One module, `plugins.py`, with a generic registry base used by both extension points.

Three sources, in ascending precedence:

1. **In-package modules** (current behaviour, repositories only).
2. **Python entry points** — `edwh_restic_plugin.repositories` and
   `edwh_restic_plugin.notifiers`. This mirrors how edwh finds this plugin
   (`[project.entry-points."edwh.tasks"]`, `pyproject.toml:44`), so being a
   plugin-of-a-plugin adds no new concept: an external package declares

   ```toml
   [project.entry-points."edwh_restic_plugin.notifiers"]
   ntfy = "edwh_restic_ntfy"
   ```

   The entry point's target is *imported for its side effects* — the `@register()`
   decorator does the registration — so the value may be a module or a class.
3. **Explicit module list** from `.toml` (`[restic.plugins] modules = [...]`) for anything
   installed but not declaring an entry point.

Discovery must be parameterisable:

```python
def discover(self, *, in_package=True, entry_points=True, config=True) -> None: ...
```

`clear()` followed by scoped rediscovery is what keeps `tests/test_repository_detection.py`
deterministic; without it, installing any third-party repository plugin in a dev
environment breaks `test_detection`'s `== "hetzner"` assertion (see §9).

Failure isolation: an entry point that raises on import must log a warning and be skipped,
never abort the run. A broken notification plugin must not prevent a backup.

## 4. Extension point 1 — Repository

Unchanged public API: `@register(short_name, aliases=(), priority=-1)` on a `Repository`
subclass. Minimum viable third-party repository after §2.3:

```python
from edwh_restic_plugin.repositories import Repository, register

@register("azure", aliases=("az",), priority=20)
class AzureRepository(Repository):
    def setup(self) -> None:
        self.check_env("AZURE_NAME", None, "Container to store backups in")
        self.check_env("AZURE_PASSWORD", generate_password(silent=True), "Restic password")
        self.check_env("AZURE_ACCOUNT_NAME", None, "Storage account name")
        self.check_env("AZURE_ACCOUNT_KEY", None, "Storage account key")

    def prepare_for_restic(self, c) -> None:
        env = self.env_config
        os.environ["RESTIC_PASSWORD"] = env["AZURE_PASSWORD"]
        os.environ["AZURE_ACCOUNT_NAME"] = env["AZURE_ACCOUNT_NAME"]
        os.environ["AZURE_ACCOUNT_KEY"] = env["AZURE_ACCOUNT_KEY"]

    @property
    def uri(self) -> str:
        return f"azure:{self.env_config['AZURE_NAME']}:/"
```

`wipe` and `move` degrade with a clear message; `backup`, `restore`, `check`, `forget`,
`snapshots`, `du`, `run` all work.

Two conventions worth documenting rather than enforcing, because they are load-bearing
elsewhere in the code:

- The `<SHORTNAME>_PASSWORD` env var is how `cli_repo` auto-selects a default repository
  when `--connection-choice` is omitted (`tasks.py:29-35`). A plugin that names its
  password variable differently is silently unselectable.
- `_short_name` and `_aliases` feed forget-policy lookup (`determine_forget_policy`,
  `repositories/__init__.py:359`), so `[restic.forget.azure]` works for free.

## 5. Extension point 2 — Notifier

```python
class Notifier(abc.ABC):
    _short_name: str                      # set by @register_notifier()
    subscribes: tuple[str, ...] = ("*",)  # default; overridden by .toml routing

    @classmethod
    def from_config(cls, env: Mapping[str, str], options: Mapping[str, Any]) -> Self | None:
        """Build an instance, or return None to stay inactive (missing credentials)."""

    def format(self, event: Event) -> str:
        """Human-readable one-liner. Override for channel-specific payloads."""

    @abc.abstractmethod
    def send(self, event: Event) -> None:
        """Deliver. May raise; the dispatcher swallows and reports."""
```

Returning `None` from `from_config` rather than raising is what makes "configured =
active" work without a separate enable flag, and matches how repositories are selected by
the presence of their env vars.

## 6. Event model

A frozen dataclass, not a dict, so plugin authors get autocompletion and mypy coverage.

```python
@dataclass(frozen=True, kw_only=True)
class Event:
    name: str                    # "backup.failed"
    ts: datetime
    level: Literal["info", "warning", "error"]
    repo: str                    # short_name, e.g. "s3"
    repo_uri: str                # REDACTED — see §7
    host: str                    # RESTICHOSTNAME or platform hostname
    project: str                 # cwd name, or [restic.notify] project
    target: str | None           # backup target ("files", "stream", ...)
    duration: float | None       # seconds, on terminal events
    exit_code: int | None
    snapshot: str | None
    message: str | None          # the snapshot message / error text
    extra: Mapping[str, Any]     # event-specific, redacted
```

### Taxonomy

| Event | Level | Notes |
|---|---|---|
| `backup.started` | info | Arms the watchdog (§8). |
| `backup.script.failed` | error | Per `captain-hooks` script, with its real exit code. Requires §2.4. |
| `backup.succeeded` | info | Carries snapshot ids. Heartbeat notifiers subscribe here. |
| `backup.failed` | error | Requires §2.4. |
| `backup.slow` | warning | Watchdog; see §8. |
| `restore.started` / `.succeeded` / `.failed` | info/info/error | `restore` also destroys pg volumes (`tasks.py:143`), so failure here is high-severity in practice. |
| `check.succeeded` / `check.failed` | info/error | **The most valuable pair.** Silent repository corruption is the failure mode you otherwise discover during a restore. |
| `forget.succeeded` / `.failed` | info/error | Include snapshots removed; a policy that suddenly prunes 400 snapshots is a signal. |
| `wipe.started` / `.succeeded` | warning/warning | Destructive and irreversible — always notify, regardless of routing config. |

`configure` and `snapshots` emit nothing; they are interactive and read-only.

### Where events are emitted

At the **task layer** (`tasks.py`), via a decorator, not inside `Repository`:

```python
@task
@emits("backup")     # started / succeeded / failed / duration, from one decorator
def backup(c, target="", ...): ...
```

Rationale: tasks are the operation boundary a human cares about, one decorator yields the
whole terminal-state triple plus timing, and — decisively — `Repository` subclasses are
written by *plugin authors*, who must not have to remember to emit anything. Fine-grained
`backup.script.failed` is the exception and is emitted from `execute_files`.

### Dispatch semantics

- Sequential, synchronous, in registration order.
- Per-notifier timeout (default 5s), enforced by the notifier's own transport.
- Every `send()` in `try/except Exception` — logged to stderr, swallowed. A dead Discord
  webhook must never turn a good backup into a failed cron job. Confirmed decision, §10.
- Guarded by a `threading.Lock`, because the watchdog (§8) dispatches from a timer thread
  while the main thread may be dispatching a terminal event. Without the lock you get
  interleaved stderr and re-entrant notifier state.

## 7. Redaction is mandatory and lives in the Event factory

`prepare_for_restic` pushes `RESTIC_PASSWORD`, `AWS_SECRET_ACCESS_KEY`,
`AZURE_ACCOUNT_KEY` and friends into `os.environ`
(`repositories/s3.py:38-42`, and every other repository). Several `uri` implementations
can embed credentials — `sftp.py` builds a host string, `swift.py`/`b2.py` similar.

A notifier that does `f"backup of {event.repo_uri} failed"` and POSTs it to Discord
publishes secrets to a third party. This cannot be left to plugin authors.

**Design:** `Event` is only constructible through a factory that scrubs by *value*. Collect
the set of known-secret values (every env var whose key matches
`PASSWORD|SECRET|KEY|TOKEN|CREDENTIAL`, plus every `<REPO>_PASSWORD` from `.env`) and
replace each occurrence in every string field and in `extra` with `***`. Value-based
scrubbing, not key-based, because the leak path is a secret *interpolated into a URI*, where
the key name is long gone.

Test requirement: a test that seeds a recognisable secret into `.env`, emits every event
type, and asserts the literal never appears in any field of any dispatched event.

## 8. Watchdog: detecting hanging backups

A backup that hangs fires neither `succeeded` nor `failed`. It is invisible to every
notifier in §6 — a third state, not a variant of failure.

**Mechanism:** at `backup.started`, arm a `threading.Timer`; cancel it on any terminal
event. On expiry, emit `backup.slow` with elapsed time and the currently running script.
The main thread is blocked in `c.run(..., pty=True)`, so a daemon timer thread is the
correct primitive — no async, no subprocess supervision.

**It does not kill the backup.** Killing restic mid-write risks leaving a stale repository
lock, which is why `inv restic.unlock` exists (`tasks.py:279`); a watchdog that routinely
creates work for that task is a net loss. If a hard kill is ever wanted, it belongs behind
a separate, explicitly-named `hard_timeout` option, and invoke's `run(timeout=)` already
provides the mechanism.

**Escalating thresholds** rather than a single shot, since "slow" and "certainly wedged"
deserve different levels:

```toml
[restic.notify]
warn_after = ["30m", "2h"]        # -> backup.slow at each, level warning then error

[restic.notify.targets.stream]
warn_after = ["4h"]               # pg dumps are legitimately slow
```

Per-target override matters because `backup_files_*` and `backup_stream_*` scripts have
wildly different expected runtimes, and one global threshold would be tuned to the slowest
and therefore useless for the rest.

**Known limit — and why heartbeat is a separate feature.** The watchdog lives inside the
backup process. If the machine reboots, the container is evicted, or cron never fired,
the watchdog dies with everything else and nothing is sent. The complement is a
**heartbeat**: on `*.succeeded`, ping an external monitor (healthchecks.io, Uptime Kuma);
that service alarms on *silence*. The watchdog catches "running too long"; the heartbeat
catches "never ran". Neither covers the other. Under the "everything external" decision
(§10) a heartbeat is simply an external notifier subscribing to `backup.succeeded`, so it
costs core nothing but the contract itself.

## 9. Configuration

Split by sensitivity, following both existing precedents in this codebase:

**Secrets in `.env`** — consistent with `check_env` and with repository selection by
env-var presence. Never committed.

```
NTFY_TOPIC=backups-edwh
NTFY_TOKEN=tk_...
DISCORD_WEBHOOK_URL=https://...
HEALTHCHECKS_URL=https://hc-ping.com/...
```

**Routing and policy in `.toml`**, next to the existing `[restic.forget]`
(`forget.py:from_toml_file`), because per-event filtering is unreadable as env vars:

```toml
[restic.notify]
project    = "acme-prod"
channels   = ["ntfy", "discord"]      # omit = every configured notifier
min_level  = "warning"                # global floor
warn_after = ["30m", "2h"]

[restic.notify.ntfy]
events = ["*"]                        # ntfy gets everything

[restic.notify.discord]
events = ["backup.failed", "check.failed", "wipe.*"]   # humans get only the bad news

[restic.plugins]
modules = ["mycorp.restic_notifiers"]
```

Resolution order for a notifier: `.toml` per-channel `events` → `[restic.notify]`
`min_level` → the notifier's own `subscribes` default. `wipe.*` ignores `min_level`.

## 10. Decisions taken

| Question | Decision |
|---|---|
| Config location | Hybrid — secrets in `.env`, routing/policy in `.toml`. |
| Built-in notifiers | **None.** Core ships the interface only; ntfy/Discord/webhook/heartbeat are external packages (`edwh-restic-ntfy`, …). Core gains no HTTP dependency. |
| Notifier failure | Always swallowed. A backup never fails because a channel is down. |
| v1 scope | Repository entry-point discovery + narrowed abstract surface; event model + notifier registry + dispatch; watchdog for hanging backups. |
| Deferred | Heartbeat/dead-man's-switch (external notifier, needs no core work beyond `backup.succeeded`), `inv restic.healthcheck` snapshot-age task, local single-file plugins, hard kill on timeout. |

### Consequence of shipping zero built-in notifiers

An extension API with no in-tree consumer drifts out of sync with its own documentation.
Mitigation: **the test suite is the consumer.** A `MemoryNotifier` and a `DummyRepository`
register through the *identical* decorator and entry-point path an external package uses
(entry points declared in the test fixtures via a `pytest` plugin that installs a stub
distribution, or via `[restic.plugins] modules`). A breaking contract change then fails CI
rather than failing a user. This is not optional given the decision above.

## 11. Implementation order

Each step is independently shippable and leaves the tree green.

1. **Refactor exits into exceptions** (§2.4) — `ResticError`, `NoScriptsFound`,
   `ResticScriptError`; `get_scripts` and `execute_files` raise, `tasks.py` catches at the
   top level and sets the process exit code there. Fix the `max(file_codes)` precedence
   bug. *No new features; changes observable exit codes, so it goes first and alone.*
2. **Discovery + registry generalisation** (§3) — `plugins.py`, entry points, scoped
   `discover()`, and the `registrations.get()` fix (§2.2).
3. **Narrow the abstract surface** (§2.3) — `UnsupportedOperation`, graceful degradation
   in `wipe`/`move`.
4. **Event model + redaction** (§6, §7) — dataclass, factory, scrubber, and the
   secret-leak test. No dispatch yet.
5. **Notifier registry + `@emits` dispatch** (§5) — plus `MemoryNotifier` in tests.
6. **Watchdog** (§8) — timer, escalation, per-target thresholds.
7. **Reference external package** — `edwh-restic-ntfy` in a separate repository, which is
   also the real proof the contract is usable from outside.

## 12. Open questions

- **`.toml` file location.** `forget.py` defaults to `Path.cwd() / ".toml"` with a
  `default.toml` fallback and a copy-on-read side effect (`get_or_copy_policy`). Should
  `[restic.notify]` inherit that same discovery-and-copy behaviour, or read plainly? The
  copy-on-read is surprising for notification config, which has no per-project default
  worth materialising.
- **Multiple repositories, one backup.** `cli_repo` resolves exactly one repository per
  invocation. Should `Event.repo` ever be a list, i.e. is fan-out to several targets in a
  single run a foreseeable feature? If yes, decide now — it is in the event schema.
- **`restore` volume destruction.** `tasks.py:143-159` stops pg containers and removes
  volumes *before* calling `restore`. If the restore then fails, the data is already gone.
  Should `restore.failed` be forced to `level = "error"` and bypass `min_level` the way
  `wipe.*` does?
