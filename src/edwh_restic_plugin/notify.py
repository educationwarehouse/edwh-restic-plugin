"""
Notifier registry, routing and dispatch.

A notifier is trusted like any other dependency: it runs in-process and can read os.environ
directly. What is guaranteed is that it cannot affect the backup.
"""

import abc
import contextlib
import dataclasses as dc
import datetime as dt
import os
import platform
import socket
import threading
import time
import typing as t
from pathlib import Path

from termcolor import cprint

from .config import read_config
from .env import DOTENV, read_dotenv
from .events import (
    ALL_PHASES,
    BasicEvent,
    Failed,
    Level,
    Phase,
    Slow,
    Started,
    Status,
    Succeeded,
    level_for,
    matches,
)
from .exceptions import ResticError, ResticScriptError, ScriptFailure
from .registry import CONTRACT_VERSION, MIN_SUPPORTED_CONTRACT, Registration, Registry
from .watchdog import Watchdog

if t.TYPE_CHECKING:
    from .repositories import Repository

#: How long a single send may take before it is abandoned, enforced by the dispatcher rather than
#: left to the notifier's transport.
DEFAULT_TIMEOUT = 5.0


class Notifier(abc.ABC):
    """Base class for notification channels.

    Subclass, decorate with @register_notifier("name"), and implement send(). A notifier runs only
    if [restic.notify] channels names it, so installing a package changes nothing on its own.
    """

    # set via @register_notifier:
    _short_name: str
    _aliases: tuple[str, ...]
    _priority: int

    #: Which contract version this notifier was written against. Declare it as a literal: reading
    #: CONTRACT_VERSION would claim compatibility with whatever it happens to be installed beside,
    #: which is exactly what the check exists to catch.
    contract: int = 1

    #: Default routing, overridden by [restic.notify.<name>] events.
    subscribes: tuple[str, ...] = ("*",)

    @classmethod
    def from_config(
        cls,
        env: t.Mapping[str, str],  # noqa: ARG003 (part of the contract; overrides use it)
        options: t.Mapping[str, t.Any],  # noqa: ARG003
    ) -> "t.Self | None":
        """Build an instance, or return None to stay inactive.

        `env` holds only the `.env` keys prefixed with this notifier's name, e.g. NTFY_TOKEN for
        `ntfy`, with the prefix kept. `options` is the resolved `[restic.notify.<name>]` table. Both
        are passed in so a notifier never has to locate or parse configuration itself, and so a
        channel is not handed every other channel's credentials, nor restic's.

        Return None when the channel is named but not provisioned yet, e.g. its token is not in
        `.env`. That is reported and skipped, so a half-provisioned machine still runs its backups.
        """
        return cls()

    def format(self, event: BasicEvent) -> str:
        """Human-readable one-liner. Override for channel-specific payloads."""
        match event.status:
            case Failed(exit_code=code):
                return f"{event.name}: {event.repo_display} (exit {code})"
            case Succeeded(duration=seconds):
                return f"{event.name}: {event.repo_display} in {seconds:.0f}s"
            case Slow(elapsed=seconds):
                return f"{event.name}: {event.repo_display} still running after {seconds / 60:.0f}m"
            case _:
                return f"{event.name}: {event.repo_display}"

    @abc.abstractmethod
    def send(self, event: BasicEvent) -> None:
        """Deliver the event. May raise; the dispatcher catches, logs and continues.

        May receive an event name it has never heard of, since adding an operation is an additive
        contract change: a "*" subscriber must tolerate unknown names.
        """


class NotifierRegistrations(Registry[Notifier]):
    # No in-package discovery: core ships no notifiers, only the interface.
    in_package = None
    config_key = "notifiers"


notifiers = NotifierRegistrations()


def register_notifier(
    short_name: str | None = None,
    aliases: tuple[str, ...] = (),
    priority: int = -1,
) -> t.Callable[[type[Notifier]], type[Notifier]]:
    if isinstance(short_name, type):
        raise SyntaxError("Please call @register_notifier() with parentheses!")

    def wraps(cls: type[Notifier]) -> type[Notifier]:
        if not (isinstance(cls, type) and issubclass(cls, Notifier)):
            raise TypeError(f"Decorated class {cls} must be a subclass of Notifier!")

        from .helpers import camel_to_snake

        name = short_name or camel_to_snake(cls.__name__).removesuffix("_notifier")

        settings: Registration = {"short_name": name, "aliases": aliases, "priority": priority}
        notifiers.push(cls, settings)
        cls._short_name = name
        cls._aliases = aliases
        cls._priority = priority
        return cls

    return wraps


class Channel(t.NamedTuple):
    """An active notifier plus the routing it was configured with."""

    notifier: Notifier
    events: tuple[str, ...]
    min_level: Level

    def wants(self, event: BasicEvent) -> bool:
        if LEVELS.index(event.level) < LEVELS.index(self.min_level):
            return False

        return any(matches(pattern, event.name) for pattern in self.events)


#: Derived from the Literal, in ascending severity, so there is no second list of level names.
LEVELS: tuple[Level, ...] = t.get_args(Level)


def env_for(name: str, env: t.Mapping[str, str]) -> dict[str, str]:
    """The `.env` keys belonging to one notifier: those prefixed with its name.

    A notifier can still read os.environ itself; this is not a sandbox. It does mean the obvious
    path hands `ntfy` only NTFY_*, rather than every other channel's tokens and restic's password.
    """
    prefix = f"{name.upper()}_"
    return {key: value for key, value in env.items() if key.upper().startswith(prefix)}


def build_channels(
    env: t.Mapping[str, str] | None = None,
    config: t.Mapping[str, t.Any] | None = None,
) -> list[Channel]:
    """Resolve [restic.notify] into the channels that should receive events."""
    # Remember whether the caller supplied config: if they did, per-channel options come from it
    # too, so a test can pass one dict instead of writing two toml files.
    config_supplied = config is not None
    config = read_config("notify") if config is None else config
    env = read_dotenv(DOTENV) if env is None else env

    names = config.get("channels") or []
    if isinstance(names, str):
        names = [names]

    global_min: Level = config.get("min_level", "info")
    if global_min not in LEVELS:
        cprint(f"warning: [restic.notify] min_level '{global_min}' is not one of {LEVELS}; using 'info'", "yellow")
        global_min = "info"

    channels = []
    for name in names:
        if not (cls := notifiers.get(name)):
            cprint(f"warning: [restic.notify] channels lists '{name}', which is not installed", "yellow")
            continue

        if not MIN_SUPPORTED_CONTRACT <= cls.contract <= CONTRACT_VERSION:
            # Checked here, not mid-backup: an AttributeError at 04:00 in a cron job is a bad way
            # to learn a plugin is stale.
            cprint(
                f"warning: notifier '{name}' declares contract {cls.contract}, which is outside the "
                f"supported range {MIN_SUPPORTED_CONTRACT}-{CONTRACT_VERSION}; skipping it",
                "yellow",
            )
            continue

        options = dict(config.get(name) or {}) if config_supplied else read_config("notify", name)
        try:
            instance = cls.from_config(env_for(name, env), options)
        except Exception as e:
            cprint(f"warning: notifier '{name}' failed to configure and was skipped: {e!r}", "yellow")
            continue

        if instance is None:
            cprint(f"note: notifier '{name}' is named but not configured yet; skipping it", "yellow")
            continue

        events = options.get("events") or instance.subscribes
        if isinstance(events, str):
            events = [events]

        min_level = options.get("min_level", global_min)
        if min_level not in LEVELS:
            min_level = global_min

        channels.append(Channel(instance, tuple(events), min_level))

    return channels


class Dispatcher:
    """Sends events to channels, sequentially, and never lets one affect the caller."""

    def __init__(self, channels: t.Sequence[Channel] | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._channels = list(channels) if channels is not None else None
        self.timeout = timeout
        # The watchdog dispatches from a timer thread while the main thread may be dispatching a
        # terminal event; without the lock you get interleaved output and re-entrant notifier state.
        self._lock = threading.Lock()

    @property
    def channels(self) -> list[Channel]:
        if self._channels is None:
            self._channels = build_channels()
        return self._channels

    def dispatch(self, event: BasicEvent) -> None:
        with self._lock:
            for channel in self.channels:
                if channel.wants(event):
                    self.send_to(channel, event)

    def send_to(self, channel: Channel, event: BasicEvent) -> None:
        """Deliver to one channel regardless of its routing, with the same timeout and containment.

        Public so `restic.notify-test` can bypass filtering deliberately; dispatch() applies routing
        first and then comes here.
        """
        name = channel.notifier._short_name
        error: list[BaseException] = []

        def target() -> None:
            try:
                channel.notifier.send(event)
            except BaseException as e:  # a notifier must never reach the caller
                error.append(e)

        # A daemon thread rather than signal.alarm, since this may already be running on the
        # watchdog's timer thread and signals only work on the main thread.
        worker = threading.Thread(target=target, daemon=True, name=f"notify-{name}")
        worker.start()
        worker.join(self.timeout)

        if worker.is_alive():
            cprint(f"warning: notifier '{name}' timed out after {self.timeout}s and was abandoned", "yellow")
        elif error:
            cprint(f"warning: notifier '{name}' raised {error[0]!r}", "yellow")


#: Shared by every emitter that is not given one, so the watchdog and the main thread contend for a
#: single lock and resolve channels once.
default_dispatcher = Dispatcher()


def _hostname() -> str:
    return os.environ.get("RESTICHOSTNAME") or socket.gethostname() or platform.node()


def _project() -> str:
    """Which deployment an event came from, so several of them can share one channel.

    `host` already says which machine; this says which project on it. Set `project` under
    `[restic.notify]` when the directory name is not distinctive enough to read in a notification.
    """
    return read_config("notify").get("project") or Path.cwd().name


class Emitter:
    """Tracks one operation and emits its lifecycle events. Created by repo_context."""

    def __init__(
        self,
        event_class: type[BasicEvent],
        repo: "Repository | None",
        repo_name: str,
        fields: dict[str, t.Any],
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.event_class = event_class
        self.repo = repo
        self.repo_name = repo_name
        self.fields = fields
        self.dispatcher = dispatcher or default_dispatcher
        self.started_at: float | None = None

    def build(self, status: Status) -> BasicEvent:
        return self.event_class(
            status=status,
            ts=dt.datetime.now(),
            level=level_for(status.phase),
            repo=self.repo_name,
            # Falls back to the choice string when the repository could not be resolved, since
            # there is no Repository yet to ask and the failure is still worth reporting.
            repo_display=self.repo.display_name if self.repo else self.repo_name,
            host=_hostname(),
            project=_project(),
            **self.fields,
        )

    def emit(self, status: Status) -> None:
        self.dispatcher.dispatch(self.build(status))

    def update(self, **fields: t.Any) -> None:
        """Add detail discovered while the operation ran, e.g. a snapshot id."""
        self.fields.update(fields)


class Operation:
    """One operation's lifecycle: resolve a repository, report, and watch for a hang.

    Collaborators are constructor arguments rather than module lookups, so a caller (or a test) can
    supply its own resolver, dispatcher or thresholds without patching anything. `resolve` is
    required precisely so this module does not have to know how a repository is found.
    """

    def __init__(
        self,
        connection_choice: str | None,
        event_class: type[BasicEvent],
        resolve: t.Callable[..., "Repository"],
        require_restic: bool = False,
        dispatcher: Dispatcher | None = None,
        thresholds: t.Sequence[float] | None = None,
        **fields: t.Any,
    ) -> None:
        self.connection_choice = connection_choice
        self.require_restic = require_restic
        self.resolve = resolve
        self.thresholds = thresholds
        self.fields = dict(fields)
        self.emitter = Emitter(event_class, None, connection_choice or "default", self.fields, dispatcher)

    @contextlib.contextmanager
    def run(self) -> t.Iterator["Repository"]:
        started = time.monotonic()

        def elapsed() -> float:
            return time.monotonic() - started

        try:
            repo = self.resolve(self.connection_choice, require_restic=self.require_restic)
        except Exception as e:
            # No Repository exists yet, so the event falls back to the choice string. Reporting this
            # is the point: a mistyped --connection-choice would otherwise kill a cron job silently.
            self.emitter.emit(Failed(duration=elapsed(), exit_code=_exit_code_of(e), logs=str(e)))
            raise

        self.emitter.repo = repo
        self.emitter.repo_name = repo._short_name
        self.emitter.emit(Started())

        watchdog = Watchdog(self.emitter, target=self.fields.get("target"), thresholds=self.thresholds)
        watchdog.arm()
        try:
            yield repo
        except Exception as e:
            self.emitter.emit(Failed(duration=elapsed(), exit_code=_exit_code_of(e), logs=_logs_of(e)))
            raise
        else:
            self.emitter.emit(Succeeded(duration=elapsed()))
        finally:
            watchdog.disarm()


def _exit_code_of(error: BaseException) -> int:
    if isinstance(error, ResticError):
        return error.exit_code

    return 1


def _logs_of(error: BaseException) -> str:
    """Full stdout/stderr where restic gave us any, else the exception text."""
    if isinstance(error, ResticScriptError):
        detail = "\n".join(f"{f.script} exited {f.exit_code}" for f in error.failures)
        return f"{error}\n{detail}"

    if (result := getattr(error, "result", None)) is not None:
        parts = [getattr(result, "stdout", "") or "", getattr(result, "stderr", "") or ""]
        if joined := "\n".join(p for p in parts if p):
            return joined

    return str(error)


#: Put in the fields of a synthetic event so a recipient can tell a drill from the real thing. A
#: test that looks identical to a genuine alert is worse than no test at all.
TEST_MARKER = "NOTIFY-TEST"

#: Plausible values for a synthetic event, filtered per operation to the fields it actually has, so
#: adding an operation does not need a change here.
_SAMPLE_FIELDS: dict[str, t.Any] = {
    "target": "files",
    "snapshot": "0000000000000000000000000000000000000000000000000000000000000000",
    "message": f"{TEST_MARKER}: synthetic event, nothing actually happened",
    "scripts": (ScriptFailure(script=f"backup_files_{TEST_MARKER.lower()}.sh", exit_code=42),),
    "policy": "--keep-last 7 --prune",
    "snapshots_removed": 3,
    "read_data": False,
    "subset": "",
}


def sample_status(phase: Phase) -> Status:
    """A status object for any phase, with values that read as obviously synthetic."""
    match phase:
        case "started":
            return Started()
        case "succeeded":
            return Succeeded(duration=42.0)
        case "failed":
            return Failed(duration=42.0, exit_code=42, logs=f"{TEST_MARKER}: no restic was harmed")
        case "slow":
            return Slow(elapsed=4200.0, threshold=1800.0)
        case _:
            t.assert_never(phase)


def build_test_event(name: str, repo_display: str | None = None) -> BasicEvent:
    """Build a synthetic `<operation>.<phase>` event, marked so it cannot pass for a real one.

    Raises ValueError on an unknown operation or phase, listing what is available, since a typo here
    would otherwise look like a plugin that silently ignores you.
    """
    operation, _, phase = name.partition(".")
    if not (event_class := BasicEvent.operations.get(operation)):
        known = ", ".join(sorted(BasicEvent.operations))
        raise ValueError(f"unknown operation {operation!r}; expected one of {known}")

    if phase not in ALL_PHASES:
        raise ValueError(f"unknown phase {phase!r}; expected one of {', '.join(ALL_PHASES)}")

    accepted = {f.name for f in dc.fields(event_class)}
    fields = {key: value for key, value in _SAMPLE_FIELDS.items() if key in accepted}

    return event_class(
        status=sample_status(phase),
        ts=dt.datetime.now(),
        level=level_for(phase),
        repo=repo_display or TEST_MARKER.lower(),
        repo_display=f"{repo_display or 'no repository resolved'} [{TEST_MARKER}]",
        host=_hostname(),
        project=_project(),
        **fields,
    )


def event_names() -> list[str]:
    """Every `<operation>.<phase>` this build can emit."""
    return [f"{operation}.{phase}" for operation in sorted(BasicEvent.operations) for phase in ALL_PHASES]


__all__ = [
    "TEST_MARKER",
    "Channel",
    "Dispatcher",
    "Emitter",
    "Notifier",
    "NotifierRegistrations",
    "Operation",
    "build_channels",
    "default_dispatcher",
    "env_for",
    "notifiers",
    "register_notifier",
]
