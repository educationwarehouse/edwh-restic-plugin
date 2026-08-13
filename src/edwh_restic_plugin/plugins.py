"""
The one import path a plugin should depend on.

Everything a repository or notifier author needs is re-exported here, so a plugin never reaches
into `.events`, `.notify` or `.repositories` internals. Those are free to move; this is not.

    from edwh_restic_plugin.plugins import CONTRACT_VERSION, Notifier, register_notifier

This module is a facade and deliberately holds no logic. The layering underneath is
`registry` (discovery primitives) -> `events` / `notify` / `repositories` -> `plugins`.
"""

from .events import (
    ALL_PHASES,
    BackupEvent,
    BasicEvent,
    CheckEvent,
    Event,
    Failed,
    ForgetEvent,
    Level,
    Phase,
    RestoreEvent,
    ScriptFailure,
    Slow,
    Started,
    Status,
    Succeeded,
    WipeEvent,
    level_for,
    matches,
)
from .exceptions import (
    NoScriptsFound,
    ResticConnectionError,
    ResticError,
    ResticScriptError,
    UnsupportedOperation,
)
from .notify import Channel, Dispatcher, Notifier, notifiers, register_notifier
from .registry import CONTRACT_VERSION, Registration, Registry
from .repositories import Repository, register, registrations

__all__ = [
    "ALL_PHASES",
    "CONTRACT_VERSION",
    "BackupEvent",
    "BasicEvent",
    "Channel",
    "CheckEvent",
    "Dispatcher",
    "Event",
    "Failed",
    "ForgetEvent",
    "Level",
    "NoScriptsFound",
    "Notifier",
    "Phase",
    "Registration",
    "Registry",
    "Repository",
    "ResticConnectionError",
    "ResticError",
    "ResticScriptError",
    "RestoreEvent",
    "ScriptFailure",
    "Slow",
    "Started",
    "Status",
    "Succeeded",
    "UnsupportedOperation",
    "WipeEvent",
    "level_for",
    "matches",
    "notifiers",
    "register",
    "register_notifier",
    "registrations",
]
