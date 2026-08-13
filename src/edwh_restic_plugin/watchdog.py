"""
Detecting operations that hang.

An operation that hangs fires neither `succeeded` nor `failed`, so it is invisible to every
notifier: a third state, not a variant of failure. The main thread is blocked in
`c.run(..., pty=True)`, so a daemon timer thread is the right primitive.

It does not kill anything: killing restic mid-write risks leaving a stale repository lock, which is
what `edwh restic.unlock` exists to clean up.

It also lives inside the process, so a reboot or a cron job that never fired sends nothing. The
complement is an external monitor alarming on silence; this catches "running too long", a heartbeat
catches "never ran".
"""

import threading
import time
import typing as t

from .config import read_config
from .events import Slow

if t.TYPE_CHECKING:
    from .notify import Emitter

#: Escalating rather than a single shot, because "slow" and "certainly wedged" differ in kind.
DEFAULT_THRESHOLDS = ("30m", "2h")


def parse_duration(text: str | int | float) -> float:
    """Accept 30, "30", "30s", "30m", "2h" or "1d". Bare numbers are seconds."""
    if isinstance(text, int | float):
        return float(text)

    value = str(text).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    if value and value[-1] in units:
        return float(value[:-1]) * units[value[-1]]

    return float(value)


def thresholds_for(target: str | None, config: t.Mapping[str, t.Any] | None = None) -> list[float]:
    """Resolve warn_after, preferring a per-target override.

    Per-target matters because backup_files_* and backup_stream_* have wildly different expected
    runtimes: one global threshold would be tuned to the slowest and therefore useless for the
    rest.
    """
    config = read_config("notify") if config is None else config

    raw: t.Any = config.get("warn_after", DEFAULT_THRESHOLDS)
    if target:
        targets = config.get("targets") or {}
        if isinstance(targets, dict) and isinstance(targets.get(target), dict):
            raw = targets[target].get("warn_after", raw)

    if isinstance(raw, str | int | float):
        raw = [raw]

    try:
        return sorted(parse_duration(item) for item in raw)
    except (TypeError, ValueError):
        return sorted(parse_duration(item) for item in DEFAULT_THRESHOLDS)


class Watchdog:
    """Emits `slow` at each threshold until disarmed."""

    def __init__(
        self,
        emitter: "Emitter",
        target: str | None = None,
        thresholds: t.Sequence[float] | None = None,
    ) -> None:
        self.emitter = emitter
        self.thresholds = list(thresholds) if thresholds is not None else thresholds_for(target)
        self._timer: threading.Timer | None = None
        self._started_at: float | None = None
        self._index = 0
        self._lock = threading.Lock()

    def arm(self) -> None:
        self._started_at = time.monotonic()
        self._index = 0
        self._schedule()

    def disarm(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _schedule(self) -> None:
        if self._index >= len(self.thresholds):
            return

        elapsed = time.monotonic() - (self._started_at or time.monotonic())
        delay = max(0.0, self.thresholds[self._index] - elapsed)

        with self._lock:
            self._timer = threading.Timer(delay, self._fire)
            self._timer.daemon = True  # must not keep the process alive after the task returns
            self._timer.start()

    def _fire(self) -> None:
        threshold = self.thresholds[self._index]
        elapsed = time.monotonic() - (self._started_at or time.monotonic())

        # The dispatcher holds a lock, so this is safe against the main thread emitting a terminal
        # event at the same moment.
        self.emitter.emit(Slow(elapsed=elapsed, threshold=threshold))

        self._index += 1
        self._schedule()
