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
(`get_scripts`, `repositories/__init__.py`). The new mechanism is therefore called
**events** and **notifiers**, never "hooks". Reusing "hook" would make every future bug
report ambiguous.

## 2. Current state: what already works and what blocks reuse

`RepositoryRegistrations` (`repositories/__init__.py`) is already most of a plugin
registry — priority heap, aliases, lazy discovery, `@register()` decorator. Four defects
block third-party use.

### 2.1 Discovery is hardcoded to the package directory

`_find_items()` (`repositories/__init__.py`) globs `Path(__file__).parent/"*.py"`. An
installed external package can never be found. Fix in §3.

### 2.2 `registrations.get()` does not trigger discovery

```python
def get(self, name: str) -> typing.Type[Repository] | None:
    return self._aliases.get(name)   # RepositoryRegistrations.get
```

`_aliases` is only populated by `push()`, which only runs from `_find_items()`, which is
only reached via the `queue` property. `get()` bypasses `queue` entirely. This works today
purely because `cli_repo` happens to call `to_ordered_dict()` before `registrations.get()`.
Any other caller — including a future
`notify`/`healthcheck` task — gets `None` from a correctly registered repository.

**Fix:** `get()` must touch `self.queue` first, same as `to_ordered_dict()`.

### 2.3 The abstract surface is twice as wide as it needs to be

`Repository` declares six abstract members: `setup`, `prepare_for_restic`, `uri`, `wipe`,
`bucket`, `prepare_rclone_config`. Only the first three are needed to perform a backup.
The other three exist solely for the `wipe` and `move` tasks (`tasks.py`).

Two pieces of evidence that this already hurts internally:

- The `check_abstract_methode` task (`tasks.py`) exists only to find subclasses that
  forgot one.
- `tests/test_repository_detection.py` defines `DummyRepostiory` implementing exactly
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
files = self.get_scripts(target, verb)   # get_scripts calls sys.exit(255) on no match
...
if worst_status_code := max(file_codes) > 0:
    exit(worst_status_code)              # in execute_files
```

There is a third, in a repository implementation: `sftp.py` calls `exit(1)` when its
connection check fails.

All three are bare process exits from inside library code. No `except` or `finally` in a caller
can run, so **no `backup.failed` notification can ever be delivered** for the most common
failure modes. Notification depends on converting these into exceptions first.

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
   (`[project.entry-points."edwh.tasks"]`, `pyproject.toml`), so being a
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
  when `--connection-choice` is omitted (`tasks.py`). A plugin that names its
  password variable differently is silently unselectable.
- `_short_name` and `_aliases` feed forget-policy lookup (`determine_forget_policy`,
  `repositories/__init__.py`), so `[restic.forget.azure]` works for free.

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

### 5.1 A complete external notifier

**Plugins never read `.toml` or `default.toml`.** Core resolves configuration and hands over
two plain mappings: `options` is the already-resolved `[restic.notify.<short_name>]` table
(template fallback and the §9.1 warnings all happen upstream), and `env` is the parsed `.env`
dict. `env` is passed explicitly rather than read from `os.environ` for two reasons — it
mirrors `Repository.env_config` (`repositories/__init__.py`), and by the time a notifier
runs, `os.environ` has been loaded with restic's credentials by `prepare_for_restic`, so the
convenient path should not be the one that walks past them.

`mycorp_restic_webhook/__init__.py`, entire:

```python
from typing import Any, Mapping, Self

import httpx
from edwh_restic_plugin.plugins import (
    CONTRACT_VERSION, BackupEvent, CheckEvent, Event, Failed, Notifier, register_notifier,
)


@register_notifier("mywebhook")
class MyWebhook(Notifier):
    contract = CONTRACT_VERSION

    def __init__(self, url: str, secret: str) -> None:
        self.url = url
        self.secret = secret

    @classmethod
    def from_config(cls, env: Mapping[str, str], options: Mapping[str, Any]) -> Self | None:
        url = options.get("webhook_url")              # from .toml
        secret = env.get("PLUGIN_WEBHOOK_SECRET")     # from .env
        if not (url and secret):
            return None                               # named but unprovisioned -> inactive
        return cls(url, secret)

    def format(self, event: Event) -> str:
        match event:
            case BackupEvent(status=Failed(exit_code=code, logs=logs)):
                return f"{event.repo_display} backup failed (exit {code})\n{logs or ''}"
            case CheckEvent(status=Failed()):
                return f"REPOSITORY DAMAGED: {event.repo_display}"
            case _:
                return super().format(event)          # sensible default for the rest

    def send(self, event: Event) -> None:
        httpx.post(
            self.url,
            headers={"X-Webhook-Secret": self.secret},
            json={"event": event.name, "level": event.level, "text": self.format(event)},
        )
```

Its `pyproject.toml` — one entry point, and `httpx` is its dependency, not ours:

```toml
[project.entry-points."edwh_restic_plugin.notifiers"]
mywebhook = "mycorp_restic_webhook"
```

Consuming project, `.env`:

```
PLUGIN_WEBHOOK_SECRET=hunter2
```

Consuming project, `.toml`:

```toml
[restic.notify]
channels = ["mywebhook"]

[restic.notify.mywebhook]
webhook_url = "https://hooks.mycorp.internal/restic"
events      = ["backup.failed", "check.failed"]
```

Everything a plugin author must know is in that file: one decorator, one classmethod, one
`send`. No config parsing, no discovery code, no `.env` handling, no timeout management (§5,
dispatch semantics — core enforces it), no exception handling (core catches).

## 6. Event model

Two axes carry real information, and picking only one makes the other's fields optional lies:

- **Operation** determines `target`, `snapshot`, `message`, `policy`, `snapshots_removed`.
- **Phase** determines `duration`, `exit_code`, `logs`, `elapsed`, `threshold`.

So compose them. One frozen dataclass per **operation**, carrying a `status` that is a small
union of **phase** objects. Phases are universal across operations, so there is nothing to map
between the two axes.

```python
Level = Literal["info", "warning", "error"]

# --- phase axis: shared by every operation ---

@dataclass(frozen=True, kw_only=True)
class Started:
    phase: Literal["started"] = "started"

@dataclass(frozen=True, kw_only=True)
class Succeeded:
    phase: Literal["succeeded"] = "succeeded"
    duration: float

@dataclass(frozen=True, kw_only=True)
class Failed:
    phase: Literal["failed"] = "failed"
    duration: float
    exit_code: int
    logs: str | None = None        # full stdout/stderr — §7.1

@dataclass(frozen=True, kw_only=True)
class Slow:
    phase: Literal["slow"] = "slow"
    elapsed: float                 # not `duration`: the operation has not finished
    threshold: float               # which warn_after step tripped

Status = Started | Succeeded | Failed | Slow

# --- operation axis ---

@dataclass(frozen=True, kw_only=True)
class BasicEvent:
    operation: ClassVar[str]       # "backup", set per subclass
    status: Status
    ts: datetime
    level: Level
    repo: str                      # short_name, e.g. "s3"
    repo_display: str              # Repository.display_name() — §7.1, never the raw uri
    host: str                      # RESTICHOSTNAME or platform hostname
    project: str                   # cwd name, or [restic.notify] project

    @property
    def name(self) -> str:
        return f"{self.operation}.{self.status.phase}"

@dataclass(frozen=True, kw_only=True)
class ScriptFailure:
    script: str
    exit_code: int

@dataclass(frozen=True, kw_only=True)
class BackupEvent(BasicEvent):
    operation: ClassVar[str] = "backup"
    target: str | None = None
    snapshot: str | None = None
    message: str | None = None
    scripts: tuple[ScriptFailure, ...] = ()    # captain-hooks that failed, if any

@dataclass(frozen=True, kw_only=True)
class RestoreEvent(BasicEvent):
    operation: ClassVar[str] = "restore"
    target: str | None = None
    snapshot: str | None = None
    scripts: tuple[ScriptFailure, ...] = ()

@dataclass(frozen=True, kw_only=True)
class CheckEvent(BasicEvent):
    operation: ClassVar[str] = "check"
    read_data: bool = False
    subset: str = ""

@dataclass(frozen=True, kw_only=True)
class ForgetEvent(BasicEvent):
    operation: ClassVar[str] = "forget"
    policy: str | None = None
    snapshots_removed: int | None = None

@dataclass(frozen=True, kw_only=True)
class WipeEvent(BasicEvent):
    operation: ClassVar[str] = "wipe"

Event = BackupEvent | RestoreEvent | CheckEvent | ForgetEvent | WipeEvent
```

Nine classes rather than the fourteen an earlier draft proposed, and **adding an operation costs
one class, not four**. `name` is derived, so `.toml` patterns like `backup.failed` and
`subscribes = ("check.*",)` keep working with no flat list of names to maintain anywhere.

`level` stays a field rather than being derived from the phase, because `wipe.succeeded` is a
`warning` while `backup.succeeded` is `info` — the mapping is not one-to-one.

### There is no separate script-failure event

An earlier draft had `backup.script.failed` as its own class, emitted per failing
`captain-hooks` script. Dropped, for a reason that is about cardinality rather than typing:
`execute_files` runs N scripts, so per-script events mean a notifier receives N+1 events for one
backup — four Discord messages for one failed nightly job.

The failures are instead collected onto the terminal event, as `scripts`. One `backup.failed`
can then say *"3 of 5 scripts failed: backup_files_pg.sh(2), backup_stream_db.sh(1)"*. Empty
tuple rather than `None`, so the field is honest at every phase. Only `backup` and `restore`
carry it, since they are the operations that run `captain-hooks` through `execute_files`.

What this gives up: a notification the instant an individual script fails. That is not worth
having — `execute_files` runs sequentially inside one cron job, so nobody is watching between
scripts.

### Reading an event

Both `ty` and `mypy` narrow a `Literal` discriminant through attribute access, in **both**
branches. No accessor helper, no overloads, no `cast`:

```python
if event.status.phase == "failed":
    event.status.exit_code          # -> Failed
else:
    event.status                    # -> Started | Succeeded | Slow
```

`match` narrows through both axes at once, which is the idiom worth documenting:

```python
match event:
    case BackupEvent(status=Failed(exit_code=code, logs=logs), target=target): ...
    case ForgetEvent(status=Succeeded(), snapshots_removed=n): ...
    case BasicEvent(status=Slow(elapsed=secs)): ...     # any operation running long
```

That last arm is the payoff of putting phase on its own axis: "anything is running long" and
"anything failed" become one arm each, instead of one per operation.

### Exhaustiveness

`assert_never` still works, now over five operations instead of fourteen names:

```python
match event:
    case BackupEvent() | RestoreEvent(): ...
    case CheckEvent() | ForgetEvent() | WipeEvent(): ...
    case _:
        assert_never(event)   # mypy errors here if an operation is unhandled
```

**Opt-in, and it interacts with versioning (§7.2).** Adding an operation widens the union
without changing existing members, so a notifier keeps *running* unchanged — but one ending in
`assert_never` will *fail type-checking* until it adds an arm. Authors should choose knowingly:

| Final arm | New operation at runtime | New operation in CI |
|---|---|---|
| `case _: return` | ignored silently | passes |
| `case _: self.generic(event)` | handled generically | passes |
| `case _: assert_never(event)` | unreachable, so no effect | **fails, naming the missing member** |

Notifiers wanting neither can ignore the union and use `BasicEvent` fields plus `event.name`,
`event.level` and `event.status.phase` — which is what §5.1's `send` does.

### Keeping the classes, the union and the emit sites in sync

Operations self-register at class creation, so there is no traversal and no second list:

```python
@dataclass(frozen=True, kw_only=True)
class BasicEvent:
    operations: ClassVar[dict[str, type["BasicEvent"]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "operation" not in cls.__dict__:
            return                       # intermediate base, or a subclass reusing one
        if clash := BasicEvent.operations.get(cls.operation):
            raise TypeError(f"{cls.__name__} reuses {cls.operation!r} from {clash.__name__}")
        BasicEvent.operations[cls.operation] = cls
```

One ordering fact worth a comment in the source, because it is easy to break:
`__init_subclass__` runs **before** `@dataclass` is applied to the subclass, and
`dataclasses.fields(cls)` does *not* raise there — `__dataclass_fields__` is inherited, so it
silently returns only the base's fields. Registration must therefore read only `__dict__`.
Since `operation` is a `ClassVar` set in the class body, that is all it needs.

Guarding on `"operation" in cls.__dict__` rather than `hasattr` is what lets intermediate bases
and plugin subclasses exist without re-registering an inherited value. Duplicate operations
become an **import-time error** rather than a test failure, which is where a definition-time
property belongs.

**What still needs a test** — only what the hook structurally cannot see:

```python
def test_union_matches_the_classes():
    """Orphan class, or junk in the union."""
    static, runtime = set(typing.get_args(Event)), set(BasicEvent.operations.values())
    assert static == runtime, {
        "defined but missing from Event": runtime - static,
        "in Event but not an operation": static - runtime,
    }

def test_every_operation_and_phase_is_emitted(pairs_seen_this_session):
    """An (operation, phase) nobody constructs is dead weight that still ships."""
    expected = {(op, ph) for op in BasicEvent.operations
                         for ph in phases_of(op)}      # wipe has no `failed`
    assert expected - pairs_seen_this_session == set()
```

The first is irreducible: `Event` must be a statically written alias because no type checker can
follow a computed union, so something has to assert the hand-written half matches the registry.

The second covers the gap the hook cannot: a class can be defined, registered and present in the
union, yet some phase of it never constructed — the taxonomy below then promises an event that
never fires. `MemoryNotifier` (§10) already subscribes to `*` as the test-suite consumer, so
collecting `(operation, phase)` pairs into a session-scoped fixture makes coverage a set
difference. Note it is now pairs, not names: with phase on its own axis, `check` existing does
not prove `check.failed` ever fires.

### Taxonomy

| Event | Level | Notes |
|---|---|---|
| `backup.started` | info | Arms the watchdog (§8). |
| `backup.succeeded` | info | Carries snapshot id. Heartbeat notifiers subscribe here. |
| `backup.failed` | error | Carries `scripts` for any failed `captain-hooks`. Requires §2.4. |
| `backup.slow` | warning | Watchdog; see §8. |
| `restore.started` / `.succeeded` / `.failed` | info/info/error | `restore` also destroys pg volumes (`tasks.py`), so failure here is high-severity in practice. |
| `check.succeeded` / `check.failed` | info/error | **The most valuable pair.** Silent repository corruption is the failure mode you otherwise discover during a restore. Needs a task first — see below. |
| `forget.succeeded` / `.failed` | info/error | Carries `snapshots_removed`; a policy that suddenly prunes 400 snapshots is a signal. |
| `wipe.started` / `.succeeded` | warning/warning | Destructive and irreversible, but user-initiated. Filterable like everything else. |

`restore.slow` and `check.slow` come free from the watchdog arming on any `Started`, at no extra
schema cost — which is the second dividend of the phase axis.

`configure` and `snapshots` emit nothing; they are interactive and read-only.
### Where events are emitted

At the **task layer** (`tasks.py`), not inside `Repository` — because `Repository` subclasses
are written by *plugin authors*, who must not have to remember to emit anything. Tasks are also
the operation boundary a human cares about.

**A context manager that yields the repository**, not a decorator:

```python
with repo_context(connection_choice, BackupEvent) as repo:
    repo.backup(c, verbose, target, message)
```

`__enter__` resolves via `cli_repo`, emits `Started`, and arms the watchdog. `__exit__` cancels
the watchdog and emits `Succeeded` or `Failed` — phase chosen from the exception or its absence,
`duration` from the elapsed time, `exit_code` from the exception. One construct covers
resolution, all four phases, and timing.

Why not a decorator: every task resolves its repository *inside* its own body — `repo =
cli_repo(connection_choice)` in `backup`, `cli_repo(connection_choice).restore(...)` inline in
`restore` — so a wrapper never sees the `Repository` and cannot fill `repo`/`repo_display`. It
could resolve one itself, but `cli_repo` prints `Use connection: …` and calls `repo.setup()` →
`check_env`, which *prompts interactively* on a missing variable, so resolving twice would print
twice and prompt twice. Injecting `repo` as a hidden parameter works (`__signature__` on the
wrapper keeps it off the CLI, verified against `ewok.Task.argspec`) but changes task signatures
and invoke semantics to buy nothing the context manager does not already give.

The second argument is the **operation class**, which is all the manager needs: phases are
universal (§6), so there is no family mapping, no name string, and no registry lookup —
`BackupEvent` plus a `Status` is a complete event.

**Two details this settles:**

- **Resolution failures are inside the block.** `cli_repo` raising `ValueError` on an invalid
  `--connection-choice` now happens after `__enter__` is entered, so it *can* be reported — but
  there is no `Repository` yet, so `repo_display` falls back to the raw choice string. Worth it:
  a mistyped connection name in a cron job is exactly the silent failure this feature exists to
  catch.
- **The choice argument is inconsistently named.** `backup`, `restore`, `snapshots`, `run` and
  `env` call it `connection_choice`; `forget`, `unlock`, `du` and `wipe` call it `connection`.
  The context manager takes the value rather than the name, so unlike the decorator it does not
  care — but `edwh restic.backup --connection-choice s3` beside `edwh restic.forget --connection
  s3` remains a wart the README documents, worth a separate follow-up.

`move` takes two repositories (`source` and `target`) and stays hand-written; nesting two
`repo_context` blocks would emit two operations for one user action.

Failed `captain-hooks` scripts are collected in `execute_files`
(`repositories/__init__.py`) — the only place per-script exit codes exist — and attached to the
terminal event as `scripts` rather than emitted separately (§6).

### `check` needs a task before it can emit

`Repository.check()` exists but **nothing calls it** — there is no `edwh restic.check`. So
`check.succeeded`/`check.failed`, which §6 calls the most valuable pair, have no emit site and
no way for a user to trigger them today.

The method is dead code, so its signature can be changed freely — and should be, because it
hardcodes `--read-data`, which re-downloads **every byte** in the repository. That is a
defensible default for a hand-run integrity check and a bad one for the cron job this feature
exists to serve, where it means paying full egress on every run.

```python
@task(aliases=("verify",))
def check(c, connection: str = None, read_data: bool = False, subset: str = ""):
    """Verify repository integrity. Structure only by default; --read-data reads everything,
    --subset=5% or --subset=1G reads a sample (restic picks a different one each run)."""
    with repo_context(connection, CheckEvent) as repo:
        repo.check(c, read_data=read_data, subset=subset)
```

Defaulting to structure-only makes the cheap check the one you get by accident, and
`--subset=5%` is the setting worth putting in a weekly cron: restic selects a different sample
per run, so repeated runs converge on full coverage without ever paying for it at once.

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
`AZURE_ACCOUNT_KEY` and friends into `os.environ` (`repositories/s3.py`, and every
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
- **No `env` and no `os.environ` passthrough**, in any form. There is also no free-form
  `extra` mapping to smuggle one through — §6's typed operation/phase classes mean every field
  is declared, so the allowlist is enforced by the type, not by a convention about what emit
  sites are supposed to put in a dict.

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
- `Notifier`, `Repository`, `Event`, every event variant and `CONTRACT_VERSION` are importable
  from **one stable module path** (`edwh_restic_plugin.plugins`), so plugins never reach into
  `.repositories` internals.
- The dispatcher **duck-types** `send` rather than requiring `isinstance`, so a notifier
  package can type against the ABC without a hard runtime import of it.

Bumping `CONTRACT_VERSION` is for changes that break a consumer at *runtime*: a removed or
renamed field, a changed `send` signature, a variant dropped from the union. Adding an event
name does **not** bump it — the union widens, existing members are untouched, and a `*`
subscriber keeps working. It will however receive names it has never heard of, so tolerating
that is a documented obligation of implementing `send`, and it is the one case where a
type-check can fail while the runtime contract holds (§6, Exhaustiveness).

## 8. Watchdog: detecting hanging backups

A backup that hangs fires neither `succeeded` nor `failed`. It is invisible to every
notifier in §6 — a third state, not a variant of failure.

**Mechanism:** at `backup.started`, arm a `threading.Timer`; cancel it on any terminal
event. On expiry, emit `backup.slow` with elapsed time and the currently running script.
The main thread is blocked in `c.run(..., pty=True)`, so a daemon timer thread is the
correct primitive — no async, no subprocess supervision.

**It does not kill the backup.** Killing restic mid-write risks leaving a stale repository
lock, which is why `edwh restic.unlock` exists (`tasks.py`); a watchdog that routinely
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
`determine_forget_policy` (`repositories/__init__.py`) tries `_short_name` first, the
first `edwh restic.forget` against an S3 repository writes `[restic.forget.s3] = <the default
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
| Event schema | One frozen dataclass per **operation**, carrying a `status` union of **phase** objects. Two axes composed rather than one chosen, so neither's fields become optional. No free-form `extra`, no separate script-failure event. Exhaustive matching via `assert_never` is available and opt-in. |
| Name/class sync | Class definitions are the source of truth. `name` is derived from `operation` + `status.phase`, so no flat name list exists; operations self-register in `__init_subclass__`, making duplicates an import-time error. Two tests cover the rest: the static `Event` union, and (operation, phase) emit coverage. |
| Plugin config access | Core resolves everything and passes `env` (parsed `.env`) plus `options` (resolved `[restic.notify.<name>]`) to `from_config`. Plugins never open `.toml`. |
| Notifier activation | Explicit — named in `[restic.notify] channels`. Unlike repositories, not env-presence. |
| Notifier failure | Caught, logged, timed out by the dispatcher, never fatal. A backup never fails because a channel is down. |
| Event filtering | Uniform. No event bypasses `min_level` or per-channel `events`. |
| Contract versioning | `CONTRACT_VERSION` integer; warn-and-skip on mismatch at discovery. Plus `py.typed`. |
| Multiple repos per run | Out of scope, nothing reserved in the schema. One repository per invocation, as `cli_repo` does today. |
| v1 scope | Repository entry-point discovery + narrowed abstract surface; event model + notifier registry + dispatch; watchdog for hanging backups. |
| Deferred | Heartbeat/dead-man's-switch (external notifier, needs no core work beyond `backup.succeeded`), `edwh restic.healthcheck` snapshot-age task, local single-file plugins, hard kill on timeout. |

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

Each step is independently shippable and leaves the tree green. Baseline at time of writing:
11 tests pass under Python 3.12.

0. **Prerequisites that are not design work:**
   - **An `edwh restic.check` task** (§6). Without it the `check.*` pair cannot fire and
     `test_every_variant_is_actually_emitted` fails by construction.
   - **Get the existing tooling green.** `pytest`, `ruff` and `ty` run before release, so the
     §6 invariants do have a gate — but this repository does not currently pass two of the
     three. Measured at the time of writing, on Python 3.12: **11 tests pass**, `ruff format`
     is clean, but `ruff check` reports **21 findings** and `ty` reports **83**.

     Most are stylistic (`SIM108`, `E501`, `ARG001` on unused fixtures; `ty`'s 25
     `invalid-parameter-default` are all `str = None` annotations that want `str | None`). Four
     are real and two sit directly in code this design touches:

     | Finding | Where | Assessment |
     |---|---|---|
     | `F821` undefined name `invoke` | `tasks.py`, in `restore` | `docker_inspect: invoke.Result` with no `import invoke`. Harmless at runtime — PEP 526 does not evaluate local variable annotations — but both tools flag it, and it is inside the function step 1 rewrites. |
     | `F841` unused local `x` | `tasks.py`, `check_abstract_methode` | Dead assignment in the task §2.3 shrinks anyway. |
     | `F522` unused `.format` argument | `hetzner.py`, `uri` | Passes `account_id=` to a template that never interpolates it. The URI is correct; the kwarg is a leftover. |
     | `E713` `not ... in` | `tasks.py` | Autofixable. |

     `ty`'s 19 `unresolved-import` are almost certainly missing stubs for `edwh`/`ewok`/`invoke`
     rather than defects, and should be triaged before anyone treats the count as a target.

     This is not blocking for steps 1–7, but it is worth knowing that "the gate is green" is not
     true here yet, so a new module arriving with its own findings will be hard to distinguish
     from the existing backlog.
1. ~~**Refactor exits into exceptions** (§2.4)~~ — **done.** `exceptions.py` defines
   `ResticError`, `NoScriptsFound`, `ResticScriptError`, `ResticConnectionError` and
   `ScriptFailure`; `get_scripts`, `execute_files` and `sftp.py` raise instead of exiting, and
   `@exits_on_restic_error` in `tasks.py` converts a `ResticError` back into its exit code at
   the one place that knows the process is ending. The `max(file_codes)` precedence bug is fixed
   — `ResticScriptError.exit_code` is now the worst script's real code rather than `True`.
   Exit codes are preserved: 255 for no scripts, worst-script code for script failures, 1
   otherwise.
2. **Discovery + registry generalisation** (§3) — `plugins.py`, entry points, scoped
   `discover()`, and the `registrations.get()` fix (§2.2).
3. **Narrow the abstract surface** (§2.3) — `UnsupportedOperation`, graceful degradation
   in `wipe`/`move`.
4. **Event model + contract** (§6, §7) — the operation classes, the `Status` union, the
   `__init_subclass__` registry,
   `Repository.display_name()`, `CONTRACT_VERSION`, `py.typed`, and the secret-leak tripwire
   test. No dispatch yet.
5. **Notifier registry + emit sites** (§5) — the `emit_*` context managers, activation from
   `[restic.notify] channels`, dispatcher-enforced timeout, contract check at discovery, plus
   `MemoryNotifier` in tests.
6. **Watchdog** (§8) — timer, escalation, per-target thresholds.
7. **Reference external package** — `edwh-restic-ntfy` in a separate repository, which is
   also the real proof the contract is usable from outside.

Step 0 is genuinely blocking for the *enforcement* half of this design, not for the features:
steps 1–7 can be written without CI, but the union-completeness and emit-coverage tests only
protect anything once something runs them on push.

## 12. Known issues outside this design

Both are pre-existing and independent of the plugin work; recorded here because they were
found while mapping the code, not proposed as part of it.

- **`restore` destroys before it verifies.** `tasks.py` stops the pg containers and
  removes their volumes *before* calling `restore`. If the restore then fails — bad snapshot
  id, unreachable repository, wrong password — the old data is already gone and the failure
  notification arrives too late to matter. Notification cannot fix this; the ordering can.
  Verifying the snapshot exists and is readable before destroying anything is a separate,
  small change, and worth filing on its own.
- **`get_or_copy_policy` third branch is unreachable.** See §9.1 for the derivation.
- ~~**Constructing a `Repository` can install a package.**~~ **Fixed.** `__init__` called
  `_require_restic()`, which on a `which restic` miss runs `require_sudo()` then
  `sudo apt install -y restic` and `sudo restic self-update` — so object construction could
  prompt for a password and mutate the host, on every code path that touches a repository
  whether or not it needed restic. That included `edwh restic.env`, which only diffs
  `os.environ`, and `move`, which paid it twice.

  Now opt-in: `cli_repo(..., require_restic=True)`, which only `configure` passes, since
  provisioning is its job. `run` already had `pre=[require_restic]` and is unaffected.
  Implemented on `cli_repo` rather than as a constructor parameter because every subclass
  defines `__init__(self)` without forwarding, so `repoclass(require_restic=True)` would raise
  `TypeError`.

  **Behaviour change worth knowing:** a `backup` on a machine where restic is missing or has
  been removed now fails instead of silently installing it. After step 1 that failure is a
  proper exception rather than a bare exit, so it is reportable — which is the trade: explicit
  failure you can be notified about, instead of an implicit `sudo apt install` nobody asked for.
