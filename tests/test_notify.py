"""The test suite is the consumer of the notifier contract.

Core ships no notifiers, and an extension API with no in-tree consumer drifts out of sync with its
own documentation. MemoryNotifier registers through the *identical* decorator an external package
uses, so a breaking contract change fails CI rather than a user.
"""

import datetime
import time

import pytest

from src.edwh_restic_plugin.events import BackupEvent, CheckEvent, Failed, Slow, Started, Succeeded
from src.edwh_restic_plugin.notify import (
    Channel,
    Dispatcher,
    Emitter,
    Notifier,
    build_channels,
    notifiers,
    register_notifier,
)
from src.edwh_restic_plugin.registry import CONTRACT_VERSION, MIN_SUPPORTED_CONTRACT


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Keep notifiers registered at import, discard ones a test registers.

    Clearing outright would also drop MemoryNotifier below, and re-registering it per test would
    make the fixture depend on declaration order. Snapshot and restore instead.
    """
    saved = (list(notifiers._queue), dict(notifiers._aliases), notifiers._discovered)
    # Nothing to find: core ships no notifiers, and entry points would depend on what happens to
    # be installed in the environment running the suite.
    notifiers._discovered = True
    yield
    notifiers._queue, notifiers._aliases, notifiers._discovered = saved


def make(cls=BackupEvent, status=None, level="info", **kwargs):
    return cls(
        status=status or Started(),
        ts=datetime.datetime(2026, 1, 1),
        level=level,
        repo="s3",
        repo_display="s3:acme",
        host="db-01",
        project="acme",
        **kwargs,
    )


@register_notifier("memory")
class MemoryNotifier(Notifier):
    """Records what it was sent. The in-tree consumer of the contract."""

    seen: list = []

    @classmethod
    def from_config(cls, _env, _options):
        instance = cls()
        instance.received = []
        return instance

    def send(self, event):
        self.received.append(event)
        MemoryNotifier.seen.append(event)


# --- registration ---------------------------------------------------------------------------


def test_registration_requires_parentheses():
    with pytest.raises(SyntaxError):

        @register_notifier
        class Invalid(Notifier): ...


def test_registration_requires_a_notifier_subclass():
    with pytest.raises(TypeError):

        @register_notifier()
        class Invalid: ...


def test_short_name_is_derived_from_the_class_name():
    @register_notifier()
    class SlackNotifier(Notifier):
        def send(self, _event):
            pass

    assert SlackNotifier._short_name == "slack"


# --- activation is explicit -----------------------------------------------------------------


def test_a_notifier_is_inactive_until_channels_names_it():
    """Installing a package must not start sending anything."""
    assert build_channels(env={}, config={}) == []


def test_naming_a_channel_activates_it():
    channels = build_channels(env={}, config={"channels": ["memory"]})

    assert len(channels) == 1
    assert channels[0].notifier._short_name == "memory"


def test_an_unknown_channel_warns_and_is_skipped(capsys):
    channels = build_channels(env={}, config={"channels": ["nope"]})

    assert channels == []
    assert "which is not installed" in capsys.readouterr().out


def test_returning_none_from_from_config_stays_inactive(capsys):
    """A named but unprovisioned channel: 'not yet', not an error."""

    @register_notifier("unprovisioned")
    class Unprovisioned(Notifier):
        @classmethod
        def from_config(cls, _env, _options):
            return None if not _env.get("UNPROVISIONED_TOKEN") else cls()

        def send(self, _event):
            pass

    assert build_channels(env={}, config={"channels": ["unprovisioned"]}) == []
    assert "not configured yet" in capsys.readouterr().out

    assert len(build_channels(env={"UNPROVISIONED_TOKEN": "x"}, config={"channels": ["unprovisioned"]})) == 1


def test_a_notifier_that_raises_during_configure_is_skipped(capsys):
    @register_notifier("explodes")
    class Explodes(Notifier):
        @classmethod
        def from_config(cls, _env, _options):
            raise RuntimeError("bad config")

        def send(self, _event):
            pass

    assert build_channels(env={}, config={"channels": ["explodes"]}) == []
    assert "failed to configure" in capsys.readouterr().out


def test_a_contract_outside_the_supported_range_is_skipped(capsys):
    """Checked here, not mid-backup: an AttributeError at 04:00 in cron is a bad way to learn it."""

    @register_notifier("too_new")
    class TooNew(Notifier):
        contract = CONTRACT_VERSION + 1

        def send(self, _event):
            pass

    assert build_channels(env={}, config={"channels": ["too_new"]}) == []
    assert "outside the supported range" in capsys.readouterr().out


def test_an_older_but_still_supported_contract_is_accepted():
    """A notifier need not track every additive bump to keep working."""

    @register_notifier("older")
    class Older(Notifier):
        contract = MIN_SUPPORTED_CONTRACT

        def send(self, _event):
            pass

    assert len(build_channels(env={}, config={"channels": ["older"]})) == 1


def test_a_contract_below_the_floor_is_skipped(capsys):
    @register_notifier("ancient")
    class Ancient(Notifier):
        contract = MIN_SUPPORTED_CONTRACT - 1

        def send(self, _event):
            pass

    assert build_channels(env={}, config={"channels": ["ancient"]}) == []
    assert "outside the supported range" in capsys.readouterr().out


# --- routing --------------------------------------------------------------------------------


def test_events_pattern_filters():
    channel = Channel(MemoryNotifier(), ("backup.failed", "check.*"), "info")

    assert channel.wants(make(BackupEvent, Failed(duration=1.0, exit_code=1), level="error"))
    assert not channel.wants(make(BackupEvent, Succeeded(duration=1.0)))
    assert channel.wants(make(CheckEvent, Succeeded(duration=1.0)))


def test_min_level_filters():
    channel = Channel(MemoryNotifier(), ("*",), "warning")

    assert not channel.wants(make(level="info"))
    assert channel.wants(make(level="warning"))
    assert channel.wants(make(level="error"))


def test_no_event_bypasses_filtering():
    """Uniform by decision: an event that ignores the config it appears to obey is its own bug."""
    channel = Channel(MemoryNotifier(), ("backup.*",), "error")

    from src.edwh_restic_plugin.events import WipeEvent

    assert not channel.wants(make(WipeEvent, Started(), level="warning"))


def test_per_channel_events_override_subscribes():
    @register_notifier("narrow")
    class Narrow(Notifier):
        subscribes = ("check.*",)

        def send(self, _event):
            pass

    from_default = build_channels(env={}, config={"channels": ["narrow"]})
    assert from_default[0].events == ("check.*",)

    overridden = build_channels(env={}, config={"channels": ["narrow"], "narrow": {"events": ["backup.failed"]}})
    assert overridden[0].events == ("backup.failed",)


# --- dispatch never affects the caller ------------------------------------------------------


def test_dispatch_delivers_to_matching_channels():
    notifier = MemoryNotifier()
    notifier.received = []
    dispatcher = Dispatcher([Channel(notifier, ("*",), "info")])

    dispatcher.dispatch(make())

    assert len(notifier.received) == 1


def test_a_raising_notifier_does_not_reach_the_caller(capsys):
    class Boom(Notifier):
        _short_name = "boom"

        def send(self, _event):
            raise RuntimeError("webhook down")

    dispatcher = Dispatcher([Channel(Boom(), ("*",), "info")])
    dispatcher.dispatch(make())  # must not raise

    assert "raised" in capsys.readouterr().out


def test_a_hanging_notifier_is_abandoned(capsys):
    """The dispatcher owns the timeout, not the notifier's transport.

    A plugin author who forgets timeout= on a requests.post would otherwise hang the backup.
    """

    class Hangs(Notifier):
        _short_name = "hangs"

        def send(self, _event):
            time.sleep(30)

    dispatcher = Dispatcher([Channel(Hangs(), ("*",), "info")], timeout=0.1)

    started = time.monotonic()
    dispatcher.dispatch(make())
    elapsed = time.monotonic() - started

    assert elapsed < 5, "dispatch waited for the hanging notifier"
    assert "timed out" in capsys.readouterr().out


def test_one_broken_channel_does_not_block_the_others():
    class Boom(Notifier):
        _short_name = "boom"

        def send(self, _event):
            raise RuntimeError("down")

    good = MemoryNotifier()
    good.received = []
    dispatcher = Dispatcher([Channel(Boom(), ("*",), "info"), Channel(good, ("*",), "info")])

    dispatcher.dispatch(make())

    assert len(good.received) == 1


# --- the default format covers every phase --------------------------------------------------


class _Concrete(Notifier):
    """Notifier is abstract, so exercising the inherited format() needs a concrete subclass."""

    _short_name = "concrete"

    def send(self, _event):
        pass


@pytest.mark.parametrize(
    "status",
    [
        Started(),
        Succeeded(duration=12.0),
        Failed(duration=3.0, exit_code=7),
        Slow(elapsed=120.0, threshold=60.0),
    ],
)
def test_default_format_handles_every_phase(status):
    rendered = _Concrete().format(make(BackupEvent, status))

    assert "s3:acme" in rendered
    assert status.phase in rendered


# --- the emitter ----------------------------------------------------------------------------


def test_emitter_fills_the_allowlisted_fields():
    notifier = MemoryNotifier()
    notifier.received = []
    emitter = Emitter(
        BackupEvent,
        None,
        "s3",
        {"target": "files"},
        Dispatcher([Channel(notifier, ("*",), "info")]),
    )

    emitter.emit(Succeeded(duration=4.0))

    event = notifier.received[0]
    assert event.name == "backup.succeeded"
    assert event.target == "files"
    assert event.repo == "s3"
    assert event.repo_display == "s3"  # no Repository resolved, so it falls back to the name
    assert event.level == "info"


def test_emitter_update_carries_detail_discovered_mid_operation():
    notifier = MemoryNotifier()
    notifier.received = []
    emitter = Emitter(BackupEvent, None, "s3", {}, Dispatcher([Channel(notifier, ("*",), "info")]))

    emitter.update(snapshot="abc123")
    emitter.emit(Succeeded(duration=1.0))

    assert notifier.received[0].snapshot == "abc123"


def test_emitter_uses_display_name_not_the_uri():
    """The raw uri can embed credentials; display_name is the safe substitute."""

    class FakeRepo:
        _short_name = "sftp"

        @property
        def display_name(self):
            return "sftp:backups"

        @property
        def uri(self):
            return "sftp:user:hunter2@host:/backups"

    notifier = MemoryNotifier()
    notifier.received = []
    emitter = Emitter(BackupEvent, FakeRepo(), "sftp", {}, Dispatcher([Channel(notifier, ("*",), "info")]))

    emitter.emit(Started())

    event = notifier.received[0]
    assert event.repo_display == "sftp:backups"
    assert "hunter2" not in repr(event)


# --- env is namespaced per channel ----------------------------------------------------------


def test_a_channel_only_sees_its_own_env_keys():
    """So one channel is not handed another's token, nor restic's password."""
    seen = {}

    @register_notifier("scoped")
    class Scoped(Notifier):
        @classmethod
        def from_config(cls, env, _options):
            seen.update(env)
            return cls()

        def send(self, _event):
            pass

    build_channels(
        env={
            "SCOPED_TOKEN": "mine",
            "SCOPED_URL": "https://mine",
            "OTHER_TOKEN": "not mine",
            "RESTIC_PASSWORD": "definitely not mine",
        },
        config={"channels": ["scoped"]},
    )

    assert seen == {"SCOPED_TOKEN": "mine", "SCOPED_URL": "https://mine"}
