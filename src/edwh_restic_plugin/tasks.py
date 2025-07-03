import json
import os
import subprocess
import typing

import invoke
from edwh import task
from edwh.tasks import DOCKER_COMPOSE
from invoke import Context
from termcolor import cprint

from .env import DOTENV, read_dotenv, set_env_value
from .forget import ResticForgetPolicy
from .helpers import _require_restic
from .repositories import Repository, registrations
from .restictypes import DockerContainer


def cli_repo(connection_choice: str = None, restichostname: str = None) -> Repository:
    """
    Create a repository object and set up the connection to the backend.
    :param connection_choice: choose where you want to store the repo (local, SFTP, B2, swift)
    :param restichostname: which hostname to force for restic, or blank for default.
    :return: repository object
    """
    env = read_dotenv(DOTENV)
    if restichostname:
        set_env_value(DOTENV, "RESTICHOSTNAME", restichostname)

    options = registrations.to_ordered_dict()

    connection_lowercase = ""
    if connection_choice is None:
        # search for the most important backup and use it as default
        for option in options:
            if f"{option.upper()}_NAME" in env:
                connection_lowercase = option.lower()
                break
    else:
        connection_lowercase = connection_choice.lower()

    if not (repoclass := registrations.get(connection_lowercase)):
        _options = ", ".join(list(options))
        raise ValueError(f"Invalid connection type {connection_choice}. Please use one of {_options}!")

    print("Use connection: ", connection_lowercase)
    repo = repoclass()
    repo.setup()
    return repo


@task
def require_restic(c):
    _require_restic(c)


@task(aliases=("setup", "init"))
def configure(c, connection_choice=None, restichostname=None):
    """Setup or update the backup command for your environment.
    connection_choice: choose where you want to store the repo (local, SFTP, B2, swift)
    restichostname: which hostname to force for restic, or blank for default.
    """

    # It has been decided to create a main path called 'backups' for each repository.
    # This can be changed or removed if desired.
    # A password is only passed with a few functions.
    cli_repo(connection_choice, restichostname).configure(c)


@task
def backup(
    c,
    target: str = "",
    connection_choice: str = None,
    message: str = None,
    verbose: bool = True,
    without_forget: bool = False,
):
    """Performs a backup operation using restic on a local or remote/cloud file system.

    Args:
        c (Context)
        target (str): The target of the backup (e.g. 'files', 'stream'; default is all types).
        connection_choice (str): The name of the connection to use for the backup.
            Defaults to None, which means the default connection will be used.
        message (str): A message to attach to the backup snapshot.
            Defaults to None, which means no message will be attached.
        verbose (bool): If True, outputs more information about the backup process. Defaults to False.
        without_forget (bool): don't execute forget policy to purge old snapshots

    Raises:
        Exception: If an error occurs during the backup process.
    """
    # --without-forget is nicer than --no-with-forget, inverse here to make it less confusing:
    with_forget = not without_forget
    # After 'backup', a file path can be specified.In this script, a test file is chosen at './test/testbestand'.
    # It can be replaced with the desired path over which restic should perform a backup.
    # The option --verbose provides more information about the backup that is made.It can be removed if desired.

    # By using additions, it is possible to specify what should be included:
    # --exclude ,Specified one or more times to exclude one or more items.
    # --iexclude, Same as --exclude but ignores the case of paths.
    # --exclude-caches, Specified once to exclude folders containing this special file.
    # --exclude-file, Specified one or more times to exclude items listed in a given file.
    # --iexclude-file, Same as exclude-file but ignores cases like in --iexclude.
    # --exclude-if-present 'foo', Specified one or more times to exclude a folder's content if it contains.
    # a file called 'foo' (optionally having a given header, no wildcards for the file name supported).
    # --exclude-larger-than 'size', Specified once to excludes files larger than the given size.
    # Please see 'restic help backup' for more specific information about each exclude option.
    repo = cli_repo(connection_choice)
    repo.backup(c, verbose, target, message)

    # if policy is available: execute forget after backing up:
    if with_forget and (policy := repo.determine_forget_policy()):
        repo.forget(c, policy)


@task
def restore(c, connection_choice: str = None, snapshot: str = "latest", target: str = "", verbose: bool = True):
    """
    The restore function restores the latest backed-up files by default and puts them in a restore folder.

    IMPORTANT: please provide -t for the path where the restore should go. Also remember to include -c for the service
    where the backup is stored.

    :type c: Context
    :param connection_choice: the service where the files are backed up, e.g., 'local' or 'os' (= openstack).
    :param snapshot: the ID where the files are backed up, default value is 'latest'.
    :param target: The target of the backup (e.g. 'files', 'stream'; default is all types).
    :param verbose: display verbose logs (inv restore -v).
    :return: None
    """
    # For restore, --target is the location where the restore should be placed, --path is the file/path that should be
    # retrieved from the repository.
    # 'which_restore' is a user input to enable restoring an earlier backup (default = latest).
    # Stop the postgres services.
    c.run(f"{DOCKER_COMPOSE} stop -t 1 pg-0 pg-1 pgpool", warn=True, hide=True)

    # Get the volumes that are being used.
    docker_inspect: invoke.Result = c.run("docker inspect pg-0 pg-1", hide=True, warn=True)
    if docker_inspect.ok:
        # Only if ok, because if pg-0 and pg-1 do not exist, this does not exist either, and nothing needs to be removed
        inspected: list[DockerContainer] = json.loads(docker_inspect.stdout)
        volumes_to_remove: list[str] = []
        for service in inspected:
            volumes_to_remove.extend(mount["Name"] for mount in service["Mounts"] if mount["Type"] == "volume")
        # Remove the containers before a volume can be removed.
        c.run(f"{DOCKER_COMPOSE} rm -f pg-0 pg-1")
        # Remove the volumes.
        for volume_name in volumes_to_remove:
            c.run(f"docker volume rm {volume_name}")

    cli_repo(connection_choice).restore(c, verbose, target, snapshot)
    # print("`inv up` to restart the services.")


@task(iterable=["tag"])
def snapshots(c, connection_choice: str = None, tag: list[str] = None, n: int = 1, verbose: bool = False):
    """
    With this you can see per repo which repo is made when and where, \
        the repo-id can be used at inv restore as an option

    :type c: Context
    :param connection_choice: service
    :param tag: files, stream ect
    :param n: amount of snapshot to view, default=1(latest)
    :param verbose: show which commands are being executed?
    :return: None
    """
    # if tags is None set tag to default tags
    if tag is None:
        tag = ["files", "stream"]

    cli_repo(connection_choice).snapshot(c, tags=tag, n=n, verbose=verbose)


def interactive(conn: Repository):
    subprocess.run(["/bin/bash", "--norc", "--noprofile"], env=os.environ | {"PS1": f"{conn!r} $ "})


@task(pre=[require_restic])
def run(c, connection_choice: str = None, command: typing.Optional[str] = None):
    """
    This function prepares for restic and runs the input command until the user types "exit".

    :type c: Context
    :param connection_choice: The connection name of the repository.
    :param command: restic subcommand to use (default: none, prompt interactively)
    """
    conn = cli_repo(connection_choice)
    conn.prepare_for_restic(c)

    if command:
        if not command.startswith("restic "):
            command = f"restic {command}"
        print(c.run(command, hide=True, warn=True, pty=True))
    else:
        interactive(conn)


@task()
def env(c, connection_choice: str = None):
    """

    :type c: Context
    :param connection_choice: The connection name of the repository.
    """
    from copy import deepcopy

    old = deepcopy(os.environ)
    cli_repo(connection_choice).prepare_for_restic(c)
    new = os.environ
    for k, v in new.items():
        if k not in old or old[k] != v:
            print(f"export {k.upper()}={v}")


@task()
def forget(c: Context, connection: str = None, policy: str = None, dry: bool = False):
    """
    Run restic forget (with prune) based on a specific policy defined in a TOML configuration file.

    This task retrieves a forgetting policy based on the active connection's short name (e.g. 'os')
    or its aliases (e.g. 'openstack', 'swift'), with a fallback to the key "default" if no specific policy is found.
    The policy is first searched in the specified TOML configuration file
    (defaulting to '.toml' in the current directory),
    and if not present, it falls back to 'default.toml'.

    The policy can include various retention options such as:
        - `keep-last`: Number of latest snapshots to keep.
        - `keep-hourly`: Number of hourly snapshots to keep.
        - `keep-daily`: Number of daily snapshots to keep.
        - `keep-weekly`: Number of weekly snapshots to keep.
        - `keep-monthly`: Number of monthly snapshots to keep.
        - `keep-yearly`: Number of yearly snapshots to keep.
        ...

    Example TOML section:
        [tool.restic.openstack]
        keep-daily = 3
        keep-weekly = 2

    Args:
        c (Context): The context in which the task is executed.
        connection (str, optional): The name of the connection to use for the backup.
                                    Defaults to None which will look for the connection based on your .env file
                                    and the repository priorities.
        policy (str, optional): A string representation of the policy to apply.
                                If not provided, the policy will be retrieved from the TOML files as described above.
        dry (bool): If set to True, performs a dry run of the forget operation without making any changes.
            This allows users to see what would happen without actually deleting any snapshots.

    See also:
        https://restic.readthedocs.io/en/latest/060_forget.html#removing-snapshots-according-to-a-policy
    """

    repo = cli_repo(connection)

    repo.forget(
        c,
        policy=policy and ResticForgetPolicy.from_string(policy),
        dry=dry,
    )


@task(aliases=("stats", "stat"))
def du(
    c: Context,
    connection: str = None,
    mode: typing.Literal["restore-size", "file-by-contents", "blobs-per-file", "raw-data"] = "raw-data",
):
    """
    Retrieve and display statistics about the backup repository.

    Args:
        c: invoke Context
        connection (str, optional): The name of the connection to use for the backup.
                                    Defaults to None, which will look for the connection based on your .env file
                                    and the repository priorities.

        mode (Literal): Specifies the mode of statistics to retrieve.
            Restic uses "restore-size" by default, but it's wildly inaccurate.
            The available modes are:
                - "restore-size": Estimates the size of data that would be restored.
                - "file-by-contents": Shows the number of files and their sizes based on their contents.
                - "blobs-per-file": Displays the number of blobs associated with each file.
                - "raw-data": Provides the most detailed information about the repository's data.

    """
    repo = cli_repo(connection)
    repo.prepare_env_for_restic(c)

    c.run(f"restic stats --mode {mode}")
