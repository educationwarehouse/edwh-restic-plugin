"""wipe/bucket/prepare_rclone_config are optional, not abstract.

Declaring them abstract meant a third-party repository had to implement six members to be
instantiable, three of them purely so two tasks it may never use could work. They now degrade at
the point of use.
"""

from contextlib import chdir
from pathlib import Path

import pytest

from src.edwh_restic_plugin.exceptions import ResticError, UnsupportedOperation
from src.edwh_restic_plugin.repositories import Repository


class _MinimalRepository(Repository):
    """Everything a third-party repository must implement, and nothing more."""

    _short_name = "minimal"

    def _require_restic(self):
        pass

    def setup(self):
        pass

    def prepare_for_restic(self, c):
        pass

    @property
    def uri(self):
        return "minimal:repo"


@pytest.fixture()
def repo(tmp_path):
    with chdir(tmp_path):
        Path(".env").touch()
        yield _MinimalRepository()


def test_three_members_are_enough_to_instantiate(repo):
    assert repo.uri == "minimal:repo"


def test_wipe_degrades(repo):
    with pytest.raises(UnsupportedOperation) as caught:
        repo.wipe()

    assert caught.value.repository == "minimal"
    assert caught.value.operation == "wipe"
    assert "does not support wipe" in str(caught.value)


def test_bucket_degrades(repo):
    with pytest.raises(UnsupportedOperation):
        _ = repo.bucket  # a property, so accessing it is the call


def test_rclone_config_degrades(repo):
    with pytest.raises(UnsupportedOperation):
        repo.prepare_rclone_config()


def test_unsupported_is_a_restic_error():
    """So the task-layer decorator turns it into an exit code rather than a traceback."""
    assert issubclass(UnsupportedOperation, ResticError)
    assert UnsupportedOperation("minimal", "wipe").exit_code == 1


def test_a_repository_missing_a_required_member_still_fails(tmp_path):
    class Incomplete(Repository):
        def setup(self):
            pass

        # no prepare_for_restic, no uri

    with chdir(tmp_path), pytest.raises(TypeError):
        Incomplete()
