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
- [configure](#resticconfigure)

## Installation

```console
pip install edwh-restic-plugin
```

But probably you want to install the whole `edwh` package:

```console
pipx install edwh[plugins,omgeving]
```
## What is Restic?
Restic is a program that automatically stores backups in a separate repository, securing them with a password and compressing them. 
There are multiple options for storing backups, including locally on the same host, SFTP to a remote host,
and remotely to various systems and providers such as Amazon, REST, Minio, Wasabi, Alibaba Cloud, Openstack, Backblaze, 
Azure, Google, and rclone.

## restic usages for edwh
For EDWH, they use local storage, SFTP, Backblaze, and Openstack.

## Creating a new repository
To create a new repository, you need the local path to the folder where the backup should be stored,
or an SSH connection for SFTP with a path to the folder where the backup should be stored. 
It's important to note that SFTP servers may close the connection if they don't receive data, 
which can happen if Restic is processing large amounts of unchanged data. 
To avoid this issue, you can use the option "ServerAliveInterval 60 & ServerAliveCountMax 240" in the SSH config.

For Backblaze, you need an account ID and Key, as well as the name of the bucket. 
For Openstack, you need to specify which Keystone to use, environment variables (as shown in the image), 
and the name of the container. 
Restic can also work with an OpenStack RC file.### Commando:

### local:
> restic init –repo /pad/naar/repo

### SFTP:
> restic -r sftp:user@host:/pad/naar/repo init

### Backblaze:
> restic -r b2:bucketname:/pad/naar/repo init

### Openstack:
> restic -r swift:container_name:/repo init

## restic.backup
To backup a file use the ``` inv backup ``` command

**arguments for the inv backup**
- connection_choice=SERVICE
- snapshot = "latest" by default, see [snapshots](#resticsnapshots) for more info
- message | send a message with the backup, default is datetime.localtime()
- verbose | print logs

**Requirements:**

- For local storage, the path to the folder where the backup should be stored.
- For SFTP, an SSH connection with public/private key in an agent or via SSH config, and the path to the folder where the backup should be stored. Note that SFTP servers may close the connection if they don't receive data, which can happen if Restic is processing large amounts of unchanged data. To avoid this issue, you can use the option ServerAliveInterval 60 & ServerAliveCountMax 240 in the SSH config.
- For Backblaze, you need a B2 account ID and key, as well as the name of the bucket.
- For Openstack, you need to specify which Keystone to use, environment variables (as shown in the image), and the name of the container. Restic can also work with an OpenStack RC file.
- For all of the above requirements, you also need to specify what needs to be backed up.
- example for backing up files without using streams:

example for backing up files using no stream
>restic $HOST -r $URI backup --tag files *.sh

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

## restic.configure
set configuration for the .env file
> inv configure

## License

`edwh-restic-plugin` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
See [the license](LICENSE.txt) for details. 