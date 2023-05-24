# edwh-restic-plugin

[![PyPI - Version](https://img.shields.io/pypi/v/edwh-restic-plugin.svg)](https://pypi.org/project/edwh-restic-plugin)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/edwh-restic-plugin.svg)](https://pypi.org/project/edwh-restic-plugin)

-----

**Table of Contents**

- [Installation](#installing-the-plugin)
- [what is restic](#what-is-restic)
- [configuring edwh restic](#configuring-edwh-restic)
- [backup](#using-ew-resticbackup)
- [restore](#using-ew-resticrestore)
- [snapshots](#using-ew-resticsnapshots)
- [configure](#using-ew-resticconfigure)
- [License](#license)

## Installing the plugin

```console
pip install edwh-restic-plugin
```

But probably you want to install the whole `edwh` package:

```console
pipx install edwh[plugins,omgeving]
# or
pipx install edwh[restic]
```

## What is Restic?
Restic is a program that automatically stores backups in a separate repository, securing them with a password and compressing them. 
There are multiple options for storing backups, including locally on the same host, SFTP to a remote host,
and remotely to various systems and providers such as Amazon, REST, Minio, Wasabi, Alibaba Cloud, Openstack, Backblaze, 
Azure, Google, and rclone. 

## Configuring `edwh restic.*`
For EDWH, they use local storage, SFTP, Backblaze, and Openstack.

### Creating a new repository
To create a new repository, you need the local path to the folder where the backup should be stored,
or an SSH connection for SFTP with a path to the folder where the backup should be stored. 
It's important to note that SFTP servers may close the connection if they don't receive data, 
which can happen if Restic is processing large amounts of unchanged data. 
To avoid this issue, you can use the option "ServerAliveInterval 60 & ServerAliveCountMax 240" in the SSH config.

For Backblaze, you need an account ID and Key, as well as the name of the bucket. 
For Openstack, you need to specify which Keystone to use, environment variables (as shown in the image), 
and the name of the container. 
Restic can also work with an OpenStack RC file.
### commands:

#### local:
> restic init –repo /path/to/repo

#### SFTP:
> restic -r sftp:user@host:/path/to/repo init

Note that SFTP servers may close the connection if they don't receive data, which can happen if 
Restic is processing large amounts of unchanged data. To avoid this issue, you can use the 
option `ServerAliveInterval 60` and `ServerAliveCountMax 240` in the SSH config.

#### Backblaze:
> restic -r b2:bucketname:/path/to/repo init

#### Openstack:
> restic -r swift:container_name:/repo init
---
### Working with `captain-hooks` shell scripts

#### backing up file(s) using Restic with .sh
example for backing up files using no stream
>restic $HOST -r $URI backup --tag files *.sh

example for backing up files using streams:
> (place stream here) | restic $HOST -r $URI backup --tag stream --stdin --stdin-filename (file_name)

> NOTE: put restore commands as an .sh file in a folder named captain-hooks, the folder needs to be in the same dir
> you run the inv restore in. SEE examples/captain-hooks for more info

**variables you can use in the backup sh file**
- $HOST
- $URI

---

#### restoring file(s) using Restic with .sh
example for restoring files without using streams:
> restic $HOST -r $URI restore latest --target recover_data --tag files

example for restoring files using streams:
> restic $HOST -r $URI dump $SNAPSHOT --tag stream (file_name)

> NOTE: put restore commands as an .sh file in a folder named captain-hooks, the folder needs to be in the same dir
> you run the inv restore in. See examples/captain-hooks for more info

**variables you can use in the restore sh file**
- $HOST
- $URI
- $SNAPSHOT

---
## Using `ew restic.backup`
**example**: `ew restic.backup -v -c=local`

Possible arguments for `ew restic.backup`:
- **target**: restore files in captain hooks to be executed
- **connection_choice**: connection to use for access to the repository. Can be OS, SFTP, B2, or 
  local.
- **snapshot**: "latest" by default, see [snapshots](#using-ew-resticsnapshots) for more information
- **message**: store a descriptive message with the backup, default message is `datetime.localtime()`
- **verbose**: add verbosity, printing more debug information while processing the activity. 

**Requirements:**

- For local storage a path to the folder where the backup should be stored is required.
- For SFTP, an SSH connection with public/private key in an agent or via SSH config, and the path to the folder where the backup should be stored. 
- For Backblaze, you need a B2 account-ID and key, as well as the name of the bucket to store 
  the backup. 
- For Openstack, you need to specify which Keystone to use, environment variables (as shown in the image), and the name of the container. Restic can also work with an OpenStack RC file.
- For all of the above requirements, you also need to specify what needs to be backed up.

---
## Using `ew restic.restore`

**example**: `ew restic.restore -v -c=local`

Possible arguments for the `ew restic.restore`
- **connection_choice**: connection to use for access to the repository. Can be OS, SFTP, B2, or 
  local.
- **snapshot**: "latest" by default, see [snapshots](#using-ew-resticsnapshots) for more information
- **target**: restore files in captain hooks to be executed
- **verbose**: add verbosity, printing more debug information while processing the activity. 

---
## Using `ew restic.snapshots`

list of all backups that are made

**example**: `ew restic.snapshots -c local`

```
Gebruikt connectie:  local
ID        Time                 Host       Tags
------------------------------------------------
71cde9e8  2023-04-05 17:49:21  ubuntu-hp  stream : [hello world!]
d845dc99  2023-04-05 17:49:21  ubuntu-hp  message
                                          71cde9e8
------------------------------------------------
2 snapshots
```

---

## Using `ew restic.configure`
setting up the .env file for the specified repository. Which can be OS, SFTP, B2, or local.


## Using `ew restic.run`
this command sets up an eviroment with the connection choice of your choosing and runs the input command until the user 
types "exit".

Possible arguments for `ew restic.backup`:
- **connection_choice(-c)**: connection to use for access to the repository. Can be OS, SFTP, B2, or 
  local.


## License
`edwh-restic-plugin` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
See [the license](LICENSE.txt) for details. 
