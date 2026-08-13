"""The documented import path must actually work.

Regression test: `Notifier` and `register_notifier` were missing from the facade, because
notify.py imported from plugins.py and the re-export would have been circular. Every example in
the README says `from edwh_restic_plugin.plugins import ...`, so a plugin author would have hit
an ImportError on the first line. The layering is now
registry -> events/notify/repositories -> plugins.
"""

import importlib

import pytest

MODULE = "src.edwh_restic_plugin.plugins"

#: Everything the README and the design document tell a plugin author to import.
DOCUMENTED = [
    "CONTRACT_VERSION",
    "BackupEvent",
    "BasicEvent",
    "CheckEvent",
    "Event",
    "Failed",
    "ForgetEvent",
    "Notifier",
    "Repository",
    "RestoreEvent",
    "ScriptFailure",
    "Slow",
    "Started",
    "Status",
    "Succeeded",
    "UnsupportedOperation",
    "WipeEvent",
    "register",
    "register_notifier",
]


@pytest.mark.parametrize("name", DOCUMENTED)
def test_documented_name_is_importable_from_the_facade(name):
    module = importlib.import_module(MODULE)

    assert hasattr(module, name), f"{name} is documented but not exported from {MODULE}"


def test_all_matches_what_is_actually_exported():
    module = importlib.import_module(MODULE)

    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert missing == [], f"__all__ promises names the module does not have: {missing}"


def test_everything_documented_is_in_all():
    module = importlib.import_module(MODULE)

    assert set(DOCUMENTED) - set(module.__all__) == set()


def test_the_facade_holds_no_logic():
    """It exists to be stable, so it must have nothing worth changing.

    If this fails, something was implemented in the facade instead of behind it.
    """
    import inspect

    module = importlib.import_module(MODULE)
    defined_here = [
        name
        for name, value in vars(module).items()
        if (inspect.isfunction(value) or inspect.isclass(value)) and getattr(value, "__module__", "") == module.__name__
    ]

    assert defined_here == [], f"{MODULE} should only re-export, but defines: {defined_here}"
