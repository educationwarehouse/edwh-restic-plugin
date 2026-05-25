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
- Actual wipe logic is backend-specific (implemented per provider, using `restic-reaper`).

Use this only when you intentionally want to remove a repository's backup contents.

## License

`edwh-restic-plugin` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
