# edwh-restic-plugin

[![PyPI - Version](https://img.shields.io/pypi/v/edwh-restic-plugin.svg)](https://pypi.org/project/edwh-restic-plugin)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/edwh-restic-plugin.svg)](https://pypi.org/project/edwh-restic-plugin)

`edwh-restic-plugin` adds `restic.*` subcommands to `edwh` for repository setup, backup/restore flows, retention, and maintenance.

## Table of contents

- [Installation](#installation)
- [CLI usage](#cli-usage)
- [Repository types](#repository-types)
- [Captain hooks scripts](#captain-hooks-scripts)
- [Commands](#commands)
- [Forget policy integration](#forget-policy-integration)
- [Wipe (destructive)](#wipe-destructive)
- [Integrity checks](#integrity-checks)
- [Notifications](#notifications)
- [Trying your channels out](#trying-your-channels-out)
- [Custom repository types](#custom-repository-types)
- [License](#license)

## Installation

```console
(uv) pip install edwh-restic-plugin
```

Or add it through the plugin manager:

```console
edwh plugin.add restic
```

Most users install it through `edwh` extras:

```console
uvenv install edwh[plugins,omgeving]
# or
uvenv install edwh[restic]
```

## CLI usage

Use `edwh` as the canonical CLI:

```console
edwh restic.backup --connection-choice local
```

`ew` is a valid shorthand for the same commands:

```console
ew restic.backup --connection-choice local
```

## Repository types

The plugin supports multiple backends through provider implementations in `src/edwh_restic_plugin/repositories`:

- `local`
- `sftp`
- `b2`
- `swift` (OpenStack Swift)
- `s3`
- `r2`
- `oracle`
- `hetzner`

If you omit connection selection, the plugin auto-detects based on configured `*_PASSWORD` variables and repository priority.

## Captain hooks scripts

Backup/restore scripts are discovered in `captain-hooks/`.

Expected naming:

- `backup_<target>*`
- `restore_<target>*`

Examples:

- `backup_files.sh`
- `backup_stream.sh`
- `restore_files.sh`
- `restore_stream.sh`

During execution, the plugin prepares environment variables commonly used by scripts:

- `HOST`
- `URI`
- `RESTIC_HOST`
- `RESTIC_REPOSITORY`
- `SNAPSHOT` (restore flows)
- `MSG` (backup message snapshot content)

Scripts can still call raw `restic ...` commands internally; the plugin prepares required env/auth context first.

## Commands

Note: connection option names differ across commands in current implementation.

### `restic.configure`

Set up/update repository env settings and run `restic init --repository-version 2`.

```console
edwh restic.configure --connection-choice local
edwh restic.configure --connection-choice sftp --restichostname my-host
```

Options:

- `--connection-choice`
- `--restichostname`

Aliases: `restic.setup`, `restic.init`

### `restic.backup`

Run backup scripts for a target.

```console
edwh restic.backup --connection-choice local --target files
edwh restic.backup --connection-choice sftp --target stream --message "nightly backup"
```

Options:

- `--target`
- `--connection-choice`
- `--message`
- `--verbose`
- `--without-forget` (skip automatic forget-policy run)

Behavior:

- Executes matching `captain-hooks/backup_<target>*` scripts.
- Stores a message snapshot (tag `message`) linked to created snapshots.
- Automatically runs `restic.forget` policy after backup when policy exists, unless `--without-forget` is set.

### `restic.restore`

Run restore scripts for a target and snapshot.

```console
edwh restic.restore --connection-choice local --target files --snapshot latest
edwh restic.restore --connection-choice sftp --target stream --snapshot <snapshot-id>
```

Options:

- `--connection-choice`
- `--snapshot` (default: `latest`)
- `--target`
- `--verbose`

### `restic.snapshots`

List snapshots (with parsed message-snapshot annotations).

```console
edwh restic.snapshots --connection-choice local
edwh restic.snapshots --connection-choice local --tag files --tag stream -n 5
```

Options:

- `--connection-choice`
- `--tag` (repeatable)
- `-n` / `--n`
- `--verbose`

Aliases: `restic.list`

### `restic.run`

Open an interactive shell with restic env prepared, or run one restic subcommand.

```console
edwh restic.run --connection-choice local
edwh restic.run --connection-choice local --command "snapshots --latest 3"
```

Options:

- `--connection-choice`
- `--command`

### `restic.env`

Print `export ...` lines for environment changes that the selected repository would apply.

```console
edwh restic.env --connection-choice local
```

Options:

- `--connection-choice`

### `restic.forget`

Run `restic forget` with policy and prune options.

```console
edwh restic.forget --connection s3
edwh restic.forget --connection s3 --dry
edwh restic.forget --connection s3 --policy "--keep-daily 7 --keep-weekly 5 --prune"
```

Options:

- `--connection`
- `--policy` (raw policy CLI string)
- `--dry`

### `restic.unlock`

Run `restic unlock`.

```console
edwh restic.unlock --connection sftp
edwh restic.unlock --connection sftp --remove-all
```

Options:

- `--connection`
- `--remove-all`

### `restic.du`

Run `restic stats` helper.

```console
edwh restic.du --connection local
edwh restic.du --connection local --mode raw-data
```

Options:

- `--connection`
- `--mode` (`restore-size`, `file-by-contents`, `blobs-per-file`, `raw-data`)

Aliases: `restic.stats`, `restic.stat`

## Forget policy integration

The plugin supports retention policy configuration in TOML files via `ResticForgetPolicy`.

Policy lookup order for automatic resolution:

1. Connection short name (for example `s3`)
2. Connection aliases
3. `default`

Configuration keys are read from sections like:

```toml
[restic.forget.default]
keep-daily = 7
keep-weekly = 5
prune = true

[restic.forget.s3]
keep-last = 10
prune = true
```

Common supported keys include:

- `keep-last`
- `keep-hourly`
- `keep-daily`
- `keep-weekly`
- `keep-monthly`
- `keep-yearly`
- `keep-tag` (list)
- `keep-within*` variants
- `prune`

Integration with `restic.backup`:

- After backup, if a policy is found, `forget` is executed automatically.
- Use `--without-forget` on backup to skip that post-backup retention step.

## Wipe (destructive)

`restic.wipe` is available and is intentionally interactive.

```console
edwh restic.wipe --connection s3
```

Behavior:

- The command asks for explicit confirmation:
  - `Type YES to wipe repository <...>:`
- Any response other than `YES` aborts the operation.
- S3-style backends share the generic wipe helper; other providers still implement their own config.

Use this only when you intentionally want to remove a repository's backup contents.

## Integrity checks

`restic.check` verifies that the repository itself is intact, which is otherwise something you find
out during a restore.

```console
edwh restic.check --connection s3               # structure only, cheap
edwh restic.check --connection s3 --subset 5%   # also read a 5% sample of the data
edwh restic.check --connection s3 --read-data   # read every byte (slow, full egress)
```

Options:

- `--connection`: repository to check; defaults to the one derived from `.env`.
- `--read-data`: verify every pack file. Re-downloads the whole repository, so on a cloud backend
  it pays full egress every run.
- `--subset`: read a sample instead (`5%`, `1G`, `2/8`). Restic picks a different sample each run,
  so a weekly `--subset 5%` converges on full coverage without paying for it at once. Ignored when
  `--read-data` is set.

## Notifications

A failed backup is silent unless something reports it, which matters most when it runs unattended
(cron, a systemd timer, CI). This plugin emits events for each operation and hands them to notifier
plugins.

Core ships **no** notifiers, only the interface, so no HTTP dependency is added to this package. A
notifier is a separate pip-installable package.

### Configuring channels

Secrets go in `.env`; routing goes in `default.toml`/`.toml` next to `[restic.forget]`:

```toml
[restic.notify]
channels   = ["ntfy", "discord"]  # REQUIRED: nothing is sent unless a channel is named here
project    = "acme-prod-db01"     # optional, defaults to the directory name
min_level  = "warning"            # optional, default "info": info | warning | error
warn_after = ["30m", "2h"]        # optional, default ["30m", "2h"]: emit backup.slow at each

[restic.notify.ntfy]              # one table per channel, named after the notifier
topic      = "acme-backups"       # plugin-specific: whatever this notifier's from_config reads
events     = ["*"]                # optional, defaults to the notifier's own `subscribes`
min_level  = "info"               # optional, overrides the global floor for this channel

[restic.notify.discord]
webhook_url = "https://discord.com/api/webhooks/..."
events      = ["backup.failed", "check.failed"]   # humans get only the bad news

[restic.notify.targets.stream]    # optional per-backup-target watchdog override
warn_after = ["4h"]               # pg dumps are legitimately slow
```

Inside a channel table, **`events` and `min_level` are the only keys core reads**. Everything else
is handed to that notifier untouched, so `topic` and `webhook_url` above are entirely their own
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

`default.toml` is the committed template; `.toml` is gitignored and per-project. If `.toml` does not
exist it is copied from `default.toml`; once it exists it is yours and is never rewritten. A section
the template has and your `.toml` lacks produces a warning telling you to copy the block to adopt
the defaults, or to add an empty block to keep current behaviour and silence the warning.

### Events

Event names are `<operation>.<phase>`. Operations are `backup`, `restore`, `check`, `forget` and
`wipe`; phases are `started`, `succeeded`, `failed` and `slow`.

| Event | Level | Notes |
|---|---|---|
| `backup.started` | info | Arms the hang watchdog. |
| `backup.succeeded` | info | Carries the snapshot id. |
| `backup.failed` | error | Carries any failed `captain-hooks` scripts and their exit codes. |
| `backup.slow` | warning | Still running after `warn_after`. Does not kill anything. |
| `check.failed` | error | The one you most want to reach a human. |
| `forget.succeeded` | info | Carries the policy applied. |
| `wipe.*` | warning | Destructive but user-initiated. |

`restore.*`, `check.*` and `forget.*` follow the same shape. `configure` and `snapshots` emit
nothing; they are interactive and read-only.

Filtering is uniform: no event bypasses `min_level` or a channel's `events` list.

### Trying your channels out

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

Nothing is faked except the event: it goes through the same dispatcher, the same routing and the
same timeout as a real one.

Two things worth knowing:

- **Every drill is marked `NOTIFY-TEST`** in the fields a channel displays, so nobody woken at 3am
  has to work out whether it was real.
- **Channels filtered out by routing are reported**, not skipped silently. If `discord` only
  subscribes to `backup.failed` and you send `check.succeeded`, you are told it was filtered rather
  than left wondering whether the plugin is broken. `--force` sends anyway, which separates "this
  channel is broken" from "my routing does not match".

The task exits non-zero if nothing could be delivered, so it is usable as a provisioning check.

### Writing a notifier

One decorator, one classmethod, one `send`:

```python
from typing import Any, Mapping, Self

import requests
from edwh_restic_plugin.plugins import (
    BackupEvent, CheckEvent, Event, Failed, Notifier, register_notifier,
)


@register_notifier("mywebhook")
class MyWebhook(Notifier):
    contract = 1        # the contract version you wrote against, as a literal

    def __init__(self, url: str, secret: str) -> None:
        self.url = url
        self.secret = secret

    @classmethod
    def from_config(cls, env: Mapping[str, str], options: Mapping[str, Any]) -> Self | None:
        url = options.get("webhook_url")            # from [restic.notify.mywebhook]
        secret = env.get("MYWEBHOOK_SECRET")        # from .env, MYWEBHOOK_* only
        if not (url and secret):
            return None                             # named but unprovisioned -> inactive
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

### Security note

A notifier runs in-process and is trusted exactly like any other dependency. Events themselves are
built from an allowlist of fields and never include the repository URI (several backends embed
credentials in it) or the environment. But **`logs` on a failure event carries restic's full
stdout/stderr**, which can include repository paths and hostnames, so point channels carrying
failure events somewhere you would be comfortable pasting a terminal session.

## Custom repository types

Add a restic-supported backend this package does not ship, from your own package. Three members are
required. The example below is an **untested sketch** to show the shape, not a working Azure
implementation or a recommendation of any particular provider:

```python
import os

from edwh_restic_plugin.plugins import Repository, register


@register("example", aliases=("ex",), priority=20)
class ExampleRepository(Repository):
    def setup(self) -> None:
        self.check_env("EXAMPLE_NAME", None, "Container to store backups in")
        self.check_env("EXAMPLE_PASSWORD", None, "Restic password")
        self.check_env("EXAMPLE_ACCOUNT_KEY", None, "Account key")

    def prepare_for_restic(self, c) -> None:
        env = self.env_config
        os.environ["RESTIC_PASSWORD"] = env["EXAMPLE_PASSWORD"]
        os.environ["EXAMPLE_ACCOUNT_KEY"] = env["EXAMPLE_ACCOUNT_KEY"]

    @property
    def uri(self) -> str:
        return f"example:{self.env_config['EXAMPLE_NAME']}:/"
```

Declare it in your **`pyproject.toml`**, same group as notifiers:

```toml
[project.entry-points.edwh_restic_plugin]
example = "my_package"
```

Optional additions:

- `wipe`, `bucket` and `prepare_rclone_config` are only needed for `restic.wipe` and `restic.move`.
  Without them those two commands report that your backend does not support the operation;
  everything else works.
- `display_name` is what notifications show. It defaults to the registered short name, which
  discloses nothing. Override it to add something informative but safe, like
  `f"example:{self.bucket}"`. Never return `uri`, which can contain credentials.

Two conventions worth following:

- `<SHORTNAME>_PASSWORD` in `.env` is how a default repository is auto-selected when
  `--connection-choice` is omitted. A different name makes your repository unselectable by default.
- `_short_name` and aliases feed forget-policy lookup, so `[restic.forget.example]` works for free.

## License

`edwh-restic-plugin` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
