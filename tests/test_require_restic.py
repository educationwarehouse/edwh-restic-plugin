"""Constructing a Repository must not be able to install anything.

_require_restic() may call require_sudo() and `sudo apt install -y restic`, so it is opt-in:
callers that genuinely need restic present ask for it. `configure` is the only task that does,
since provisioning is its job.
"""

from contextlib import chdir
from pathlib import Path

from src.edwh_restic_plugin.repositories import Repository


class _CountingRepository(Repository):
    """Records how often the restic check was requested, without running it."""

    _short_name = "counting"

    def __init__(self):
        self.restic_checks = 0
        super().__init__()

    def _require_restic(self):
        self.restic_checks += 1

    def setup(self):
        pass

    def prepare_for_restic(self, c):
        pass

    @property
    def uri(self):
        return "counting:repo"

    def wipe(self, dry=False):
        raise NotImplementedError

    @property
    def bucket(self):
        return "bucket"

    def prepare_rclone_config(self):
        return ""


def test_construction_does_not_require_restic(tmp_path):
    with chdir(tmp_path):
        Path(".env").touch()
        repo = _CountingRepository()

    assert repo.restic_checks == 0


def test_construction_still_creates_the_dotenv(tmp_path):
    """The other half of __init__ is unchanged."""
    with chdir(tmp_path):
        assert not Path(".env").exists()
        _CountingRepository()
        assert Path(".env").exists()


def test_cli_repo_requires_restic_only_when_asked(tmp_path, monkeypatch):
    from src.edwh_restic_plugin import tasks

    with chdir(tmp_path):
        Path(".env").write_text("COUNTING_PASSWORD=x\n")
        monkeypatch.setattr(tasks.registrations, "get", lambda _name: _CountingRepository)
        monkeypatch.setattr(tasks.registrations, "to_ordered_dict", lambda: {"counting": _CountingRepository})

        assert tasks.cli_repo("counting").restic_checks == 0
        assert tasks.cli_repo("counting", require_restic=True).restic_checks == 1
