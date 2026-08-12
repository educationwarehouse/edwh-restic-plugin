"""
Notifier registry, routing and dispatch.

A notifier is trusted like any other dependency: it runs in-process and can read os.environ
directly. What is guaranteed is that it cannot affect the backup.
"""

import abc
import datetime as dt
import os
import platform
import socket
import threading
import typing as t
from pathlib import Path

from termcolor import cprint

from .config import read_config
from .env import DOTENV, read_dotenv
from .events import BasicEvent, Failed, Level, Slow, Status, Succeeded, level_for, matches
from .registry import CONTRACT_VERSION, MIN_SUPPORTED_CONTRACT, Registration, Registry

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

    #: Which contract this notifier was written against. A mismatch is warned about and the
    #: notifier skipped, at discovery time rather than mid-backup.
    contract: int = CONTRACT_VERSION

    #: Default routing, overridden by [restic.notify.<name>] events.
    subscribes: tuple[str, ...] = ("*",)

    @classmethod
    def from_config(
        cls,
        env: t.Mapping[str, str],  # noqa: ARG003 (part of the contract; overrides use it)
        options: t.Mapping[str, t.Any],  # noqa: ARG003
    ) -> "t.Self | None":
        """Build an instance, or return None to stay inactive.

        `env` is the parsed `.env`; `options` is the resolved `[restic.notify.<name>]` table. Both
        are passed in so a notifier never has to locate or parse configuration itself.

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
    entry_point_group = "edwh_restic_plugin.notifiers"
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
                    self._send(channel, event)

    def _send(self, channel: Channel, event: BasicEvent) -> None:
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


#: Module-level dispatcher, so the watchdog and the emitters share one lock and one channel list.
dispatcher = Dispatcher()


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
        dispatcher_: Dispatcher | None = None,
    ) -> None:
        self.event_class = event_class
        self.repo = repo
        self.repo_name = repo_name
        self.fields = fields
        self.dispatcher = dispatcher_ or dispatcher
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
