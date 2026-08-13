from contextlib import chdir
from pathlib import Path

import pytest

from src.edwh_restic_plugin.config import read_config, reset_warnings


@pytest.fixture(autouse=True)
def _fresh_warnings():
    reset_warnings()


def test_reads_the_project_toml(tmp_path):
    with chdir(tmp_path):
        Path(".toml").write_text('[restic.notify]\nchannels = ["ntfy"]\n')

        assert read_config("notify") == {"channels": ["ntfy"]}


def test_missing_section_is_an_empty_dict(tmp_path):
    """Absent and empty both mean 'nothing configured', so callers need not distinguish."""
    with chdir(tmp_path):
        Path(".toml").write_text("[restic.forget.default]\nkeep-last = 5\n")

        assert read_config("notify") == {}


def test_missing_files_are_an_empty_dict(tmp_path):
    with chdir(tmp_path):
        assert read_config("notify") == {}


def test_falls_back_to_default_toml_and_warns(tmp_path, capsys):
    with chdir(tmp_path):
        Path("default.toml").write_text('[restic.notify]\nchannels = ["ntfy"]\n')
        Path(".toml").write_text("")

        assert read_config("notify") == {"channels": ["ntfy"]}

        warning = capsys.readouterr().out
        assert "has no [restic.notify]" in warning
        assert "adopt and freeze" in warning


def test_the_fallback_never_writes_to_the_project_toml(tmp_path):
    """The freeze must stay an explicit act; reading config must not mutate config."""
    with chdir(tmp_path):
        Path("default.toml").write_text('[restic.notify]\nchannels = ["ntfy"]\n')
        Path(".toml").write_text("# hand written\n")

        read_config("notify")

        assert Path(".toml").read_text() == "# hand written\n"


def test_an_empty_section_silences_the_warning_and_wins(tmp_path, capsys):
    """The documented way to say 'I know, leave it': an empty block."""
    with chdir(tmp_path):
        Path("default.toml").write_text('[restic.notify]\nchannels = ["ntfy"]\n')
        Path(".toml").write_text("[restic.notify]\n")

        assert read_config("notify") == {}
        assert capsys.readouterr().out == ""


def test_warns_once_per_section(tmp_path, capsys):
    with chdir(tmp_path):
        Path("default.toml").write_text("[restic.notify]\na = 1\n[restic.plugins]\nb = 2\n")
        Path(".toml").write_text("")

        read_config("notify")
        read_config("notify")
        read_config("plugins")

        out = capsys.readouterr().out
        assert out.count("has no [restic.notify]") == 1
        assert out.count("has no [restic.plugins]") == 1


def test_nested_keys(tmp_path):
    with chdir(tmp_path):
        Path(".toml").write_text('[restic.notify.ntfy]\nevents = ["backup.failed"]\n')

        assert read_config("notify", "ntfy") == {"events": ["backup.failed"]}


def test_malformed_toml_warns_but_is_not_fatal(tmp_path, capsys):
    """A syntax error in .toml must not stop a backup, but must not pass silently either."""
    with chdir(tmp_path):
        Path(".toml").write_text("this is [not valid toml")

        assert read_config("notify") == {}
        assert "is not valid toml and was ignored" in capsys.readouterr().out
