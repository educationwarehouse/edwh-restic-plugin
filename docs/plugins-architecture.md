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
- **No plugin sandboxing.** A notifier runs in-process and is trusted like any other
  dependency. Every containment mechanism available in Python leaks anyway, and the
  realistic author of a plugin here is us, or one person solving one specific problem for
  themselves — not an untrusted marketplace. The event schema is built to prevent
  *accidental* disclosure (§7.1); nothing pretends to stop deliberate disclosure.

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

    def display_name(self) -> str:            # optional; defaults to _short_name
        return f"azure:{self.env_config['AZURE_NAME']}"
```

`wipe` and `move` degrade with a clear message; `backup`, `restore`, `check`, `forget`,
`snapshots`, `du`, `run` all work.

`display_name()` is the only addition to the `Repository` surface, and it exists for §7.1:
events need something human-readable to identify *which* repository they concern, and `uri`
cannot serve because several implementations embed credentials in it. The default returns
`_short_name` alone, so a plugin that ignores this method discloses nothing — the safe
behaviour is the one you get by doing nothing.

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
    contract: int = CONTRACT_VERSION      # see §7.2
    subscribes: tuple[str, ...] = ("*",)  # default; overridden by .toml routing

    @classmethod
    def from_config(cls, env: Mapping[str, str], options: Mapping[str, Any]) -> Self | None:
        """Build an instance, or return None to stay inactive (missing credentials)."""

    def format(self, event: Event) -> str:
        """Human-readable one-liner. Override for channel-specific payloads."""

    @abc.abstractmethod
    def send(self, event: Event) -> None:
        """Deliver. May raise; the dispatcher catches, logs and continues."""
```

**Activation is explicit.** Unlike repositories — which self-select on the presence of
their env vars — a notifier runs only if `[restic.notify] channels` names it. Installing a
package therefore changes nothing until it is wired up, and "why did this fire" has exactly
one answer, greppable in one file.

`from_config` returning `None` remains the way a *named but unconfigured* channel opts out:
listed in `channels` but with no token in `.env` means "not yet", logged once, not an error.
That keeps a partially provisioned machine from failing its backups over notification setup.

## 6. Event model

A frozen dataclass, not a dict, so plugin authors get autocompletion and mypy coverage.

```python
@dataclass(frozen=True, kw_only=True)
class Event:
    name: str                    # "backup.failed"
    ts: datetime
    level: Literal["info", "warning", "error"]
    repo: str                    # short_name, e.g. "s3"
    host: str                    # RESTICHOSTNAME or platform hostname
    project: str                 # cwd name, or [restic.notify] project
    target: str | None           # backup target ("files", "stream", ...)
    duration: float | None       # seconds, on terminal events
    exit_code: int | None
    snapshot: str | None
    message: str | None          # the snapshot message / error text
    logs: str | None             # restic stdout/stderr on failures — see §7.1
    extra: Mapping[str, str]     # event-specific; populated only by in-package emit sites
```

Note what is *absent*: no `repo_uri`, no `env`, no `os.environ` passthrough. That is the
whole of §7.1. The fields above are the complete contract surface — adding one is a
deliberate act, reviewed as such.

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
| `wipe.started` / `.succeeded` | warning/warning | Destructive and irreversible, but user-initiated. Filterable like everything else. |

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
- Every `send()` in `try/except Exception` — logged to stderr, swallowed. A dead Discord
  webhook must never turn a good backup into a failed cron job. Confirmed decision, §10.
- **Wall-clock timeout per notifier (default 5s), enforced by the dispatcher**, not
  delegated to the notifier's transport. Delegating is wishful: a plugin author who forgets
  `timeout=` on a `requests.post` hangs the backup indefinitely, and the whole point is that
  a notifier cannot affect the backup. A timed-out send is logged and abandoned like any
  other failure. The backup's exit code reflects the backup, never the telemetry about it.
- Guarded by a `threading.Lock`, because the watchdog (§8) dispatches from a timer thread
  while the main thread may be dispatching a terminal event. Without the lock you get
  interleaved stderr and re-entrant notifier state.

## 7. The contract: allowlisted fields, versioned

### 7.1 Events carry an allowlist, not a filtered dump

`prepare_for_restic` pushes `RESTIC_PASSWORD`, `AWS_SECRET_ACCESS_KEY`,
`AZURE_ACCOUNT_KEY` and friends into `os.environ` (`repositories/s3.py:38-42`, and every
other repository). Several `uri` implementations embed credentials — `sftp.py` builds a
host string, `swift.py`/`b2.py` similar.

The naive design assembles a rich event and then subtracts secrets. That fails **open**:
every field added later leaks by default until someone remembers to scrub it, and the
person adding the field is the least likely to be thinking about scrubbing.

**Inverted:** the event starts empty and gains only fields explicitly enumerated in §6. A
field nobody added is simply not there. This fails **closed** — the failure mode of
forgetting is a missing field in someone's Discord message, not a credential on the wire.

Two consequences worth naming:

- **`repo_uri` is gone.** It is the one genuinely useful-but-unsafe field: humans want to
  know *which* repository failed, but the raw URI can carry credentials. Replaced by
  `Repository.display_name()`, defaulting to `_short_name`, which a subclass may override
  to something informative-but-safe (`"s3:acme-backups"`). Safe by construction: a plugin
  that doesn't implement it discloses nothing.
- **No `env` and no `os.environ` passthrough**, in any form, including inside `extra`.
  `extra` is `Mapping[str, str]` and is populated only by emit sites in this package — it is
  contract surface, not an escape hatch.

**This is a guardrail, not a security boundary, and the docs must say so.** A notifier runs
in-process; it can read `os.environ` and `.env` directly whenever it likes. No in-process
Python sandbox changes that. The allowlist prevents *accidental* disclosure by a
well-intentioned plugin — which is the failure that will actually happen — and nothing more.
The honest framing for the README: **a notifier is trusted exactly like any other
dependency**, and the thing to actually watch is where you point it.

That warning belongs on `logs`, because free text is where the real exposure lives.
Structured fields are enumerable and reviewable; `logs` is whatever restic decided to print,
and restic prints repository URIs. Design decisions follow from that:

- `logs` carries full stdout/stderr — truncating it defeats the purpose, since the
  diagnostically useful part of a `check.failed` is exactly the detail.
- It is populated only on failure events. A successful backup needs no log body.
- The docs warn, once and prominently, that failure notifications may contain repository
  paths and hostnames, so a channel carrying `logs` should be one you'd be comfortable
  pasting a terminal session into. That is a routing decision, and `[restic.notify]`
  per-channel `events` already expresses it.

A value-based scrubber is *not* part of this design. It would mangle legitimate content and,
worse, create false confidence in a mechanism that cannot be complete. What survives is a
**test tripwire**: seed a recognisable secret into `.env`, emit every event type, assert the
literal appears in no dispatched field. If the allowlist is right it never fires; if someone
adds a careless field, CI catches it. That is the correct home for blocklist logic — an
assertion, not a runtime filter.

### 7.2 One integer, checked at discovery

```python
CONTRACT_VERSION = 1     # edwh_restic_plugin.plugins
```

A notifier declares `contract = 1`. On mismatch the dispatcher **warns and skips at
discovery time** — not mid-backup, and never by raising. The failure this actually catches
is the realistic one: a plugin pinned in some project's venv, still installed, after the
`Event` shape moved on. `AttributeError` at 04:00 inside a cron job is a bad way to learn
that; a line at startup saying "built for contract 1, this is 2, skipping" is a good one.

Supporting pieces, all cheap:

- Ship **`py.typed`** (the package has none today) so plugin authors get real type checking
  across the boundary.
- `Event`, `Notifier` and `Repository` are importable from **one stable module path**
  (`edwh_restic_plugin.plugins`), so plugins never reach into `.repositories` internals.
- `TypedDict` for structured payloads where useful, matching the existing `restictypes.py`
  idiom rather than inventing a second convention.
- The dispatcher **duck-types** `send` rather than requiring `isinstance`, so a notifier
  package can type against the ABC without a hard runtime import of it.

Bumping `CONTRACT_VERSION` is for changes that break a consumer: a removed or renamed field,
a changed `send` signature. Adding an event name does not bump it — which means a `*`
subscriber will receive names it has never heard of, and tolerating that is a documented
obligation of implementing `send`.

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

`channels` is also the activation list (§5): a notifier not named here does not run.

Resolution order for a notifier: `.toml` per-channel `events` → `[restic.notify]`
`min_level` → the notifier's own `subscribes` default.

**No event bypasses filtering.** An earlier draft exempted `wipe.*`, and considered exempting
`restore.failed`, on the grounds that unrecoverable-data events are too important to
misconfigure away. Rejected: predictable semantics are worth more than a hardcoded exception,
and an event that ignores the config it appears to obey is its own bug report. The
justification is easier than it first looks — both `wipe` and `restore` are *user-initiated
and interactive*, so the operator is already watching a terminal. The events that need to
reach you when nobody is looking are the cron'd ones (`backup.*`, `check.*`), and those are
filterable by the same rules as everything else.

### 9.1 `default.toml` → `.toml`: warn, don't write

`.toml` is gitignored (`.gitignore`'s `.*`); `default.toml` is committed. The intent is a
tracked template copied once into a per-project file that is then **frozen** — deliberately
so, because a project's tuned retention policy must not silently change when the template
does.

The current `get_or_copy_policy` implements the freeze by writing the template into `.toml`
on first read. That works, but the write is invisible and its shape is surprising: since
`determine_forget_policy` (`repositories/__init__.py:359`) tries `_short_name` first, the
first `inv restic.forget` against an S3 repository writes `[restic.forget.s3] = <the default
values>`. `.toml` then asserts that s3 is customised when it merely holds defaults — and a
user who later hand-edits `[restic.forget.default]` is silently overridden by that
auto-written block.

Warning instead of writing preserves the freeze and removes the surprise:

> `.toml` has no `[restic.notify]`, but `default.toml` does. Copy the block to adopt the
> default, or add an empty `[restic.notify]` to keep current behaviour and silence this.

The key missing → warn → use `default.toml`'s value *for this run only*, write nothing. The
freeze becomes an explicit user act rather than a side effect of whichever task happened to
run first, and the two escape hatches are exactly the two intents: an empty block means "I
know, leave it", a copied block means "adopt and freeze".

Applies to `[restic.notify]`, and worth backporting to `[restic.forget]` — same file, same
confusion, and it makes the two sections behave identically.

**Independent of that**, one defect in `get_or_copy_policy` is worth fixing while nearby:
its third branch is dead code. It runs only when `from_toml_file(subkey, default_toml_path)`
returns `None`, which requires `default.toml` to have neither `[subkey]` nor `[default]` — in
which case `from_toml_file("default", default_toml_path)` returns `None` too, because
`from_toml_file` already falls back to `[default]` internally
(`section := forget.get(subkey) or forget.get("default")`). Unreachable on every path.

## 10. Decisions taken

| Question | Decision |
|---|---|
| Config location | Hybrid — secrets in `.env`, routing/policy in `.toml`. |
| `default.toml` → `.toml` | Copy-once-then-frozen semantics kept. Warn on a missing key instead of writing one (§9.1). |
| Built-in notifiers | **None.** Core ships the interface only; ntfy/Discord/webhook/heartbeat are external packages (`edwh-restic-ntfy`, …). Core gains no HTTP dependency. |
| Extension mechanism | A single tier: the Python entry-point API. No URL-library delegation, no executable-hook tier, no subprocess isolation. |
| Trust model | Documented, not enforced. A notifier is trusted like any dependency; the docs warn about log destinations (§7.1). |
| Event fields | Allowlist. No `repo_uri`, no env passthrough. Full stdout/stderr permitted in `logs` on failures. |
| Notifier activation | Explicit — named in `[restic.notify] channels`. Unlike repositories, not env-presence. |
| Notifier failure | Caught, logged, timed out by the dispatcher, never fatal. A backup never fails because a channel is down. |
| Event filtering | Uniform. No event bypasses `min_level` or per-channel `events`. |
| Contract versioning | `CONTRACT_VERSION` integer; warn-and-skip on mismatch at discovery. Plus `py.typed`. |
| Multiple repos per run | Out of scope, nothing reserved in the schema. One repository per invocation, as `cli_repo` does today. |
| v1 scope | Repository entry-point discovery + narrowed abstract surface; event model + notifier registry + dispatch; watchdog for hanging backups. |
| Deferred | Heartbeat/dead-man's-switch (external notifier, needs no core work beyond `backup.succeeded`), `inv restic.healthcheck` snapshot-age task, local single-file plugins, hard kill on timeout. |

### Rejected, and why

- **Delegating channels to a URL library (Apprise).** It would cover ~100 services with no
  plugin code, and is what the nearest comparable project ([borgmatic](https://torsion.org/borgmatic/reference/configuration/monitoring/apprise/))
  does. Rejected as incoherent with the registry: a plugin already has full in-process
  access, so constraining the *channel* to a curated URL list restricts nothing an attacker
  cares about while adding a dependency and a second configuration idiom. A proper Python
  API is the better UX for the actual audience.
- **Executable hooks (JSON on stdin, scrubbed env).** Real containment and language-agnostic,
  but solves a problem this project does not have. `execute_files` already runs
  `captain-hooks/*` with `prepare_env_for_restic` applied, so project-directory code seeing
  credentials is an accepted boundary today.
- **Runtime value-based secret scrubbing.** Mangles legitimate content and creates false
  confidence in an incomplete mechanism. Retained only as a test tripwire (§7.1).
- **A `run_id` for future fan-out.** Nothing to correlate while one invocation means one
  repository; a receiving service can key on `(host, project, target, ts)`.

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
4. **Event model + contract** (§6, §7) — the dataclass and its factory,
   `Repository.display_name()`, `CONTRACT_VERSION`, `py.typed`, and the secret-leak tripwire
   test. No dispatch yet.
5. **Notifier registry + `@emits` dispatch** (§5) — activation from `[restic.notify]
   channels`, dispatcher-enforced timeout, contract check at discovery, plus `MemoryNotifier`
   in tests.
6. **Watchdog** (§8) — timer, escalation, per-target thresholds.
7. **Reference external package** — `edwh-restic-ntfy` in a separate repository, which is
   also the real proof the contract is usable from outside.

## 12. Known issues outside this design

Both are pre-existing and independent of the plugin work; recorded here because they were
found while mapping the code, not proposed as part of it.

- **`restore` destroys before it verifies.** `tasks.py:143-159` stops the pg containers and
  removes their volumes *before* calling `restore`. If the restore then fails — bad snapshot
  id, unreachable repository, wrong password — the old data is already gone and the failure
  notification arrives too late to matter. Notification cannot fix this; the ordering can.
  Verifying the snapshot exists and is readable before destroying anything is a separate,
  small change, and worth filing on its own.
- **`get_or_copy_policy` third branch is unreachable.** See §9.1 for the derivation.
