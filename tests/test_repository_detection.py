import pytest

from src.edwh_restic_plugin.repositories import Repository, register, registrations


@pytest.fixture(autouse=True)
def _clear_prio():
    """Empty the registry and put discovery under the test's control.

    Marking it discovered-but-empty means listing the registry will not go and import the real
    repositories, so a test can register doubles and see only those. Tests that *want* the real
    ones ask for them explicitly, which also keeps them deterministic when a third-party
    repository plugin happens to be installed in the dev environment.
    """
    registrations.clear()
    registrations.discover(in_package=False, entry_points=False, config=False)


def test_basics():
    with pytest.raises(SyntaxError):
        # @register() without ()
        @register
        class Invalid: ...

    with pytest.raises(TypeError):
        # not a Repository
        @register()
        class Invalid: ...

    with pytest.raises(TypeError):
        # forgot to implement abc methods
        @register()
        class Invalid(Repository): ...

        Invalid()


def test_priority():
    class DummyRepostiory(Repository):
        def setup(self):
            pass

        def prepare_for_restic(self, ctx):
            pass

    @register()
    class LowPrio1(DummyRepostiory): ...

    @register("high_prio", priority=1)
    class HighPrioRepository(DummyRepostiory): ...

    @register(priority=-1)
    class LowPrio2(DummyRepostiory): ...

    regs = list(registrations)

    assert len(regs) == 3
    assert regs[0] == HighPrioRepository

    as_dict = registrations.to_ordered_dict()
    assert len(as_dict) == 3

    for item in as_dict:
        assert item == "high_prio"
        assert as_dict[item] == HighPrioRepository
        break

    assert HighPrioRepository._short_name == "high_prio"


def test_detection():
    # in-package only: entry points and [restic.plugins] would make this depend on whatever is
    # installed in the environment running the suite.
    registrations.discover(entry_points=False, config=False)
    regs = registrations.to_ordered_dict()

    assert len(regs) > 3

    assert next(iter(regs.keys())) == "hetzner"


def test_get_triggers_discovery():
    """Regression test: get() used to read _aliases directly.

    _aliases is only filled by push(), which only runs during discovery, so get() returned None
    for a correctly registered repository unless some earlier call happened to trigger discovery.
    It worked by accident because cli_repo calls to_ordered_dict() first.
    """
    assert registrations.get("local") is None  # nothing discovered yet, by the fixture

    registrations.clear()
    assert registrations.get("local") is not None


def test_rediscovery_is_idempotent():
    """clear() + discover() must re-register, not silently produce an empty registry.

    import_module is a no-op for an already-imported module, so this only holds because
    discovery reloads.
    """
    registrations.discover(entry_points=False, config=False)
    first = set(registrations.to_ordered_dict())

    registrations.clear()
    registrations.discover(entry_points=False, config=False)
    second = set(registrations.to_ordered_dict())

    assert first == second
    assert len(second) > 3


def test_broken_plugin_is_skipped_not_fatal(capsys):
    """A broken notification plugin must not prevent a backup."""
    registrations._import("edwh_restic_plugin_does_not_exist")

    assert "could not be loaded and was skipped" in capsys.readouterr().err
