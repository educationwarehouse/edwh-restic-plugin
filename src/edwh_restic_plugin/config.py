"""
Reading `[restic.*]` sections from `.toml`, with `default.toml` as the tracked template.

`default.toml` is committed; `.toml` is gitignored and per-project. A project's tuned settings
must not silently change when the template does, so `.toml` is *frozen* once it exists.

Where `forget.py` implements that freeze by copying the template into `.toml` on first read, this
module warns instead. Same guarantee, no invisible write: the template's value is used for this
run only, and adopting or pinning it stays an explicit act.
"""

import typing
from pathlib import Path

import tomlkit
import tomlkit.exceptions
from termcolor import cprint

DEFAULT_TOML = Path("default.toml")
PROJECT_TOML = Path(".toml")

_warned: set[str] = set()


def _read_section(path: Path, *keys: str) -> dict[str, typing.Any] | None:
    """Return the `[restic.<keys>]` table from a toml file, or None if absent/unreadable."""
    try:
        data = tomlkit.parse(path.read_text())
    except OSError:
        return None
    except tomlkit.exceptions.ParseError as e:
        # Loud but not fatal. Silently ignoring a syntax error would disable whatever it
        # configured -- notifications, most likely -- without anyone noticing.
        cprint(f"warning: {path} is not valid toml and was ignored: {e}", color="yellow")
        return None

    section: typing.Any = data
    for key in keys:
        if not isinstance(section, dict) or key not in section:
            return None
        section = section[key]

    return dict(section) if isinstance(section, dict) else None


def read_config(
    *keys: str,
    project_toml: Path | None = None,
    default_toml: Path | None = None,
    warn: bool = True,
) -> dict[str, typing.Any]:
    """Read a `[restic.<keys>]` table, falling back to default.toml without writing to .toml.

    Returns an empty dict when neither file defines the section, so callers can treat "absent"
    and "empty" alike -- both mean "nothing configured".
    """
    project_toml = PROJECT_TOML if project_toml is None else project_toml
    default_toml = DEFAULT_TOML if default_toml is None else default_toml
    dotted = ".".join(("restic", *keys))

    if (section := _read_section(project_toml, "restic", *keys)) is not None:
        return section

    if (template := _read_section(default_toml, "restic", *keys)) is None:
        return {}

    # The template has it and the project does not. Use it, say so once, write nothing.
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
