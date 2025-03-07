import pytest

from src.edwh_restic_plugin.repositories import Repository, register, registrations


@pytest.fixture()
def clear_prio():
    registrations.clear()


def test_basics(clear_prio):
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


def test_priority(clear_prio):
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


def test_detection(clear_prio):
    regs = registrations.to_ordered_dict()

    assert len(regs) > 3

    assert next(iter(regs.keys())) == "os"
