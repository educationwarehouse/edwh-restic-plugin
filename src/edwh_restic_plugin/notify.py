"""
Notifier registry, routing and dispatch.

A notifier is trusted exactly like any other dependency: it runs in-process and can read
os.environ directly. Nothing here pretends otherwise. What is guaranteed is that a notifier
cannot affect the backup -- exceptions are caught, sends are timed out, and the process exit code
reflects the backup rather than the telemetry about it.
"""

import datetime
import os
import platform
import socket
import threading
import typing
from pathlib import Path

from termcolor import cprint

from .config import read_config
from .env import DOTENV, read_dotenv
from .events import BasicEvent, Failed, Level, Slow, Status, Succeeded, level_for, matches
from .registry import CONTRACT_VERSION, Registration, Registry

if typing.TYPE_CHECKING:
    from .repositories import Repository

#: How long a single send may take before it is abandoned. Enforced here rather than delegated to
#: the notifier's transport: a plugin author who forgets timeout= on a requests.post would
#: otherwise hang the backup indefinitely, and the whole point is that they cannot.
DEFAULT_TIMEOUT = 5.0


class Notifier:
    """Base class for notification channels.

    Subclass, decorate with @register_notifier("name"), and implement send(). Activation is
    explicit: a notifier runs only if [restic.notify] channels names it, so installing a package
    changes nothing until it is wired up.
    """

    # set via @register_notifier:
    _short_name: str
    _aliases: tuple[str, ...]
    _priority: int

    #: Which contract this notifier was written against. A mismatch is warned about and the
    #: notifier skipped, at discovery time rather than mid-backup.
    contract: int = CONTRACT_VERSION

    #: Default routing, overridden by [restic.notify.<name>] events.
    subscribes: tuple[str, ...] = ("*",)

    @classmethod
    def from_config(
        cls,
        env: typing.Mapping[str, str],  # noqa: ARG003 -- part of the contract; overrides use it
        options: typing.Mapping[str, typing.Any],  # noqa: ARG003
    ) -> "typing.Self | None":
        """Build an instance, or return None to stay inactive.

        `env` is the parsed .env and `options` the resolved [restic.notify.<name>] table, so a
        notifier never opens .toml itself. env is passed rather than read from os.environ because
        by the time a notifier runs, os.environ holds restic's credentials -- the convenient path
        should not be the one that walks past them.

        Returning None is how a *named but unprovisioned* channel opts out: listed in `channels`
        but with no token in `.env` means "not yet", which is reported once and is not an error.
        That keeps a half-provisioned machine from failing its backups over notification setup.
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

    def send(self, event: BasicEvent) -> None:
        """Deliver the event. May raise; the dispatcher catches, logs and continues.

        May also receive an event name it has never heard of: adding an operation is an additive
        contract change, so a "*" subscriber must tolerate unknown names.
        """
        raise NotImplementedError("Implement send() in your notifier")


class NotifierRegistrations(Registry[Notifier]):
    entry_point_group = "edwh_restic_plugin.notifiers"
    # No in-package discovery: core ships no notifiers, only the interface.
    in_package = None
    config_key = "notifiers"


notifiers = NotifierRegistrations()


def register_notifier(
    short_name: typing.Optional[str] = None,
    aliases: tuple[str, ...] = (),
    priority: int = -1,
) -> typing.Callable[[type[Notifier]], type[Notifier]]:
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


class Channel(typing.NamedTuple):
    """An active notifier plus the routing it was configured with."""

    notifier: Notifier
    events: tuple[str, ...]
    min_level: Level

    def wants(self, event: BasicEvent) -> bool:
        levels: dict[Level, int] = {"info": 0, "warning": 1, "error": 2}
        if levels[event.level] < levels[self.min_level]:
            return False

        return any(matches(pattern, event.name) for pattern in self.events)


_LEVELS: tuple[Level, ...] = ("info", "warning", "error")


def build_channels(
    env: typing.Mapping[str, str] | None = None,
    config: typing.Mapping[str, typing.Any] | None = None,
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
    if global_min not in _LEVELS:
        cprint(f"warning: [restic.notify] min_level '{global_min}' is not one of {_LEVELS}; using 'info'", "yellow")
        global_min = "info"

    channels = []
    for name in names:
        if not (cls := notifiers.get(name)):
            cprint(f"warning: [restic.notify] channels lists '{name}', which is not installed", "yellow")
            continue

        if cls.contract != CONTRACT_VERSION:
            # At resolution time, not mid-backup: an AttributeError at 04:00 inside a cron job is
            # a bad way to learn a plugin is stale.
            cprint(
                f"warning: notifier '{name}' was built for contract {cls.contract}, "
                f"this is {CONTRACT_VERSION}; skipping it",
                "yellow",
            )
            continue

        options = dict(config.get(name) or {}) if config_supplied else read_config("notify", name)
        try:
            instance = cls.from_config(env, options)
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
        if min_level not in _LEVELS:
            min_level = global_min

        channels.append(Channel(instance, tuple(events), min_level))

    return channels


class Dispatcher:
    """Sends events to channels, sequentially, and never lets one affect the caller."""

    def __init__(self, channels: typing.Sequence[Channel] | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._channels = list(channels) if channels is not None else None
        self.timeout = timeout
        # The watchdog dispatches from a timer thread while the main thread may be dispatching a
        # terminal event. Without the lock you get interleaved stderr and re-entrant notifier state.
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
                    self._send(channel, event)

    def _send(self, channel: Channel, event: BasicEvent) -> None:
        name = channel.notifier._short_name
        error: list[BaseException] = []

        def target() -> None:
            try:
                channel.notifier.send(event)
            except BaseException as e:  # a notifier must never reach the caller
                error.append(e)

        # A daemon thread rather than signal.alarm: this may already be running on the watchdog's
        # timer thread, and signals only work on the main thread.
        worker = threading.Thread(target=target, daemon=True, name=f"notify-{name}")
        worker.start()
        worker.join(self.timeout)

        if worker.is_alive():
            cprint(f"warning: notifier '{name}' timed out after {self.timeout}s and was abandoned", "yellow")
        elif error:
            cprint(f"warning: notifier '{name}' raised {error[0]!r}", "yellow")


#: Module-level dispatcher, so the watchdog and the emitters share one lock and one channel list.
dispatcher = Dispatcher()


def _hostname() -> str:
    return os.environ.get("RESTICHOSTNAME") or socket.gethostname() or platform.node()


def _project() -> str:
    return read_config("notify").get("project") or Path.cwd().name


class Emitter:
    """Tracks one operation and emits its lifecycle events.

    Created by repo_context, which is the only intended entry point.
    """

    def __init__(
        self,
        event_class: type[BasicEvent],
        repo: "Repository | None",
        repo_name: str,
        fields: dict[str, typing.Any],
        dispatcher_: Dispatcher | None = None,
    ) -> None:
        self.event_class = event_class
        self.repo = repo
        self.repo_name = repo_name
        self.fields = fields
        self.dispatcher = dispatcher_ or dispatcher
        self.started_at: float | None = None

    def build(self, status: Status) -> BasicEvent:
        operation = self.event_class.operation
        return self.event_class(
            status=status,
            ts=datetime.datetime.now(),
            level=level_for(operation, status.phase),
            repo=self.repo_name,
            # Falls back to the raw choice string when the repository could not be resolved: a
            # mistyped --connection-choice in a cron job is exactly the silent failure worth
            # reporting, and there is no Repository yet to ask.
            repo_display=self.repo.display_name() if self.repo else self.repo_name,
            host=_hostname(),
            project=_project(),
            **self.fields,
        )

    def emit(self, status: Status) -> None:
        self.dispatcher.dispatch(self.build(status))

    def update(self, **fields: typing.Any) -> None:
        """Add detail discovered while the operation ran, e.g. a snapshot id."""
        self.fields.update(fields)


__all__ = [
    "Channel",
    "Dispatcher",
    "Emitter",
    "Notifier",
    "NotifierRegistrations",
    "build_channels",
    "dispatcher",
    "notifiers",
    "register_notifier",
]
