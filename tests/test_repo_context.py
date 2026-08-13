"""Operation is the only emit site: it resolves a repository and reports the lifecycle.

Collaborators are injected rather than patched, which is what Operation's constructor arguments are
for: a resolver, a dispatcher and watchdog thresholds.
"""

from contextlib import chdir
from pathlib import Path

import pytest

from src.edwh_restic_plugin.events import BackupEvent, CheckEvent
from src.edwh_restic_plugin.exceptions import ResticScriptError, ScriptFailure
from src.edwh_restic_plugin.notify import Channel, Dispatcher, Notifier
from src.edwh_restic_plugin.repositories import Repository
from src.edwh_restic_plugin.tasks import Operation


class Recorder(Notifier):
    _short_name = "recorder"

    def __init__(self):
        self.events = []

    def send(self, event):
        self.events.append(event)

    @property
    def names(self):
        return [e.name for e in self.events]


class _FakeRepo(Repository):
    _short_name = "fake"

    def _require_restic(self):
        pass

    def setup(self):
        pass

    def prepare_for_restic(self, c):
        pass

    @property
    def uri(self):
        return "fake:user:hunter2@host/repo"

    @property
    def display_name(self):
        return "fake:repo"


@pytest.fixture()
def recorder(tmp_path):
    with chdir(tmp_path):
        Path(".env").touch()
        yield Recorder()


def operation(recorder, event_class, resolve=None, **fields):
    """An Operation wired to the recorder, with the watchdog silent (no thresholds)."""
    return Operation(
        "fake",
        event_class,
        resolve=resolve or (lambda *_a, **_kw: _FakeRepo()),
        dispatcher=Dispatcher([Channel(recorder, ("*",), "info")]),
        thresholds=[],
        **fields,
    )


def test_success_emits_started_then_succeeded(recorder):
    with operation(recorder, BackupEvent, target="files").run():
        pass

    assert recorder.names == ["backup.started", "backup.succeeded"]
    assert recorder.events[1].status.duration >= 0
    assert recorder.events[0].target == "files"


def test_failure_emits_started_then_failed_and_reraises(recorder):
    with pytest.raises(ResticScriptError), operation(recorder, BackupEvent).run():
        raise ResticScriptError([ScriptFailure("backup_files_pg.sh", 3)])

    assert recorder.names == ["backup.started", "backup.failed"]

    failed = recorder.events[1].status
    assert failed.exit_code == 3  # the worst script's code, not a generic 1
    assert "backup_files_pg.sh exited 3" in failed.logs


def test_a_plain_exception_becomes_exit_code_one(recorder):
    with pytest.raises(ValueError), operation(recorder, CheckEvent).run():
        raise ValueError("something else")

    assert recorder.events[1].status.exit_code == 1


def test_the_raw_uri_never_reaches_an_event(recorder):
    with operation(recorder, BackupEvent).run():
        pass

    for event in recorder.events:
        assert event.repo_display == "fake:repo"
        assert "hunter2" not in repr(event)


def test_resolution_failure_is_reported(recorder):
    """A mistyped --connection-choice in a cron job is exactly the silent failure to catch."""

    def boom(*_args, **_kwargs):
        raise ValueError("Invalid connection type nope")

    with pytest.raises(ValueError), operation(recorder, BackupEvent, resolve=boom).run():
        pass  # pragma: no cover: the failure happens on entry

    assert recorder.names == ["backup.failed"]
    # No Repository existed yet, so it falls back to the choice string rather than inventing one.
    assert recorder.events[0].repo_display == "fake"


def test_update_carries_detail_into_the_terminal_event(recorder):
    with operation(recorder, CheckEvent, read_data=True).run() as repo:
        assert repo.display_name == "fake:repo"

    assert recorder.events[-1].read_data is True
    assert recorder.events[-1].name == "check.succeeded"
