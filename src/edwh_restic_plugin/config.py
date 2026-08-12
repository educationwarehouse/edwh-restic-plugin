"""
Reading `[restic.*]` sections from `.toml`, with `default.toml` as the tracked template.

`default.toml` is committed; `.toml` is gitignored and per-project. If `.toml` does not exist yet
it is copied from `default.toml`; once it exists it is frozen, so a project's tuned settings never
change because the template did. A section the template has and `.toml` lacks produces a warning
rather than a write.
"""

import shutil
import typing as t
from pathlib import Path

import tomlkit
import tomlkit.exceptions
from termcolor import cprint

DEFAULT_TOML = Path("default.toml")
PROJECT_TOML = Path(".toml")

_warned: set[str] = set()


def _read_section(path: Path, *keys: str) -> dict[str, t.Any] | None:
    """Return the `[restic.<keys>]` table from a toml file, or None if absent or unreadable."""
    try:
        data = tomlkit.parse(path.read_text())
    except OSError:
        return None
    except tomlkit.exceptions.ParseError as e:
        # Loud but not fatal: silently ignoring a syntax error would disable whatever it
        # configured, most likely notifications, without anyone noticing.
        cprint(f"warning: {path} is not valid toml and was ignored: {e}", color="yellow")
        return None

    section: t.Any = data
    for key in keys:
        if not isinstance(section, dict) or key not in section:
            return None
        section = section[key]

    return dict(section) if isinstance(section, dict) else None


def ensure_project_toml(project_toml: Path = PROJECT_TOML, default_toml: Path = DEFAULT_TOML) -> bool:
    """Copy `default.toml` to `.toml` if the project has no `.toml` yet.

    Returns True if a copy was made. This is the "customise per project" step: the copy is the
    only write, and after it `.toml` is yours.
    """
    if project_toml.exists() or not default_toml.exists():
        return False

    shutil.copyfile(default_toml, project_toml)
    cprint(f"note: created {project_toml} from {default_toml}; edit it to customise this project.", color="green")
    return True


def read_config(
    *keys: str,
    project_toml: Path = PROJECT_TOML,
    default_toml: Path = DEFAULT_TOML,
    warn: bool = True,
) -> dict[str, t.Any]:
    """Read a `[restic.<keys>]` table.

    Returns an empty dict when neither file defines the section, so callers can treat "absent" and
    "empty" alike: both mean nothing is configured.
    """
    ensure_project_toml(project_toml, default_toml)
    dotted = ".".join(("restic", *keys))

    if (section := _read_section(project_toml, "restic", *keys)) is not None:
        return section

    if (template := _read_section(default_toml, "restic", *keys)) is None:
        return {}

    # `.toml` exists but predates this section. Use the template's values for this run and say so;
    # writing them would silently un-freeze a file the user owns.
    if warn and dotted not in _warned:
        _warned.add(dotted)
        cprint(
            f"warning: {project_toml} has no [{dotted}], but {default_toml} does. "
            f"Using the {default_toml} values for this run.\n"
            f"  - copy the [{dotted}] block into {project_toml} to adopt and freeze them\n"
            f"  - add an empty [{dotted}] to keep current behaviour and silence this",
            color="yellow",
        )

    return template


def reset_warnings() -> None:
    """Forget which sections have already warned. For tests."""
    _warned.clear()
