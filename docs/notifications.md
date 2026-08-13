# Notifications

A failed backup is silent unless something reports it, which matters most when it runs unattended
(cron, a systemd timer, CI). This plugin emits lifecycle events for `backup`, `restore`, `check`,
`forget` and `wipe` operations and hands them to notifier plugins.

Core ships **no** notifiers, only the interface, so no HTTP dependency is added to this package. A
notifier is a separate pip-installable package.

## Configuring channels

Secrets go in `.env`; routing goes in `default.toml`/`.toml` next to `[restic.forget]`:

```toml
[restic.notify]
channels = ["ntfy", "discord"]  # REQUIRED: nothing is sent unless a channel is named here
project = "acme-prod-db01"     # optional, defaults to the directory name
min_level = "warning"            # optional, default "info": info | warning | error
warn_after = ["30m", "2h"]        # optional, default ["30m", "2h"]: emit backup.slow at each

[restic.notify.ntfy]              # one table per channel, named after the notifier
topic = "acme-backups"       # plugin-specific: whatever this notifier's from_config reads
events = ["*"]                # optional, defaults to the notifier's own `subscribes`
min_level = "info"               # optional, overrides the global floor for this channel

[restic.notify.discord]
username = "backup-bot"           # plugin-specific option, not a secret
events = ["backup.failed", "check.failed"]   # humans get only the bad news

[restic.notify.targets.stream]    # optional per-target watchdog override (backup and restore)
warn_after = ["4h"]               # pg dumps are legitimately slow
```

Put secrets in `.env`, using the channel name as the prefix:

```dotenv
NTFY_TOKEN = "..."
DISCORD_WEBHOOK_URL = "..."
```

Inside a channel table, **`events` and `min_level` are the only keys core reads**. Everything else
is handed to that notifier untouched, so `topic` and `username` above are entirely their own
business. Credentials do not belong here: put them in `.env` and read them from the `env` argument
of `from_config`.

That `env` argument is **namespaced**, not the whole file. A notifier registered as `ntfy` receives
only the `NTFY_*` keys, prefix included, so one channel is never handed another channel's token or
restic's password. A plugin can still reach `os.environ` itself; this makes the obvious path the
narrow one rather than pretending to be a sandbox.

`project` identifies the *deployment*, so several of them can share one channel and still be told
apart. `host` says which machine an event came from; `project` says which project on it. Set it when
the directory name is not distinctive enough to read in a notification.

**Activation is explicit.** A notifier runs only if `channels` names it, so installing a package
sends nothing until you wire it up. A channel that is named but not provisioned yet (no token in
`.env`) is skipped with a note rather than treated as an error.

A project can ship `default.toml` as a tracked template. `.toml` is gitignored and per-project. If
`.toml` does not exist and `default.toml` does, it is copied from `default.toml`; once `.toml`
exists it is yours and is never rewritten. A section the template has and your `.toml` lacks
produces a warning telling you to copy the block to adopt the defaults, or to add an empty block to
keep current behaviour and silence the warning.

## Events

Event names are `<operation>.<phase>`. Operations are `backup`, `restore`, `check`, `forget` and
`wipe`; phases are `started`, `succeeded`, `failed` and `slow`.

| Event              | Level    | Notes                                                            |
|--------------------|----------|------------------------------------------------------------------|
| `backup.started`   | info     | Arms the hang watchdog.                                          |
| `backup.succeeded` | info     | Carries the backup target and message.                           |
| `backup.failed`    | error    | Failure logs identify failed `captain-hooks` scripts and exits.  |
| `backup.slow`      | warning  | Still running after `warn_after`. Does not kill anything.        |
| `check.failed`     | error    | The one you most want to reach a human.                          |
| `forget.succeeded` | info     | Carries the policy, when given explicitly.                       |
| `wipe.*`           | by phase | Destructive but user-initiated.                                  |

`restore.*`, `check.*` and `forget.*` follow the same shape. `configure` and `snapshots` emit
nothing. `snapshots` is read-only; `configure` is interactive and writes `.env` and initializes the
repository.

Operational filtering is uniform: normal events never bypass `min_level` or a channel's `events`
list. `notify-test --force` is the deliberate exception.

## Trying your channels out

`restic.notify-test` sends a synthetic event to your **real** notifiers, so you can check a channel
works without waiting for a genuine failure or forcing one.

```console
edwh restic.notify-test                              # backup.failed to every configured channel
edwh restic.notify-test --event check.failed
edwh restic.notify-test --channel discord            # just one channel
edwh restic.notify-test --force                      # ignore events/min_level filtering
edwh restic.notify-test --all-events                 # every operation.phase in turn
edwh restic.notify-test --connection s3              # resolve the repo, so events carry its name
```

Nothing is faked except the event: it goes through the same dispatcher and timeout as a real one;
`--force` deliberately skips routing.

Two things worth knowing:

- **Every drill is marked `NOTIFY-TEST`** in the fields a channel displays, so nobody woken at 3am
  has to work out whether it was real.
- **Channels filtered out by routing are reported**, not skipped silently. If `discord` only
  subscribes to `backup.failed` and you send `check.succeeded`, you are told it was filtered rather
  than left wondering whether the plugin is broken. `--force` sends anyway, which separates "this
  channel is broken" from "my routing does not match".

The task exits non-zero if nothing could be delivered, so it is usable as a provisioning check.

## Writing a notifier

One decorator, one classmethod, one `send`:

```python
from typing import Any, Mapping, Self

import requests
from edwh_restic_plugin.plugins import (
    BackupEvent,
    CheckEvent,
    Event,
    Failed,
    Notifier,
    register_notifier,
)


@register_notifier("mywebhook")
class MyWebhook(Notifier):
    contract = 1  # the contract version you wrote against, as a literal

    def __init__(self, url: str, secret: str) -> None:
        self.url = url
        self.secret = secret

    @classmethod
    def from_config(cls, env: Mapping[str, str], options: Mapping[str, Any]) -> Self | None:
        url = env.get("MYWEBHOOK_URL")  # from .env, MYWEBHOOK_* only
        secret = env.get("MYWEBHOOK_SECRET")  # from .env, MYWEBHOOK_* only
        if not (url and secret):
            return None  # named but unprovisioned -> inactive
        return cls(url, secret)

    def format(self, event: Event) -> str:
        match event:
            case BackupEvent(status=Failed(exit_code=code, logs=logs)):
                return f"{event.repo_display} backup failed (exit {code})\n{logs or ''}"
            case CheckEvent(status=Failed()):
                return f"REPOSITORY DAMAGED: {event.repo_display}"
            case _:
                return super().format(event)

    def send(self, event: Event) -> None:
        requests.post(
            self.url,
            headers={"X-Webhook-Secret": self.secret},
            json={"event": event.name, "level": event.level, "text": self.format(event)},
            timeout=10,
        )
```

Declare one entry point in your **`pyproject.toml`** so it is discovered:

```toml
[project.entry-points.edwh_restic_plugin]
mywebhook = "my_package"
```

One group covers both kinds of plugin: which registry you land in is decided by the decorator you
used, not by the group you declare, so a repository cannot be filed under notifiers by mistake.

You do not write config parsing, discovery, timeout handling or exception handling; core does all of
it. `from_config` receives your `.env` keys and the resolved `[restic.notify.<name>]` table, so a
notifier never opens `.toml` itself.

Notes:

- **A notifier cannot break a backup.** Exceptions are caught and logged, each `send` has a
  wall-clock timeout, and the process exit code reflects the backup rather than the telemetry about
  it. Setting `timeout=` on your own request is still worth doing, so a hung call fails cleanly
  instead of being abandoned.
- **`send` may receive event names it does not know.** Adding an operation is an additive change, so
  a `*` subscriber must tolerate unknown names.
- **Declare `contract` as a literal**, not by importing `CONTRACT_VERSION`. Reading ours would claim
  compatibility with whatever version you happen to be installed beside, which is what the check
  exists to catch. Core accepts a range, so an additive bump does not orphan you.
- **Type checking works across the boundary:** the package ships `py.typed`, and every public name
  is importable from `edwh_restic_plugin.plugins`.

## Security note

A notifier runs in-process and is trusted exactly like any other dependency. Events themselves are
built from an allowlist of fields and never include the repository URI (several backends embed
credentials in it) or the environment. But **`logs` on a failure event may carry restic or script
output**, which can include repository paths and hostnames, so point channels carrying failure
events somewhere you would be comfortable pasting a terminal session.
