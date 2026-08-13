"""`restic.notify-test` sends synthetic events to the real notifiers.

The point is to exercise a channel without having to break a backup first, so the events go through
the actual Dispatcher and the actual configured channels. Nothing is faked except the event.
"""

import dataclasses

import pytest

from src.edwh_restic_plugin.events import ALL_PHASES, BasicEvent, Failed, Slow, Started, Succeeded
from src.edwh_restic_plugin.notify import (
    TEST_MARKER,
    Channel,
    Dispatcher,
    Notifier,
    build_test_event,
    event_names,
    sample_status,
)


class Recorder(Notifier):
    _short_name = "recorder"

    def __init__(self):
        self.events = []

    def send(self, event):
        self.events.append(event)


# --- the synthetic event --------------------------------------------------------------------


def test_every_event_name_can_be_built():
    """Adding an operation must not leave a name this task cannot send."""
    for name in event_names():
        assert build_test_event(name).name == name


def test_event_names_covers_every_operation_and_phase():
    assert len(event_names()) == len(BasicEvent.operations) * len(ALL_PHASES)


def test_an_unknown_operation_says_what_is_available():
    """A typo must not look like a plugin silently ignoring you."""
    with pytest.raises(ValueError, match="unknown operation 'nope'"):
        build_test_event("nope.failed")


def test_an_unknown_phase_says_what_is_available():
    with pytest.raises(ValueError, match="unknown phase 'exploded'"):
        build_test_event("backup.exploded")


def test_a_drill_is_always_marked():
    """A test event that reads like a real 3am alert is worse than no test at all."""
    for name in event_names():
        event = build_test_event(name, "s3:acme")
        assert TEST_MARKER in event.repo_display


def test_the_marker_survives_the_default_format():
    """Whatever a channel displays, the drill must be recognisable in it."""
    for name in event_names():
        rendered = Recorder().format(build_test_event(name, "s3:acme"))
        assert TEST_MARKER in rendered


def test_only_fields_the_operation_has_are_set():
    """The sample values are filtered per operation, so adding one needs no change here."""
    wipe = build_test_event("wipe.failed")
    backup = build_test_event("backup.failed")

    assert not hasattr(wipe, "target")
    assert backup.target == "files"
    assert backup.scripts and TEST_MARKER.lower() in backup.scripts[0].script


def test_a_resolved_repository_shows_its_display_name():
    assert build_test_event("backup.failed", "s3:acme").repo_display.startswith("s3:acme")


def test_without_a_repository_it_says_so_rather_than_inventing_one():
    assert "no repository resolved" in build_test_event("backup.failed").repo_display


@pytest.mark.parametrize("phase", ALL_PHASES)
def test_sample_status_covers_every_phase(phase):
    status = sample_status(phase)

    assert status.phase == phase
    assert isinstance(status, Started | Succeeded | Failed | Slow)


def test_a_drill_carries_no_environment_secret(monkeypatch):
    secret = "SUPERSECRETVALUE12345"
    monkeypatch.setenv("RESTIC_PASSWORD", secret)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)

    for name in event_names():
        event = build_test_event(name, "s3:acme")
        assert secret not in repr(event) + repr(dataclasses.asdict(event))


# --- delivery -------------------------------------------------------------------------------


def test_send_to_bypasses_routing_but_keeps_containment():
    """What --force uses: prove a channel works at all, separately from whether routing is right."""
    recorder = Recorder()
    channel = Channel(recorder, ("backup.failed",), "error")
    event = build_test_event("check.succeeded")

    assert not channel.wants(event)  # routing would drop it

    Dispatcher([channel]).send_to(channel, event)

    assert len(recorder.events) == 1


def test_send_to_still_swallows_a_raising_notifier(capsys):
    class Boom(Notifier):
        _short_name = "boom"

        def send(self, _event):
            raise RuntimeError("channel down")

    channel = Channel(Boom(), ("*",), "info")
    Dispatcher([channel]).send_to(channel, build_test_event("backup.failed"))

    assert "raised" in capsys.readouterr().out
