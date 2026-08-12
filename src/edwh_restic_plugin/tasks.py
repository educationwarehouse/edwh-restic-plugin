import functools
import json
import os
import subprocess
import sys
import tempfile
import typing as t
from pathlib import Path

import edwh.tasks
import invoke
from edwh import task
from edwh.tasks import DOCKER_COMPOSE
from ewok import Context
from termcolor import cprint

from .env import DOTENV, read_dotenv, set_env_value
from .events import BackupEvent, BasicEvent, CheckEvent, ForgetEvent, RestoreEvent, WipeEvent
from .exceptions import ResticError, UnsupportedOperation
from .forget import ResticForgetPolicy
from .helpers import _require_restic
from .notify import Dispatcher, Operation, build_channels, build_test_event, event_names
from .repositories import Repository, registrations
from .restictypes import DockerContainer

P = t.ParamSpec("P")
R = t.TypeVar("R")


def exits_on_restic_error(fn: t.Callable[P, R]) -> t.Callable[P, R]:
    """Turn a ResticError into a process exit code, at the outermost point that can.

    Library code raises instead of calling sys.exit(), so callers can react to a failure: print
    it, notify about it, clean up after it. Something still has to produce the exit code the CLI
    contract promises, and a task is the only place that knows the process is ending.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except ResticError as e:
            cprint(str(e), color="red", file=sys.stderr)
            sys.exit(e.exit_code)

    return wrapper


def cli_repo(
    connection_choice: str = None,
    restichostname: str = None,
    require_restic: bool = False,
) -> Repository:
    """
    Create a repository object and set up the connection to the backend.
    :param connection_choice: choose where you want to store the repo (local, SFTP, B2, swift)
    :param restichostname: which hostname to force for restic, or blank for default.
    :param require_restic: install restic if missing. Off by default because it may prompt for
        sudo and apt-install a package; `configure` opts in, since provisioning is its job.
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
            if f"{option.upper()}_PASSWORD" in env:
                connection_lowercase = option.lower()
                break
    else:
        connection_lowercase = connection_choice.lower()

    if not (repoclass := registrations.get(connection_lowercase)):
        _options = ", ".join(list(options))
        raise ValueError(f"Invalid connection type {connection_choice}. Please use one of {_options}!")

    print("Use connection: ", connection_lowercase)
    repo = repoclass()
    if require_restic:
        repo._require_restic()
    repo.setup()
    return repo


def repo_context(
    connection_choice: str | None,
    event_class: type[BasicEvent],
    require_restic: bool = False,
    **fields: t.Any,
) -> t.ContextManager[Repository]:
    """Run an operation against a repository, reporting its lifecycle.

        with repo_context(connection_choice, BackupEvent, target=target) as repo:
            repo.backup(c, verbose, target, message)

    Entering emits `started` and arms the watchdog; leaving disarms it and emits `succeeded` or
    `failed`. The second argument is the operation class, which is all this needs since phases are
    universal.
    """
    return Operation(connection_choice, event_class, cli_repo, require_restic, **fields).run()


@task(aliases=("notify-test", "test-notify"))
@exits_on_restic_error
def notify_test(
    _c: Context,
    event: str = "backup.failed",
    connection: str | None = None,
    channel: str | None = None,
    force: bool = False,
    all_events: bool = False,
):
    """Send a synthetic event to your real notifiers, so you can try them without breaking a backup.

    Events are marked NOTIFY-TEST in the fields a channel is likely to display, because a drill that
    looks identical to a genuine 3am alert is worse than no drill.

    Args:
        _c (Context)
        event (str): which `<operation>.<phase>` to send. Use --all-events for every one.
        connection (str, optional): resolve this repository so events carry its real display name.
            Left out, they say "no repository resolved" instead of touching a backend.
        channel (str, optional): only send to this channel, instead of all configured ones.
        force (bool): send even to channels whose `events` or `min_level` would filter it out.
            Use this to prove a channel works at all, separately from whether your routing is right.
        all_events (bool): send every `<operation>.<phase>` in turn.
    """
    channels = build_channels()
    if channel:
        channels = [ch for ch in channels if ch.notifier._short_name == channel]
        if not channels:
            configured = ", ".join(sorted(ch.notifier._short_name for ch in build_channels())) or "none"
            raise ResticError(f"no active channel named {channel!r}; configured: {configured}")

    if not channels:
        raise ResticError(
            "no channels are active. Name them in [restic.notify] channels, and check their credentials are in .env."
        )

    repo_display = None
    if connection:
        # Resolving is opt-in: the point of this task is to exercise notifiers, and it should work
        # on a machine where the backend is unreachable.
        repo_display = cli_repo(connection).display_name

    names = event_names() if all_events else [event]
    dispatcher = Dispatcher(channels)
    delivered = 0

    for name in names:
        built = build_test_event(name, repo_display)
        wanted = [ch for ch in channels if ch.wants(built)]
        skipped = [ch for ch in channels if ch not in wanted]

        for ch in skipped:
            # Reported rather than silent: "nothing arrived" otherwise looks like a broken plugin
            # when it is really the routing doing its job.
            cprint(
                f"{name}: skipping '{ch.notifier._short_name}' (filtered by events/min_level)"
                + ("; sending anyway because --force" if force else ""),
                color="yellow",
            )

        targets = channels if force else wanted
        for ch in targets:
            cprint(f"{name} -> {ch.notifier._short_name}", color="blue")
            dispatcher.send_to(ch, built)
            delivered += 1

    if not delivered:
        raise ResticError(
            "nothing was delivered: every channel filtered these events out. Re-run with --force to bypass routing."
        )

    cprint(f"\ndelivered {delivered} event(s). Nothing was backed up, restored or deleted.", color="green")


@task
def require_restic(c):
    _require_restic(c)


@task(aliases=("setup", "init"))
@exits_on_restic_error
def configure(c, connection_choice=None, restichostname=None):
    """Setup or update the backup command for your environment.
    connection_choice: choose where you want to store the repo (local, SFTP, B2, swift)
    restichostname: which hostname to force for restic, or blank for default.
    """

    # It has been decided to create a main path called 'backups' for each repository.
    # This can be changed or removed if desired.
    # A password is only passed with a few functions.
    # require_restic: this is the provisioning task, so it is the one that may install restic.
    cli_repo(connection_choice, restichostname, require_restic=True).configure(c)


@task
@exits_on_restic_error
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
    with repo_context(connection_choice, BackupEvent, target=target or None, message=message) as repo:
        repo.backup(c, verbose, target, message)

    # if policy is available: execute forget after backing up:
    # Separate operation, separate event: a backup that succeeded and a forget that failed are
    # different facts, and collapsing them would hide the second.
    if with_forget and (policy := repo.determine_forget_policy()):
        with repo_context(connection_choice, ForgetEvent, policy=policy.to_string()) as forget_repo:
            forget_repo.forget(c, policy)


@task
@exits_on_restic_error
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

    with repo_context(connection_choice, RestoreEvent, target=target or None, snapshot=snapshot) as repo:
        repo.restore(c, verbose, target, snapshot)
    # print("`edwh up` to restart the services.")


@task(iterable=["tag"], aliases=["list"])
@exits_on_restic_error
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
@exits_on_restic_error
def run(c, connection_choice: str = None, command: t.Optional[str] = None):
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
@exits_on_restic_error
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
@exits_on_restic_error
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

    with repo_context(connection, ForgetEvent, policy=policy) as repo:
        repo.forget(
            c,
            policy=policy and ResticForgetPolicy.from_string(policy),
            dry=dry,
        )


@task(aliases=("verify",))
@exits_on_restic_error
def check(c: Context, connection: str = None, read_data: bool = False, subset: str = ""):
    """Verify repository integrity.

    Silent repository corruption is the failure mode you otherwise discover during a restore,
    which makes this the most valuable thing to run on a schedule.

    Structure only by default. `--read-data` re-reads every byte, which is thorough but pays full
    egress on a cloud backend every run; `--subset=5%` (or `1G`, or `2/8`) reads a sample, and
    restic picks a different one each time, so repeated runs converge on full coverage without
    ever paying for it at once.

    Args:
        c (Context)
        connection (str, optional): repository to check; defaults to the .env-derived one.
        read_data (bool): read and verify every pack file.
        subset (str): read a subset, e.g. "5%", "1G" or "2/8". Ignored if read_data is set.
    """
    with repo_context(connection, CheckEvent, read_data=read_data, subset=subset) as repo:
        repo.check(c, read_data=read_data, subset=subset)


@task()
@exits_on_restic_error
def unlock(c: Context, connection: str = None, remove_all: bool = False):
    """
    Run restic unlock.
    """

    repo = cli_repo(connection)

    repo.prepare_env_for_restic(c)

    args = ["restic", "unlock"]

    if remove_all:
        args.append("--remove-all")

    c.run(" ".join(args))


@task(aliases=("stats", "stat"))
@exits_on_restic_error
def du(
    c: Context,
    connection: str = None,
    mode: t.Literal["restore-size", "file-by-contents", "blobs-per-file", "raw-data"] = "raw-data",
):
    """
    Retrieve and display statistics about the backup repository.

    Args:
        c: ewok Context
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


@task()
@exits_on_restic_error
def wipe(c, connection: str = None):
    repo = cli_repo(connection)
    repo.prepare_env_for_restic(c)

    # Confirm before entering repo_context: declining is not an operation that started, so it must
    # not emit wipe.started followed by nothing.
    confirmation = input(f"Type YES to wipe repository {repo!r}: ").strip()
    if confirmation != "YES":
        print("Aborted wipe operation.")
        return

    try:
        with repo_context(connection, WipeEvent) as wipe_repo:
            print(wipe_repo.wipe())
    except UnsupportedOperation as e:
        cprint(str(e), color="yellow")


@task()
@exits_on_restic_error
def move(c: Context, source: str = "", target: str = "", dry: bool = False):
    """Moves everything from source bucket to target bucket
    Args:
        c: Context
        source: source bucket
        target: target bucket
        dry: set to True to do a dry run of move. This mimics the function without actually moving files.
             NOTE, It's recommended to do a dry run first since dataloss is possible.
    """
    print(source, target)
    source_repo = cli_repo(source)
    source_repo.prepare_env_for_restic(c)
    target_repo = cli_repo(target)
    target_repo.prepare_env_for_restic(c)

    # Fail before touching anything: move needs rclone config and a bucket name from *both*
    # repositories, and a backend that cannot provide them should say so rather than half-run.
    try:
        source_config, target_config = source_repo.prepare_rclone_config(), target_repo.prepare_rclone_config()
        source_bucket, target_bucket = source_repo.bucket, target_repo.bucket
    except UnsupportedOperation as e:
        return cprint(str(e), color="yellow")

    with tempfile.TemporaryDirectory() as rclone:
        rclone_config = Path(rclone) / "rclone.config"
        rclone_config.write_text(f"""[{source}]
{source_config}

[{target}]
{target_config}""")

        rclone = f"rclone --config {rclone_config}"
        check_target_files = c.run(
            f"{rclone} lsf -R --files-only {target}:{target_bucket} | wc -l", hide=True
        ).stdout.strip()
        if int(check_target_files) > 0:
            if not edwh.tasks.confirm(
                f"There are {check_target_files} files in the target bucket. Continuing might overwrite them. Continue? [Yn] ",
                default=True,
            ):
                return
        params: str = ""
        if dry:
            params += "--dry-run"
        c.run(f"{rclone} sync {source}:{source_bucket} {target}:{target_bucket} {params}")


@task(pre=[edwh.tasks.require_sudo])
def backup_env_variables(c: Context, full: bool = False):
    """Prints out all .env repo variables


    Args:
        c: ewok Context
        full: enable to display all .env variables instead of repo variables

    """
    options = registrations.to_ordered_dict()
    grep_options = ""
    for option in options:
        grep_options += f"-e '{option.upper()}_' "
    home = c.run("echo $HOME", hide=True).stdout.strip()
    env_files = (
        c.run(f"sudo find {home} -name .env -type f -exec grep -l " + grep_options + " {}  \\;", hide=True)
        .stdout.strip()
        .split("\n")
    )
    if not full:
        grep_options = " | grep " + grep_options
    else:
        grep_options = ""
    for env_file in env_files:
        if not home + "/.env" in env_file and home + "/." in env_file:
            continue
        print(f"\n{env_file}\n")
        c.sudo(f"cat {env_file}{grep_options}")
    print("\n")


@task(aliases=("check-abstract-methods",))
def check_abstract_methode(c: Context):
    """Report repositories that cannot be instantiated because an abstract member is missing.

    Only setup, prepare_for_restic and uri are required. wipe, bucket and prepare_rclone_config
    are optional and degrade at the point of use, so a repository lacking them is fine here.
    """
    missing = False
    for repository_class in registrations:
        try:
            repository_class()
        except TypeError as e:
            missing = True
            cprint(f"Repository missing abstract methode(s): {repository_class.__name__} \n {e}", color="red")

    if not missing:
        cprint(
            f"All {len(registrations.to_ordered_dict())} repositories implement setup, prepare_for_restic and uri.",
            color="green",
        )
