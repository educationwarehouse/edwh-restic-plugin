# edwh-restic-plugin

[![PyPI - Version](https://img.shields.io/pypi/v/edwh-restic-plugin.svg)](https://pypi.org/project/edwh-restic-plugin)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/edwh-restic-plugin.svg)](https://pypi.org/project/edwh-restic-plugin)

-----

**Table of Contents**

- [Installation](#installation)
- [License](#license)
- [backup](#resticbackup)
- [restore](#resticrestore)
- [snapshots](#resticsnapshots)

## Installation

```console
pip install edwh-restic-plugin
```

But probably you want to install the whole `edwh` package:

```console
pipx install edwh[plugins,omgeving]
```

## restic.backup
To backup a file use the ``` inv backup ``` command

**arguments for the inv backup**
- connection_choice=SERVICE
- snapshot = "latest" by default, see [snapshots](#resticsnapshots) for more info
- message | send a message with the backup, default is datetime.localtime()
- verbose | print logs

example for backing up files without using streams:
> restic $HOST -r $URI backup --tag files *.sh

example for backing up files using streams:
> echo "hi" | restic $HOST -r $URI backup --tag stream --stdin --stdin-filename pg_dump.sql

> NOTE: put restore commands as an .sh file in a folder named captain-hooks, the folder needs to be in the same dir
> you run the inv restore in. SEE examples/captain-hooks for more info

**variables you can use in the restore sh file**
- $HOST
- $URI

---
## restic.restore
To restore a backup use the ``` inv restore ``` command

**arguments for the inv restore**
- connection_choice=SERVICE
- snapshot = "latest" by default, see [snapshots](#resticsnapshots) for more info
- target | location where the backup will go to
- verbose | print logs

example for restoring files without using streams:
> restic $HOST -r $URI restore latest --target recover_data --tag files

example for restoring files using streams:
> restic $HOST -r $URI dump $SNAPSHOT --tag stream pg_dump.sql

> NOTE: put restore commands as an .sh file in a folder named captain-hooks, the folder needs to be in the same dir
> you run the inv restore in. See examples/captain-hooks for more info

**variables you can use in the restore sh file**
- $HOST
- $URI
- $SNAPSHOT

---
## restic.snapshots

list of all backups that are made

> example command: inv snapshots -c local

```
Gebruikt connectie:  local
ID        Time                 Host     Tags
------------------------------------------------
71cde9e8  2023-04-05 17:49:21  sven-hp  stream : [hello world!]
d845dc99  2023-04-05 17:49:21  sven-hp  message
                                        71cde9e8
------------------------------------------------
2 snapshots
```

---

## License

`edwh-restic-plugin` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
See [the license](LICENSE.txt) for details. 