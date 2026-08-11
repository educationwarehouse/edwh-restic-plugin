"""
The event schema notifiers receive.

Two axes carry real information, and picking only one makes the other's fields optional lies:
**operation** determines target/snapshot/message/policy/snapshots_removed, **phase** determines
duration/exit_code/logs/elapsed/threshold. So they are composed -- one frozen dataclass per
operation, carrying a `status` that is a small union of phase objects. Phases are universal
across operations, so there is nothing to map between the two axes.

The fields here are an **allowlist**, not a filtered dump. The naive design assembles a rich
event and subtracts secrets, which fails open: every field added later leaks by default until
someone remembers to scrub it. Starting from nothing and adding only what is enumerated fails
closed -- the failure mode of forgetting is a missing field, not a credential on the wire.

Note in particular: no repo_uri (several backends embed credentials in it -- see
Repository.display_name), no env, no os.environ passthrough, and no free-form `extra` mapping to
smuggle one through.

This is a guardrail, not a security boundary: a notifier runs in-process and can read os.environ
directly. It prevents *accidental* disclosure by a well-intentioned plugin, which is the failure
that actually happens.

NB: this module must not use `from __future__ import annotations`. __init_subclass__ reads
`operation` from the class body, and postponed evaluation is a trap waiting for the next person
who adds annotation introspection here.
"""

import dataclasses
import datetime
import typing

# Defined with the exception that carries it. Collected onto the terminal event rather than
# emitted per script: execute_files runs N scripts, so per-script events would mean a notifier
# receives N+1 events for one backup.
from .exceptions import ScriptFailure

Level = typing.Literal["info", "warning", "error"]
Phase = typing.Literal["started", "succeeded", "failed", "slow"]


# --------------------------------------------------------------------------------------------
# phase axis: shared by every operation
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class Started:
    phase: typing.Literal["started"] = "started"


@dataclasses.dataclass(frozen=True, kw_only=True)
class Succeeded:
    phase: typing.Literal["succeeded"] = "succeeded"
    duration: float


@dataclasses.dataclass(frozen=True, kw_only=True)
class Failed:
    phase: typing.Literal["failed"] = "failed"
    duration: float
    exit_code: int
    #: Full stdout/stderr. Restic prints repository URIs, so a channel carrying this should be one
    #: you would paste a terminal session into. That is a routing decision -- see [restic.notify].
    logs: str | None = None


@dataclasses.dataclass(frozen=True, kw_only=True)
class Slow:
    phase: typing.Literal["slow"] = "slow"
    #: Not `duration`: the operation has not finished. One field meaning "final runtime" on some
    #: events and "so far" on others was a bug waiting to be read the wrong way.
    elapsed: float
    threshold: float


Status = Started | Succeeded | Failed | Slow


# --------------------------------------------------------------------------------------------
# operation axis
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, kw_only=True)
class BasicEvent:
    #: Set per subclass; also the registry key. See __init_subclass__.
    operation: typing.ClassVar[str]
    #: operation -> class, filled at class creation so there is no second list to maintain.
    operations: typing.ClassVar[dict[str, type["BasicEvent"]]] = {}

    status: Status
    ts: datetime.datetime
    level: Level
    #: Repository short_name, e.g. "s3".
    repo: str
    #: Repository.display_name() -- never the raw uri, which can embed credentials.
    repo_display: str
    #: RESTICHOSTNAME or the platform hostname.
    host: str
    #: Directory name, or [restic.notify] project.
    project: str

    def __init_subclass__(cls, **kwargs: typing.Any) -> None:
        super().__init_subclass__(**kwargs)

        # Guarding on __dict__ rather than hasattr is what lets intermediate bases and plugin
        # subclasses exist: a class that does not declare its own operation is not a new
        # operation, and is correctly ignored instead of re-registering an inherited one.
        if "operation" not in cls.__dict__:
            return

        if clash := BasicEvent.operations.get(cls.operation):
            # A definition-time property, so it fails when you write the class rather than when
            # somebody runs the tests.
            raise TypeError(f"{cls.__name__} reuses operation {cls.operation!r} from {clash.__name__}")

        BasicEvent.operations[cls.operation] = cls

    @property
    def name(self) -> str:
        """Derived, so no flat list of event names exists to drift out of sync.

        This is what `.toml` `events` patterns and Notifier.subscribes match against.
        """
        return f"{self.operation}.{self.status.phase}"

    def __str__(self) -> str:
        return f"{self.name} ({self.repo_display})"


@dataclasses.dataclass(frozen=True, kw_only=True)
class BackupEvent(BasicEvent):
    operation: typing.ClassVar[str] = "backup"
    target: str | None = None
    snapshot: str | None = None
    message: str | None = None
    scripts: tuple[ScriptFailure, ...] = ()


@dataclasses.dataclass(frozen=True, kw_only=True)
class RestoreEvent(BasicEvent):
    operation: typing.ClassVar[str] = "restore"
    target: str | None = None
    snapshot: str | None = None
    scripts: tuple[ScriptFailure, ...] = ()


@dataclasses.dataclass(frozen=True, kw_only=True)
class CheckEvent(BasicEvent):
    operation: typing.ClassVar[str] = "check"
    read_data: bool = False
    subset: str = ""


@dataclasses.dataclass(frozen=True, kw_only=True)
class ForgetEvent(BasicEvent):
    operation: typing.ClassVar[str] = "forget"
    policy: str | None = None
    snapshots_removed: int | None = None


@dataclasses.dataclass(frozen=True, kw_only=True)
class WipeEvent(BasicEvent):
    operation: typing.ClassVar[str] = "wipe"


#: Hand-written because no type checker can follow a computed union. A test asserts it matches
#: BasicEvent.operations, which is the half that maintains itself.
Event = BackupEvent | RestoreEvent | CheckEvent | ForgetEvent | WipeEvent

#: Every operation can reach every phase. An earlier draft kept a per-operation phase set, on the
#: assumption that e.g. wipe could not fail -- but restic_reaper can raise, and the watchdog arms
#: on any Started, so the sets were uniform and the bookkeeping bought nothing. Coverage is
#: therefore asserted as "every operation is emitted somewhere" plus a phase test on the emitter,
#: rather than as a 20-pair cross product.
ALL_PHASES: tuple[Phase, ...] = ("started", "succeeded", "failed", "slow")

#: Level for each (operation, phase) that is not derivable from the phase alone: wipe.succeeded is
#: a warning while backup.succeeded is info, so the mapping is not one-to-one.
_LEVELS: dict[tuple[str, str], Level] = {
    ("wipe", "started"): "warning",
    ("wipe", "succeeded"): "warning",
}


#: The phase's level, before per-operation overrides.
_PHASE_LEVELS: dict[Phase, Level] = {
    "started": "info",
    "succeeded": "info",
    "failed": "error",
    "slow": "warning",
}


def level_for(operation: str, phase: Phase) -> Level:
    if override := _LEVELS.get((operation, phase)):
        return override

    return _PHASE_LEVELS[phase]


def matches(pattern: str, name: str) -> bool:
    """Does an event name match a `.toml` `events` entry or a `subscribes` glob?

    Supports "*" for everything and a trailing ".*" for a whole operation, which is all the
    config syntax promises -- deliberately not fnmatch, so `backup.*` cannot accidentally match
    something in a future operation called `backup_verify`.
    """
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return name.startswith(pattern[:-1])

    return pattern == name
