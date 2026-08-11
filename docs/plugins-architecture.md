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
(`repositories/__init__.py:30`, `get_scripts`). The new mechanism is therefore called
**events** and **notifiers**, never "hooks". Reusing "hook" would make every future bug
report ambiguous.

## 2. Current state: what already works and what blocks reuse

`RepositoryRegistrations` (`repositories/__init__.py:477`) is already most of a plugin
registry — priority heap, aliases, lazy discovery, `@register()` decorator. Four defects
block third-party use.

### 2.1 Discovery is hardcoded to the package directory

`_find_items()` (`repositories/__init__.py:525`) globs `Path(__file__).parent/"*.py"`. An
installed external package can never be found. Fix in §3.

### 2.2 `registrations.get()` does not trigger discovery

```python
def get(self, name: str) -> typing.Type[Repository] | None:
    return self._aliases.get(name)   # repositories/__init__.py:506-507
```

`_aliases` is only populated by `push()`, which only runs from `_find_items()`, which is
only reached via the `queue` property. `get()` bypasses `queue` entirely. This works today
purely because `cli_repo` happens to call `to_ordered_dict()` first (`tasks.py:32`) before
`registrations.get()` (`tasks.py:44`). Any other caller — including a future
`notify`/`healthcheck` task — gets `None` from a correctly registered repository.

**Fix:** `get()` must touch `self.queue` first, same as `to_ordered_dict()`.

### 2.3 The abstract surface is twice as wide as it needs to be

`Repository` declares six abstract members: `setup`, `prepare_for_restic`, `uri`, `wipe`,
`bucket`, `prepare_rclone_config`. Only the first three are needed to perform a backup.
The other three exist solely for the `wipe` (`tasks.py:317`) and `move` (`tasks.py:330`)
tasks.

Two pieces of evidence that this already hurts internally:

- The `check_abstract_methode` task (`tasks.py:401`) exists only to find subclasses that
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
files = self.get_scripts(target, verb)   # calls sys.exit(255) on no match — line 242
...
if worst_status_code := max(file_codes) > 0:
    exit(worst_status_code)              # line 322
```

There is a third, in a repository implementation: `sftp.py:69` calls `exit(1)` when its
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
   (`[project.entry-points."edwh.tasks"]`, `pyproject.toml:43`), so being a
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
  when `--connection-choice` is omitted (`tasks.py:34-40`). A plugin that names its
  password variable differently is silently unselectable.
- `_short_name` and `_aliases` feed forget-policy lookup (`determine_forget_policy`,
  `repositories/__init__.py:423`), so `[restic.forget.azure]` works for free.

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
mirrors `Repository.env_config` (`repositories/__init__.py:150`), and by the time a notifier
runs, `os.environ` has been loaded with restic's credentials by `prepare_for_restic`, so the
convenient path should not be the one that walks past them.

`mycorp_restic_webhook/__init__.py`, entire:

```python
from typing import Any, Mapping, Self

import httpx
from edwh_restic_plugin.plugins import (
    CONTRACT_VERSION, BackupFailed, CheckFailed, Event, Notifier, register_notifier,
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
            case BackupFailed(exit_code=code, logs=logs):
                return f"{event.repo_display} backup failed (exit {code})\n{logs or ''}"
            case CheckFailed():
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

A **tagged union of frozen dataclasses**, discriminated on `name`. Not one wide dataclass
with a `str` name and an untyped `extra` bag: that pushes every "is this field set for this
event?" question to runtime, which is exactly the question a plugin author needs answered
while writing.

```python
Level = Literal["info", "warning", "error"]

@dataclass(frozen=True, kw_only=True)
class EventBase:
    ts: datetime
    level: Level
    repo: str                     # short_name, e.g. "s3"
    repo_display: str             # Repository.display_name() — §7.1, never the raw uri
    host: str                     # RESTICHOSTNAME or platform hostname
    project: str                  # cwd name, or [restic.notify] project

@dataclass(frozen=True, kw_only=True)
class BackupStarted(EventBase):
    name: Literal["backup.started"] = "backup.started"
    target: str | None

@dataclass(frozen=True, kw_only=True)
class BackupSucceeded(EventBase):
    name: Literal["backup.succeeded"] = "backup.succeeded"
    target: str | None
    duration: float
    snapshot: str | None
    message: str | None           # the snapshot message

@dataclass(frozen=True, kw_only=True)
class BackupFailed(EventBase):
    name: Literal["backup.failed"] = "backup.failed"
    target: str | None
    duration: float
    exit_code: int
    logs: str | None              # full stdout/stderr — §7.1

@dataclass(frozen=True, kw_only=True)
class BackupScriptFailed(EventBase):
    name: Literal["backup.script.failed"] = "backup.script.failed"
    script: str                   # the captain-hooks file that failed
    exit_code: int
    logs: str | None

@dataclass(frozen=True, kw_only=True)
class BackupSlow(EventBase):
    name: Literal["backup.slow"] = "backup.slow"
    target: str | None
    elapsed: float                # not `duration`: the backup has not finished
    threshold: float              # which warn_after step tripped
    script: str | None            # currently-running script, if known

# ... restore.*, check.*, forget.*, wipe.* likewise

Event = (
    BackupStarted | BackupSucceeded | BackupFailed | BackupScriptFailed | BackupSlow
    | RestoreStarted | RestoreSucceeded | RestoreFailed
    | CheckSucceeded | CheckFailed
    | ForgetSucceeded | ForgetFailed
    | WipeStarted | WipeSucceeded
)
```

There is deliberately **no flat `EventName = Literal[...]` alias.** Each variant already pins
its own name, so a second list is a second thing to forget to update, and it would buy
nothing: config `events` entries are patterns (`backup.*`), not names, so they cannot be typed
by it anyway.

Three things this buys that the wide-dataclass version could not:

- **`exit_code` is `int`, not `int | None`.** It exists on failure events and nowhere else,
  so the type states that instead of documenting it. Same for `duration`, `snapshot`,
  `script`.
- **`extra` is gone.** Event-specific data has a declared home — `BackupSlow.threshold`,
  `BackupScriptFailed.script` — rather than a `Mapping[str, str]` that every emit site
  populates by convention and every consumer reads by guesswork.
- **`BackupSlow.elapsed` is not `duration`.** The wide version forced both concepts through
  one field, quietly meaning "final runtime" on some events and "so far" on others. Splitting
  the classes made the naming bug visible.

### Keeping name, class, union and emit site in sync

The class definitions are the single source of truth. Everything else is derived or
CI-enforced, because there are four places the same fact could drift apart: the `Literal` on
the class, the `Event` union alias, the runtime lookup table, and whether anything ever
actually constructs the variant.

**Registered at definition, via `__init_subclass__`.** Registration happens as each class is
created, so there is no traversal to get wrong and nesting depth is irrelevant:

```python
@dataclass(frozen=True, kw_only=True)
class EventBase:
    variants: ClassVar[dict[str, type["EventBase"]]] = {}

    ts: datetime
    level: Level
    # ... common fields per §6

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__:
            return                       # intermediate base, or a subclass reusing a name
        if (declared := get_args(cls.__annotations__["name"])) != (cls.name,):
            raise TypeError(f"{cls.__name__}: annotation {declared} != default {cls.name!r}")
        if clash := EventBase.variants.get(cls.name):
            raise TypeError(f"{cls.__name__} reuses {cls.name!r} from {clash.__name__}")
        EventBase.variants[cls.name] = cls
```

Two ordering facts make this work, both worth a comment in the source because they are easy to
break:

- **`__init_subclass__` runs before `@dataclass` is applied to the subclass.** `cls.name` and
  `cls.__annotations__` come from the class body, so both are already populated — but
  `dataclasses.fields(cls)` does *not* raise here, which is the trap. `__dataclass_fields__` is
  inherited, so it silently returns only the **base's** fields: `['ts']`, not
  `['ts', 'name', 'exit_code']`. Registration must therefore depend only on `__dict__` and
  `__annotations__`, and any future field-level validation belongs in a test or a
  `__post_init__`, not here.
- **This module must not use `from __future__ import annotations`.** With postponed
  evaluation, `cls.__annotations__["name"]` is the *string* `'Literal["backup.failed"]'` and
  `get_args` returns `()`. Nothing else in `src/` uses it today, and 3.12 leaves it opt-in, so
  this is a constraint to document rather than defend against.

Guarding on `"name" in cls.__dict__` rather than `hasattr` is what lets intermediate bases and
plugin subclasses exist: a class that does not declare its own name is not a new variant, and
is correctly ignored instead of re-registering an inherited one.

**Two invariants become import-time errors** rather than test failures — a name annotation
disagreeing with its own default, and two variants sharing a name. Both are definition-time
properties, so they should fail when you write the class, not when someone runs pytest.

**What still needs a test.** Only what the hook structurally cannot see:

```python
def test_union_matches_the_classes():
    """Orphan class, or junk in the union."""
    static, runtime = set(typing.get_args(Event)), set(EventBase.variants.values())
    assert static == runtime, {
        "defined but missing from Event": runtime - static,
        "in Event but not an event class": static - runtime,
    }

def test_every_variant_is_actually_emitted(names_seen_this_session):
    """A variant nobody constructs is dead weight that still ships in the contract."""
    assert set(EventBase.variants) - names_seen_this_session == set()
```

The first is unavoidable: `Event` must be a statically written alias because no type checker can
follow a computed union, so something has to assert the hand-written half matches the registry.

The second closes the gap none of the above can see. A variant can be defined, registered, and
present in the union, yet never constructed by any emit site — the taxonomy table then promises
an event that never fires. `MemoryNotifier` (§10) is already the test-suite consumer subscribing
to `*`, so collecting `event.name` into a session-scoped fixture makes emit-site coverage a set
difference. It is also what keeps `@emits` honest: a typo there yields a silently-never-emitted
variant rather than an error.

### Exhaustiveness

`match` narrows on class patterns, and `assert_never` turns a missing arm into a type error:

```python
def send(self, event: Event) -> None:
    match event:
        case BackupFailed() | CheckFailed() | RestoreFailed() | ForgetFailed():
            self.page_someone(event)
        case BackupSlow(elapsed=secs):
            self.warn(f"still running after {secs / 60:.0f}m")
        case _:
            assert_never(event)   # mypy errors here if a variant is unhandled
```

**This is opt-in, and it interacts with versioning (§7.2).** Adding an event name is a minor
bump — it widens the union without changing existing variants, so a notifier keeps *running*
unchanged. But a notifier that ends in `assert_never` will *fail type-checking* against the
new version until it adds an arm. That is the intended trade and authors should choose
knowingly:

| Final arm | On a new event at runtime | On a new event in CI |
|---|---|---|
| `case _: return` | ignored silently | passes |
| `case _: self.generic(event)` | handled generically | passes |
| `case _: assert_never(event)` | unreachable, so no effect | **fails, with the missing variant named** |

Notifiers wanting neither can ignore the union entirely and use only `EventBase` fields plus
`event.name` and `event.level` — which is what §5.1's `send` does.

### Taxonomy

| Event | Level | Notes |
|---|---|---|
| `backup.started` | info | Arms the watchdog (§8). |
| `backup.script.failed` | error | Per `captain-hooks` script, with its real exit code. Requires §2.4. |
| `backup.succeeded` | info | Carries snapshot ids. Heartbeat notifiers subscribe here. |
| `backup.failed` | error | Requires §2.4. |
| `backup.slow` | warning | Watchdog; see §8. |
| `restore.started` / `.succeeded` / `.failed` | info/info/error | `restore` also destroys pg volumes (`tasks.py:140-153`), so failure here is high-severity in practice. |
| `check.succeeded` / `check.failed` | info/error | **The most valuable pair.** Silent repository corruption is the failure mode you otherwise discover during a restore. |
| `forget.succeeded` / `.failed` | info/error | Include snapshots removed; a policy that suddenly prunes 400 snapshots is a signal. |
| `wipe.started` / `.succeeded` | warning/warning | Destructive and irreversible, but user-initiated. Filterable like everything else. |

`configure` and `snapshots` emit nothing; they are interactive and read-only.

### Where events are emitted

At the **task layer** (`tasks.py`), not inside `Repository` — because `Repository` subclasses
are written by *plugin authors*, who must not have to remember to emit anything. Tasks are also
the operation boundary a human cares about.

**Not a decorator, though.** An earlier draft proposed `@emits("backup")` above `@task`. That
does not work: every task resolves its repository *inside* its own body
(`repo = cli_repo(connection_choice)`, `tasks.py:112`; `cli_repo(connection_choice).restore(...)`,
`tasks.py:154`), so a decorator wrapping the function never sees the `Repository` instance and
cannot fill `repo` or `repo_display`. Nor can it resolve one itself: `cli_repo` prints
`Use connection: …` and calls `repo.setup()`, which calls `check_env` — an *interactive prompt*
on a missing variable. Resolving twice would double-prompt.

A context manager inside the body has everything the decorator lacked, at the cost of one line:

```python
repo = cli_repo(connection_choice)
with emit_backup(repo, target=target) as run:      # backup.started here
    repo.backup(c, verbose, target, message)
    run.snapshot = ...                             # carried into backup.succeeded
# __exit__ emits succeeded or failed, with duration, from the exception (or its absence)
```

`__enter__`/`__exit__` yield the whole terminal-state triple plus timing anyway, so nothing is
lost but the appearance of zero-cost instrumentation. It also arms and disarms the watchdog
(§8) at exactly the right boundaries.

Known consequence: a failure *before* the `with` — an invalid `--connection-choice` raising
`ValueError` from `cli_repo` (`tasks.py:44-46`), or an interactive `check_env` prompt the user
abandons — emits nothing. That is acceptable: no backup had begun, and both cases are
synchronous and interactive, so the operator sees the traceback. It would not be acceptable for
cron'd operation, which is why the watchdog and an external heartbeat (§8) are the complement.

Fine-grained `backup.script.failed` is emitted from `execute_files`
(`repositories/__init__.py:246`), which is the only place per-script exit codes exist.

**`check` has no task to hook.** `Repository.check()` exists
(`repositories/__init__.py:348`) but **nothing calls it** — there is no `edwh restic.check`.
So `check.succeeded`/`check.failed`, which §6 calls the most valuable pair, currently have no
emit site and no way for a user to trigger them. Implementing the taxonomy therefore requires
adding the task; see §11 step 0.

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
- **No `env` and no `os.environ` passthrough**, in any form. There is also no free-form
  `extra` mapping to smuggle one through — §6's tagged union means every field on every event
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
name does **not** bump it — the union widens, existing variants are untouched, and a `*`
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
lock, which is why `edwh restic.unlock` exists (`tasks.py:269`); a watchdog that routinely
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
`determine_forget_policy` (`repositories/__init__.py:423`) tries `_short_name` first, the
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
| Event schema | Tagged union of frozen dataclasses discriminated on a `Literal` `name`; no free-form `extra`. Exhaustive matching via `assert_never` is available and opt-in. |
| Name/class sync | Class definitions are the source of truth. No flat `EventName` alias; variants self-register in `__init_subclass__`, which makes annotation/default disagreement and duplicate names import-time errors. Two tests cover the rest: the static `Event` union, and emit-site coverage. |
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

0. **Prerequisites that are not design work**, and were missing when this was written:
   - **CI.** There is no `.github/` at all, so nothing runs the suite. Every "CI-enforced"
     invariant in §6 is aspirational until a workflow exists. Add one pinned to **3.12** —
     `pyproject.toml` declares `requires-python = ">=3.12"` and `ewok>=0.4.8` publishes no
     wheel for 3.11, so the dependency set is uninstallable below 3.12.
   - **An `edwh restic.check` task.** `Repository.check()` exists
     (`repositories/__init__.py:348`) with no caller. Note it runs `check --read-data`, which
     re-downloads the entire repository — acceptable interactively, expensive as a cron job
     against S3 egress, so the task wants a `--read-data-subset` option rather than inheriting
     that default blindly.
1. **Refactor exits into exceptions** (§2.4) — `ResticError`, `NoScriptsFound`,
   `ResticScriptError`; `get_scripts`, `execute_files` and `sftp.py` raise, `tasks.py` catches
   at the top level and sets the process exit code there. Fix the `max(file_codes)` precedence
   bug. *No new features; changes observable exit codes, so it goes first and alone.*
2. **Discovery + registry generalisation** (§3) — `plugins.py`, entry points, scoped
   `discover()`, and the `registrations.get()` fix (§2.2).
3. **Narrow the abstract surface** (§2.3) — `UnsupportedOperation`, graceful degradation
   in `wipe`/`move`.
4. **Event model + contract** (§6, §7) — the variants and their `__init_subclass__` registry,
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

- **`restore` destroys before it verifies.** `tasks.py:140-154` stops the pg containers and
  removes their volumes *before* calling `restore`. If the restore then fails — bad snapshot
  id, unreachable repository, wrong password — the old data is already gone and the failure
  notification arrives too late to matter. Notification cannot fix this; the ordering can.
  Verifying the snapshot exists and is readable before destroying anything is a separate,
  small change, and worth filing on its own.
- **`get_or_copy_policy` third branch is unreachable.** See §9.1 for the derivation.
