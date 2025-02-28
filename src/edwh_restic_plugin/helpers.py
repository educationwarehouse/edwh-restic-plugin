import sys
import typing

import invoke
from edwh.tasks import require_sudo

T = typing.TypeVar("T")


def fix_tags(tags: typing.Iterable[T | None]) -> list[T]:
    """
    Removes all None type elements from the input list.

    :param tags: list of strings, some elements may be None
    :return: list of strings with all None elements removed
    """
    return [tag for tag in tags if tag is not None]


def camel_to_snake(s: str) -> str:
    return "".join([f"_{c.lower()}" if c.isupper() else c for c in s]).lstrip("_")


def _require_restic(c: invoke.Context = None) -> bool:
    """
    Checks if 'restic' is installed in the system. If not, it installs 'restic' using the 'apt' package manager
    and updates it to the latest version. The function returns False if 'restic' is already installed,
    and True after successfully installing and updating 'restic'.

    :param c: An optional Invoke context. If not provided, a new context will be created.
    :return: False if 'restic' is already installed, True otherwise.
    """
    c = c or invoke.Context()  # type: invoke.Context
    if c.run("which restic", warn=True, hide=True).ok:
        # restic already exists, do nothing
        return False

    if not require_sudo(c):
        return False

    # sudo available
    print("Restic missing from this system! Installing now...", file=sys.stderr)
    c.sudo("apt install -y restic", hide=True)
    c.sudo("restic self-update", hide=True)
    print("Restic installed and updated!", file=sys.stderr)
    return True
