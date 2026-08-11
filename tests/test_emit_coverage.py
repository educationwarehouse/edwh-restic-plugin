"""Every operation must have an emit site, and every phase must be reachable.

An operation can be defined, registered and present in the Event union yet never constructed by
anything -- the taxonomy then promises an event that never fires. Neither __init_subclass__ nor the
union test can see that, so it is asserted here.
"""

import ast
import inspect
from pathlib import Path

from src.edwh_restic_plugin import tasks
from src.edwh_restic_plugin.events import ALL_PHASES, BasicEvent


def _operations_passed_to_repo_context() -> set[str]:
    """The second positional argument of every repo_context(...) call in tasks.py."""
    source = Path(inspect.getsourcefile(tasks)).read_text()
    found = set()

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "repo_context" or len(node.args) < 2:
            continue

        event_class = node.args[1]
        if isinstance(event_class, ast.Name):
            found.add(event_class.id)

    return found


def test_every_operation_has_an_emit_site():
    emitted = _operations_passed_to_repo_context()
    declared = {cls.__name__ for cls in BasicEvent.operations.values()}

    assert declared - emitted == set(), f"declared but never emitted: {declared - emitted}"


def test_every_emit_site_uses_a_real_operation():
    emitted = _operations_passed_to_repo_context()
    declared = {cls.__name__ for cls in BasicEvent.operations.values()}

    assert emitted - declared == set(), f"emitted but not a registered operation: {emitted - declared}"


def test_repo_context_can_produce_every_phase():
    """Coverage is structural: repo_context emits started/succeeded/failed and arms the watchdog
    for slow, so any operation it is given reaches all four phases."""
    source = inspect.getsource(tasks.repo_context)

    assert "Started()" in source
    assert "Succeeded(" in source
    assert "Failed(" in source
    assert "Watchdog(" in source  # Slow comes from the watchdog, not the context manager
    assert set(ALL_PHASES) == {"started", "succeeded", "failed", "slow"}
