import dataclasses
import datetime
import typing

import pytest

from src.edwh_restic_plugin.events import (
    ALL_PHASES,
    BackupEvent,
    BasicEvent,
    CheckEvent,
    Event,
    Failed,
    ForgetEvent,
    Phase,
    Slow,
    Started,
    Succeeded,
    level_for,
    matches,
)


def make(cls=BackupEvent, status=None, **kwargs):
    return cls(
        status=status or Started(),
        ts=datetime.datetime(2026, 1, 1),
        level="info",
        repo="s3",
        repo_display="s3:acme-backups",
        host="db-01",
        project="acme",
        **kwargs,
    )


# --- the union and the registry must agree -------------------------------------------------


def test_union_matches_the_classes():
    """Orphan class, or junk in the union.

    Event has to be a statically written alias because no type checker can follow a computed
    union, so something must assert the hand-written half matches the self-maintaining half.
    """
    static = set(typing.get_args(Event))
    runtime = set(BasicEvent.operations.values())

    assert static == runtime, {
        "defined but missing from Event": runtime - static,
        "in Event but not an operation": static - runtime,
    }


def test_duplicate_operation_is_an_import_time_error():
    """A definition-time property should fail when you write the class, not in a test run."""
    with pytest.raises(TypeError, match="reuses operation 'backup'"):

        @dataclasses.dataclass(frozen=True, kw_only=True)
        class Clashing(BasicEvent):
            operation: typing.ClassVar[str] = "backup"


def test_a_subclass_without_its_own_operation_is_not_registered():
    """So intermediate bases and plugin subclasses can exist without hijacking a name."""
    before = dict(BasicEvent.operations)

    @dataclasses.dataclass(frozen=True, kw_only=True)
    class Extended(BackupEvent):
        mine: int = 1

    assert BasicEvent.operations == before
    assert Extended.operation == "backup"


# --- names, levels, matching ---------------------------------------------------------------


def test_name_is_derived_from_operation_and_phase():
    assert make(BackupEvent, Started()).name == "backup.started"
    assert make(CheckEvent, Failed(duration=1.0, exit_code=1)).name == "check.failed"
    assert make(ForgetEvent, Succeeded(duration=2.0)).name == "forget.succeeded"


def test_level_follows_the_phase():
    """Uniform across operations: no operation gets a special level."""
    assert level_for("started") == "info"
    assert level_for("succeeded") == "info"
    assert level_for("failed") == "error"
    assert level_for("slow") == "warning"


def test_all_phases_is_derived_from_the_literal():
    """So the tuple cannot drift from the type."""
    assert set(ALL_PHASES) == set(typing.get_args(Phase))


@pytest.mark.parametrize(
    ("pattern", "name", "expected"),
    [
        ("*", "backup.failed", True),
        ("backup.*", "backup.failed", True),
        ("backup.*", "restore.failed", False),
        ("backup.failed", "backup.failed", True),
        ("backup.failed", "backup.succeeded", False),
        # deliberately not fnmatch: a future operation named backup_verify must not match
        ("backup.*", "backup_verify.failed", False),
    ],
)
def test_matches(pattern, name, expected):
    assert matches(pattern, name) is expected


# --- the phase axis carries exact fields ---------------------------------------------------


def test_terminal_phases_require_their_fields():
    """exit_code is an int, not int | None: it exists on Failed and nowhere else."""
    with pytest.raises(TypeError):
        Failed()  # duration and exit_code are mandatory

    with pytest.raises(TypeError):
        Succeeded()  # duration is mandatory

    assert Started().phase == "started"  # started needs nothing


def test_slow_does_not_reuse_duration():
    """The operation has not finished, so calling it `duration` would be a lie."""
    slow = Slow(elapsed=90.0, threshold=60.0)

    assert not hasattr(slow, "duration")
    assert slow.elapsed == 90.0


def test_phase_literal_narrows_both_branches():
    """No accessor or overload needed -- ty and mypy narrow on status.phase directly."""
    event = make(BackupEvent, Failed(duration=3.0, exit_code=7, logs="boom"))

    if event.status.phase == "failed":
        assert event.status.exit_code == 7
    else:
        pytest.fail("should have narrowed to Failed")


def test_every_operation_reaches_every_phase():
    for operation, cls in BasicEvent.operations.items():
        for phase in ALL_PHASES:
            status = {
                "started": Started(),
                "succeeded": Succeeded(duration=1.0),
                "failed": Failed(duration=1.0, exit_code=1),
                "slow": Slow(elapsed=1.0, threshold=1.0),
            }[phase]
            assert make(cls, status).name == f"{operation}.{phase}"


# --- the allowlist -------------------------------------------------------------------------

SECRET_FIELDS = {"repo_uri", "uri", "env", "env_config", "password", "extra", "secret", "token"}


def test_no_event_exposes_a_credential_shaped_field():
    """The schema is an allowlist. A field nobody added must simply not be there."""
    for cls in BasicEvent.operations.values():
        fields = {f.name for f in dataclasses.fields(cls)}
        assert not (fields & SECRET_FIELDS), (cls.__name__, fields & SECRET_FIELDS)


def test_events_are_frozen():
    event = make()

    with pytest.raises(dataclasses.FrozenInstanceError):
        event.repo = "other"


def test_secret_values_never_appear_in_a_dispatched_event(monkeypatch):
    """Tripwire, not a scrubber.

    If the allowlist is right this never fires. If someone adds a careless field that pulls from
    the environment, it does. This is the correct home for blocklist logic -- an assertion, not a
    runtime filter that would mangle legitimate content and imply a guarantee it cannot make.
    """
    secret = "SUPERSECRETVALUE12345"
    for key in ("RESTIC_PASSWORD", "AWS_SECRET_ACCESS_KEY", "S3_PASSWORD", "AZURE_ACCOUNT_KEY"):
        monkeypatch.setenv(key, secret)

    for cls in BasicEvent.operations.values():
        for status in (
            Started(),
            Succeeded(duration=1.0),
            Failed(duration=1.0, exit_code=1, logs="restic failed"),
            Slow(elapsed=1.0, threshold=1.0),
        ):
            event = make(cls, status)
            rendered = repr(event) + str(event) + repr(dataclasses.asdict(event))
            assert secret not in rendered, f"{cls.__name__} leaked a secret via {status.phase}"
