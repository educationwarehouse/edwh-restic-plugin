from contextlib import chdir
from pathlib import Path

import invoke
import pytest

from src.edwh_restic_plugin.exceptions import (
    NoScriptsFound,
    ResticConnectionError,
    ResticError,
    ResticScriptError,
    ScriptFailure,
)
from src.edwh_restic_plugin.repositories import Repository
from src.edwh_restic_plugin.tasks import exits_on_restic_error


def test_every_exception_carries_an_exit_code():
    assert ResticError.exit_code == 1
    assert NoScriptsFound.exit_code == 255  # what sys.exit(255) used to produce
    assert ResticConnectionError.exit_code == 1


def test_script_error_reports_the_worst_exit_code():
    """Regression test for the walrus precedence bug.

    `if worst := max(file_codes) > 0` bound the *comparison*, so every failure exited 1
    regardless of what the script actually returned.
    """
    error = ResticScriptError([ScriptFailure("a.sh", 2), ScriptFailure("b.sh", 7), ScriptFailure("c.sh", 3)])

    assert error.exit_code == 7
    assert error.exit_code is not True  # the bug produced a bool
    assert "a.sh(2)" in str(error) and "b.sh(7)" in str(error)


def test_script_error_needs_at_least_one_failure():
    with pytest.raises(ValueError):
        ResticScriptError([])


def test_get_scripts_raises_instead_of_exiting(tmp_path):
    """A bare sys.exit() here made backup.failed notification impossible."""
    with chdir(tmp_path):
        Path("captain-hooks").mkdir()

        with pytest.raises(NoScriptsFound) as caught:
            Repository.get_scripts("files", "backup")

    assert caught.value.exit_code == 255
    assert caught.value.verb == "backup"
    assert caught.value.target == "files"


def test_get_scripts_returns_matching_files(tmp_path):
    with chdir(tmp_path):
        folder = Path("captain-hooks")
        folder.mkdir()
        (folder / "backup_files_pg.sh").touch()
        (folder / "restore_files_pg.sh").touch()

        found = Repository.get_scripts("files", "backup")

    assert [Path(f).name for f in found] == ["backup_files_pg.sh"]


class _FakeResult:
    def __init__(self, exited: int = 0, stdout: str = "") -> None:
        self.exited = exited
        self.stdout = stdout
        self.ok = exited == 0


class _FakeContext:
    """Runs every captain-hooks script as a failure, and anything else as a success."""

    def __init__(self, failing: dict[str, int]) -> None:
        self.failing = failing
        self.commands: list[str] = []

    def run(self, command, **_kwargs):
        self.commands.append(command)
        if (code := self.failing.get(Path(command).name)) is not None:
            raise invoke.exceptions.UnexpectedExit(_FakeResult(code, "snapshot abc123 saved"))
        return _FakeResult(0, "snapshot def456 saved")


class _StubRepository(Repository):
    _short_name = "stub"

    def setup(self):
        pass

    def prepare_for_restic(self, c):
        pass

    def prepare_env_for_restic(self, c):
        pass

    @property
    def uri(self):
        return "stub:repo"

    def wipe(self, dry=False):
        raise NotImplementedError

    @property
    def bucket(self):
        return "bucket"

    def prepare_rclone_config(self):
        return ""


def test_execute_files_raises_with_the_worst_code(tmp_path):
    with chdir(tmp_path):
        folder = Path("captain-hooks")
        folder.mkdir()
        (folder / "backup_files_a.sh").touch()
        (folder / "backup_files_b.sh").touch()
        Path(".env").touch()

        repo = _StubRepository()
        context = _FakeContext(failing={"backup_files_a.sh": 2, "backup_files_b.sh": 5})

        with pytest.raises(ResticScriptError) as caught:
            repo.execute_files(context, target="files", verb="backup", verbose=False)

    assert caught.value.exit_code == 5
    assert {f.exit_code for f in caught.value.failures} == {2, 5}


def test_execute_files_is_silent_when_every_script_succeeds(tmp_path):
    with chdir(tmp_path):
        folder = Path("captain-hooks")
        folder.mkdir()
        (folder / "backup_files_a.sh").touch()
        Path(".env").touch()

        repo = _StubRepository()
        repo.execute_files(_FakeContext(failing={}), target="files", verb="backup", verbose=False)


def test_decorator_turns_a_restic_error_into_an_exit_code(capsys):
    @exits_on_restic_error
    def task_body(_c):
        raise ResticScriptError([ScriptFailure("a.sh", 4)])

    with pytest.raises(SystemExit) as caught:
        task_body(None)

    assert caught.value.code == 4
    assert "a.sh(4)" in capsys.readouterr().err


def test_decorator_leaves_other_exceptions_alone():
    @exits_on_restic_error
    def task_body(_c):
        raise ValueError("not a restic problem")

    with pytest.raises(ValueError):
        task_body(None)


def test_decorator_returns_the_value_on_success():
    @exits_on_restic_error
    def task_body(_c):
        return "done"

    assert task_body(None) == "done"
