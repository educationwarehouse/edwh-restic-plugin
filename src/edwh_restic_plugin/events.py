"""
The event schema notifiers receive.

One frozen dataclass per operation, carrying a `status` drawn from a union of phase objects, so
neither axis has to make the other's fields optional.

Fields are an allowlist: no repository uri (backends embed credentials in it, so
`Repository.display_name()` is the safe substitute), no environment, no free-form mapping. That
prevents accidental disclosure by a well-meaning plugin; it is not a sandbox, since a notifier runs
in-process. See docs/plugins-architecture.md for the reasoning.

NB: do not add `from __future__ import annotations` here. `__init_subclass__` reads `operation`
from the class body, and postponed evaluation would break any annotation introspection added later.
"""

import dataclasses as dc
import datetime as dt
import typing as t

from .exceptions import ScriptFailure

Level = t.Literal["info", "warning", "error"]
Phase = t.Literal["started", "succeeded", "failed", "slow"]


# ---------------------------------------------------------------------------------------------
# phase axis: shared by every operation
# ---------------------------------------------------------------------------------------------


@dc.dataclass(frozen=True, kw_only=True)
class Started:
    phase: t.Literal["started"] = "started"


@dc.dataclass(frozen=True, kw_only=True)
class Succeeded:
    phase: t.Literal["succeeded"] = "succeeded"
    duration: float


@dc.dataclass(frozen=True, kw_only=True)
class Failed:
    phase: t.Literal["failed"] = "failed"
    duration: float
    exit_code: int
    #: Full stdout/stderr. Restic prints repository URIs, so route this only to channels you would
    #: paste a terminal session into.
    logs: str | None = None


@dc.dataclass(frozen=True, kw_only=True)
class Slow:
    phase: t.Literal["slow"] = "slow"
    #: Not `duration`: the operation has not finished.
    elapsed: float
    threshold: float


Status = Started | Succeeded | Failed | Slow


# ---------------------------------------------------------------------------------------------
# operation axis
# ---------------------------------------------------------------------------------------------


@dc.dataclass(frozen=True, kw_only=True)
class BasicEvent:
    #: Set per subclass; also the registry key.
    operation: t.ClassVar[str]
    #: operation -> class, filled at class creation so there is no second list to maintain.
    operations: t.ClassVar[dict[str, type["BasicEvent"]]] = {}

    status: Status
    ts: dt.datetime
    level: Level
    #: Repository short_name, e.g. "s3".
    repo: str
    #: Repository.display_name(), never the raw uri.
    repo_display: str
    #: RESTICHOSTNAME or the platform hostname.
    host: str
    #: Directory name, or the `project` key of `[restic.notify]`.
    project: str

    def __init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__init_subclass__(**kwargs)

        # Guarding on __dict__ rather than hasattr lets intermediate bases and plugin subclasses
        # exist: a class that does not declare its own operation is not a new operation.
        if "operation" not in cls.__dict__:
            return

        if clash := BasicEvent.operations.get(cls.operation):
            raise TypeError(f"{cls.__name__} reuses operation {cls.operation!r} from {clash.__name__}")

        BasicEvent.operations[cls.operation] = cls

    @property
    def name(self) -> str:
        """What `.toml` `events` patterns and `Notifier.subscribes` match against."""
        return f"{self.operation}.{self.status.phase}"

    def __str__(self) -> str:
        return f"{self.name} ({self.repo_display})"


@dc.dataclass(frozen=True, kw_only=True)
class BackupEvent(BasicEvent):
    operation: t.ClassVar[str] = "backup"
    target: str | None = None
    snapshot: str | None = None
    message: str | None = None
    #: Which captain-hooks scripts failed. Collected here rather than emitted per script, so one
    #: backup produces one event instead of N+1.
    scripts: tuple[ScriptFailure, ...] = ()


@dc.dataclass(frozen=True, kw_only=True)
class RestoreEvent(BasicEvent):
    operation: t.ClassVar[str] = "restore"
    target: str | None = None
    snapshot: str | None = None
    scripts: tuple[ScriptFailure, ...] = ()


@dc.dataclass(frozen=True, kw_only=True)
class CheckEvent(BasicEvent):
    operation: t.ClassVar[str] = "check"
    read_data: bool = False
    subset: str = ""


@dc.dataclass(frozen=True, kw_only=True)
class ForgetEvent(BasicEvent):
    operation: t.ClassVar[str] = "forget"
    policy: str | None = None
    snapshots_removed: int | None = None


@dc.dataclass(frozen=True, kw_only=True)
class WipeEvent(BasicEvent):
    operation: t.ClassVar[str] = "wipe"


#: Hand-written because no type checker can follow a computed union. A test asserts it matches
#: BasicEvent.operations, which is the half that maintains itself.
Event = BackupEvent | RestoreEvent | CheckEvent | ForgetEvent | WipeEvent

ALL_PHASES: tuple[Phase, ...] = t.get_args(Phase)

_PHASE_LEVELS: dict[Phase, Level] = {
    "started": "info",
    "succeeded": "info",
    "failed": "error",
    "slow": "warning",
}


def level_for(phase: Phase) -> Level:
    """The level an event of this phase carries."""
    return _PHASE_LEVELS[phase]


def matches(pattern: str, name: str) -> bool:
    """Does an event name match a `.toml` `events` entry or a `subscribes` glob?

    Supports "*" and a trailing ".*", which is all the config syntax promises. Deliberately not
    fnmatch, so `backup.*` cannot accidentally match a future operation called `backup_verify`.
    """
    if pattern == "*":
        return True
    elif pattern.endswith(".*"):
        return name.startswith(pattern[:-1])
    else:
        return pattern == name
