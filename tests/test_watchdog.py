"""An operation that hangs fires neither succeeded nor failed -- a third state, not a failure."""

import threading
import time

import pytest

from src.edwh_restic_plugin.events import BackupEvent
from src.edwh_restic_plugin.notify import Channel, Dispatcher, Emitter, Notifier
from src.edwh_restic_plugin.watchdog import DEFAULT_THRESHOLDS, Watchdog, parse_duration, thresholds_for


class Recorder(Notifier):
    _short_name = "recorder"

    def __init__(self):
        self.events = []
        self.seen = threading.Event()

    def send(self, event):
        self.events.append(event)
        self.seen.set()


@pytest.fixture()
def emitter():
    recorder = Recorder()
    dispatcher = Dispatcher([Channel(recorder, ("*",), "info")])
    made = Emitter(BackupEvent, None, "s3", {}, dispatcher)
    made.recorder = recorder
    return made


# --- duration parsing -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", 30.0),
        ("30m", 1800.0),
        ("2h", 7200.0),
        ("1d", 86400.0),
        ("45", 45.0),  # bare numbers are seconds
        (45, 45.0),
        (1.5, 1.5),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_thresholds_default_when_unconfigured():
    assert thresholds_for(None, config={}) == sorted(parse_duration(t) for t in DEFAULT_THRESHOLDS)


def test_thresholds_are_sorted():
    """Escalation only makes sense in order, whatever order the config listed them in."""
    assert thresholds_for(None, config={"warn_after": ["2h", "30m"]}) == [1800.0, 7200.0]


def test_a_single_threshold_need_not_be_a_list():
    assert thresholds_for(None, config={"warn_after": "10m"}) == [600.0]


def test_per_target_override():
    """backup_files_* and backup_stream_* have wildly different expected runtimes."""
    config = {"warn_after": ["30m"], "targets": {"stream": {"warn_after": ["4h"]}}}

    assert thresholds_for("files", config=config) == [1800.0]
    assert thresholds_for("stream", config=config) == [14400.0]


def test_a_malformed_threshold_falls_back_rather_than_crashing():
    assert thresholds_for(None, config={"warn_after": ["not a duration"]}) == sorted(
        parse_duration(t) for t in DEFAULT_THRESHOLDS
    )


# --- firing ---------------------------------------------------------------------------------


def test_it_emits_slow_when_the_threshold_passes(emitter):
    watchdog = Watchdog(emitter, thresholds=[0.05])
    watchdog.arm()

    assert emitter.recorder.seen.wait(timeout=2), "watchdog never fired"
    watchdog.disarm()

    event = emitter.recorder.events[0]
    assert event.name == "backup.slow"
    assert event.level == "warning"
    assert event.status.threshold == 0.05
    assert event.status.elapsed >= 0.05


def test_it_escalates_through_every_threshold(emitter):
    watchdog = Watchdog(emitter, thresholds=[0.05, 0.1])
    watchdog.arm()

    deadline = time.monotonic() + 3
    while len(emitter.recorder.events) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    watchdog.disarm()

    assert [e.status.threshold for e in emitter.recorder.events] == [0.05, 0.1]


def test_disarming_before_the_threshold_emits_nothing(emitter):
    watchdog = Watchdog(emitter, thresholds=[5.0])
    watchdog.arm()
    watchdog.disarm()

    time.sleep(0.1)
    assert emitter.recorder.events == []


def test_no_thresholds_means_no_timer(emitter):
    watchdog = Watchdog(emitter, thresholds=[])
    watchdog.arm()

    time.sleep(0.05)
    assert emitter.recorder.events == []
    watchdog.disarm()


def test_the_timer_thread_is_a_daemon(emitter):
    """It must not keep the process alive after the task returns."""
    watchdog = Watchdog(emitter, thresholds=[10.0])
    watchdog.arm()

    assert watchdog._timer is not None
    assert watchdog._timer.daemon

    watchdog.disarm()


def test_disarm_is_idempotent(emitter):
    watchdog = Watchdog(emitter, thresholds=[10.0])
    watchdog.arm()
    watchdog.disarm()
    watchdog.disarm()  # must not raise


def test_it_does_not_kill_anything(emitter):
    """Killing restic mid-write risks a stale lock, which is why `edwh restic.unlock` exists.

    The watchdog reports and nothing else; a hard kill belongs behind a separate named option.
    """
    watchdog = Watchdog(emitter, thresholds=[0.05])

    assert not hasattr(watchdog, "kill")
    assert not hasattr(watchdog, "terminate")

    watchdog.arm()
    assert emitter.recorder.seen.wait(timeout=2)
    watchdog.disarm()
