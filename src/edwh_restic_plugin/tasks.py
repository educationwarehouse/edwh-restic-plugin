from collections import defaultdict, OrderedDict

import invoke
from invoke import task

import datetime
import io
import os
from pathlib import Path
import re
import sys
import typing
import json

from tqdm import tqdm

# the path where the restic command is going to be executed
DEFAULT_BACKUP_FOLDER = Path("captain-hooks")
# the path where the environment variables are going
DOTENV = Path(".env")
_dotenv_settings = {}


def fix_tags(tags):
    """
    removes all None type elements from list
    :param tags: list of string
    :return:
    """
    i = 0
    while i < len(tags):
        if tags[i] is not None:
            i += 1
        else:
            del tags[i]

    return tags


class Repository:
    # _targets: a list of file and directory paths that should be included in the backup.
    _targets = [".env", "./backup"]
    # _excluded: a list of file and directory paths that should be excluded from the backup.
    _excluded = [
        ".git",
        ".idea",
        "backups",
        "*.pyc",
        "*.bak",
        "../",
        "./..",
        "errors",
        "sessions",
        "__pycache__",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._restichostname = read_dotenv(DOTENV).get(
            "RESTICHOSTNAME"
        )  # or None if it is not there

    @property
    def uri(self):
        """Return the prefix required for restic to indicate the protocol, for example sftp:hostname:"""
        raise NotImplementedError("Prefix unknown in base class")

    def setup(self):
        """Ensure that the settings are in the .env file"""
        raise NotImplementedError("Setup undefined")

    def prepare_for_restic(self, c):
        """No environment variables need to be defined for local"""
        raise NotImplementedError("Prepare for restic undefined")

    def configure(self, c):
        """Configure the backup environment variables."""
        self.prepare_for_restic(c)
        print("configure")
        # First, make sure restic is up-to-date
        c.run("sudo restic self-update", hide=True, warn=True)
        # This is the command used to configure the environment variables properly.
        c.run(f"restic init --repository-version 2 -r {self.uri}")

    @property
    def hostarg(self):
        """Return the host argument for restic command."""
        return f" --host {self._restichostname} " if self._restichostname else ""

    @property
    def targets(self):
        """Return the target files and directories for the backup."""
        return " ".join(self._targets)

    @property
    def excluded(self):
        """Return the excluded files and directories for the backup.
        Here comes the files that are going to be excluded"""
        return " --exclude ".join(self._excluded)

    @staticmethod
    def get_snapshot_from(stdout: str):
        """
        Parses the stdout from a Restic command to extract the snapshot ID.

        Args:
        - stdout (str): The stdout output from a Restic command.

        Returns:
        - The snapshot ID as a string.
        """
        snapshots_ids = re.findall(r"snapshot (.*?) saved", stdout)
        return snapshots_ids[-1] if snapshots_ids else None

    @staticmethod
    def get_scripts(target, verb):
        """Retrieves the scripts that contain a restic command and returns them to 'execute_files' to execute them.

        Args:
        - target (str): target is a string that specifies the target of the backup, can be a file, stream, directory,
        or any other object that needs to be backed up.
        - verb (str): is also a string that specifies the action to be performed on the target.
        For example, the verb could be "backup" or "restore". The verb is used in combination with the target to
        search for the backup script files that contain the restic command.
        """
        # get files by verb and target. EXAMPLE backup_files_*.sh
        files = [str(file) for file in DEFAULT_BACKUP_FOLDER.glob(f"{verb}_{target}*")]
        # check if no files are found
        if not files:
            print("no files found with target:", target)
            sys.exit(255)

        return files

    def execute_files(
        self,
        c,
        target: str,
        verb: str,
        verbose: bool,
        message: str = None,
        snapshot: str = "latest",
    ):
        """
        Executes the backup scripts retrieved by 'get_scripts' function.

        Args:
        - verbose (bool): A flag indicating whether to display verbose output.
        - target (str): The target of the backup.
        - verb (str): The verb associated with the backup.
        - message (str, optional): The message to be associated with the backup.
        If not provided, the current local time is used. Defaults to None.
        - snapshot (str, optional): The snapshot to be used for the backup. Defaults to "latest".
        """
        self.prepare_for_restic(c)

        # set snapshot available in environment for sh files
        os.environ["SNAPSHOT"] = snapshot

        # Here you can make a message that you will see in the snapshots list
        if message is None:
            # If no message is provided, use the current local time as the backup message
            message = f"{str(datetime.datetime.now())} localtime"

        # set MSG in environment for sh files
        os.environ["MSG"] = message

        # get files by target and verb. see self.get_scripts for more info
        files = self.get_scripts(target, verb)

        snapshots_created = []
        # run all backup/restore files
        for file in tqdm(files):
            if verbose:
                print("running", file)

            ran_script = c.run(file, hide=True, warn=True, pty=True)

            if verbose:
                print(f"{file} output:")
                if ran_script.stdout or ran_script.stderr:
                    print(f"out:{ran_script.stdout}\nerr:{ran_script.stderr}")
                else:
                    print("no output found!")
            snapshot = self.get_snapshot_from(ran_script.stdout)
            snapshots_created.append(snapshot)

        # send message with backup. see message for more info
        # also if a tag in tags is None it will be removed by fix_tags
        if verb != "restore":
            tags = fix_tags(["message"] + snapshots_created)
            c.run(
                f"restic {self.hostarg} -r {self.uri} backup --tag {','.join(tags)} --stdin --stdin-filename message",
                in_stream=io.StringIO(message),
                hide=True
            )

    def backup(self, c, verbose: bool, target: str, message: str):
        """
        Backs up the specified target.

        Args:
        - verbose (bool): A flag indicating whether to display verbose output.
        - target (str): The target of the backup.
        - verb (str): The verb associated with the backup.
        - message (str): The message to be associated with the backup.
        """
        self.execute_files(c, target, "backup", verbose, message)

    def restore(self, c, verbose: bool, target: str, snapshot: str = "latest"):
        """
        Restores the specified target using the specified snapshot or the latest if None is given.

        Args:
        - verbose (bool): A flag indicating whether to display verbose output.
        - target (str): The target of the restore.
        - verb (str): The verb associated with the restore.
        - snapshot (str, optional): The snapshot to be used for the restore. Defaults to "latest".
        """
        self.execute_files(c, target, "restore", verbose, snapshot=snapshot)

    def check(self, c):
        """
        Checks the integrity of the backup repository.
        """
        self.prepare_for_restic(c)
        c.run(f"restic {self.hostarg} -r {self.uri} check --read-data")

    def snapshot(self, c, tags: list = None, n=2):
        """
        a list of all the backups with a message

        Args:
        - tags (list, optional): A list of tags to use for the snapshot. Defaults to None.
        - n (int, optional): The number of latest snapshots to show. Defaults to 2.

        Returns:
        None. This function only prints the output to the console.
        """
        # choose to see only the files or the stream snapshots
        if tags is None:
            tags = ["files", "stream"]

        self.prepare_for_restic(c)
        tags = "--tag " + " --tag ".join(tags) if tags else ""
        stdout = c.run(
            f"restic {self.hostarg} -r {self.uri} snapshots --latest {n} {tags} -c",
            hide=True,
        ).stdout

        snapshot_lines = re.findall(r"^([0-9a-z]{8})\s", stdout, re.MULTILINE)
        main_tag_per_snapshot = {
            snapshot: re.findall(rf"^{snapshot}.*?(\w*)$", stdout, re.MULTILINE)
            for snapshot in snapshot_lines
            # snapshot: re.findall(rf"^{snapshot}", stdout) for snapshot in snapshots
        }

        message_snapshot_per_snapshot = defaultdict(
            list
        )  # key is source, value is snapshot containing the message
        for snapshot, possible_tag_names in main_tag_per_snapshot.items():
            tag_name = possible_tag_names[0]
            if tag_name not in ["message"]:
                continue
            for _, is_message_for_snapshot_id in re.findall(
                rf"\n{snapshot}.*(\n\s+(.*)\n)+", stdout
            ):
                message_snapshot_per_snapshot[is_message_for_snapshot_id].append(
                    snapshot
                )

        for snapshot, message_snapshots in message_snapshot_per_snapshot.items():
            # print all Restic messages
            restore_output = c.run(
                f"restic {self.hostarg} -r {self.uri} dump {message_snapshots[0]} --tag message message",
                hide=True,
                warn=True,
            ).stdout
            message = restore_output.strip()
            stdout = re.sub(
                rf"\n{snapshot}(.*)\n", rf"\n{snapshot}\1 : [{message}]\n", stdout
            )


class LocalRepository(Repository):
    def __init__(self):
        super().__init__()
        self.password = None
        self.name = None

    def setup(self):
        """Ensure the required settings are defined in the .env file."""
        self.name = check_env(
            DOTENV,
            "LOCAL_NAME",
            default=None,
            comment="Repository name  is mandatory (directory)",
        )
        self.password = check_env(
            DOTENV,
            "LOCAL_PASSWORD",
            default=None,
            comment="Create a password, keep it safe.",
        )

    def prepare_for_restic(self, c):
        """No environment variables need be defined for local"""
        env = read_dotenv(DOTENV)
        self.name = env["LOCAL_NAME"]
        os.environ["HOST"] = self.hostarg
        os.environ["URI"] = self.uri
        os.environ["RESTIC_HOST"] = self.hostarg
        os.environ["RESTIC_REPOSITORY"] = self.uri
        os.environ["RESTIC_PASSWORD"] = self.password = env["LOCAL_PASSWORD"]

    @property
    def uri(self):
        """
        Get the URI of the class instance.

        The function returns the value of the 'name' attribute, which represents the URI of the class instance.
        """
        return self.name


class SFTPRepository(Repository):
    def __init__(self):
        super().__init__()
        self.hostname = None
        self.password = None
        self.name = None

    def setup(self):
        """Ensure the required settings are defined in the .env file."""
        check_env(
            DOTENV,
            "SFTP_NAME",
            default=None,
            comment="Repository name  is mandatory (directory)",
        )
        check_env(
            DOTENV,
            "SFTP_PASSWORD",
            default=None,
            comment="Create a password, keep it safe.",
        )
        check_env(
            DOTENV,
            "SFTP_HOSTNAME",
            default=None,
            comment="Use the correnct hostname (directory above the repo name)",
        )  #

    def prepare_for_restic(self, c):
        """read out of .env file"""
        env = read_dotenv(DOTENV)
        self.name = env["SFTP_NAME"]
        self.password = env["SFTP_PASSWORD"]
        self.hostname = env["SFTP_HOSTNAME"]
        os.environ["HOST"] = self.hostarg
        os.environ["URI"] = self.uri
        os.environ["RESTIC_HOST"] = self.hostarg
        os.environ["RESTIC_REPOSITORY"] = self.uri
        os.environ["RESTIC_PASSWORD"] = self.password
        ran = c.run(f'ssh {self.hostname} "exit"', warn=True, hide=True)
        if not ran.ok:
            print(
                """
                SSH config file not (properly) configured, configure according to the following format:
                Host romy
                HostName romy.edwh.nl
                User ubuntu
                IdentityFile ~/romy.key
                To save a new host in the ssh config file, go to ~/.ssh and edit the config file there.
                For more information, read the ssh_config manual (man ssh_config)
                """
            )
            exit(1)

    @property
    def uri(self):
        """
        :return: sftp uri with self.hostname and self.name
        """
        return f"sftp:{self.hostname}:{self.name}"


class B2Repository(Repository):
    def __init__(self):
        super().__init__()
        self.key = None
        self.keyid = None
        self.bucket_name = None
        self.password = None
        self.name = None

    def setup(self):
        """Ensure the required settings are defined in the .env file."""
        check_env(
            DOTENV,
            "B2_NAME",
            default=None,
            comment="Repository name  is mandatory (directory)",
        )
        check_env(
            DOTENV,
            "B2_PASSWORD",
            default=None,
            comment="Create a password, keep it safe.",
        )
        check_env(
            DOTENV,
            "B2_BUCKETNAME",
            default=None,
            comment="Use the correct bucketname (directory above repo name",
        )
        check_env(
            DOTENV,
            "B2_ACCOUNT_ID",
            default=None,
            comment="enter the correct KEY ID here.",
        )
        check_env(
            DOTENV,
            "B2_ACCOUNT_KEY",
            default=None,
            comment="enter the correct KEY here.",
        )

    def prepare_for_restic(self, c):
        """read variables out of .env file"""
        env = read_dotenv(DOTENV)
        self.name = env["B2_NAME"]
        self.password = env["B2_PASSWORD"]
        self.bucket_name = env["B2_BUCKETNAME"]
        self.keyid = env["B2_ACCOUNT_ID"]
        self.key = env["B2_ACCOUNT_KEY"]
        os.environ["B2_ACCOUNT_ID"] = self.keyid
        os.environ["B2_ACCOUNT_KEY"] = self.key
        os.environ["RESTIC_REPOSITORY"] = self.uri
        os.environ["RESTIC_HOST"] = self.hostarg
        os.environ["RESTIC_PASSWORD"] = self.password
        os.environ["HOST"] = self.hostarg
        os.environ["URI"] = self.uri

    @property
    def uri(self):
        """
        :return: uri of b2 with self.bucketname and self.name
        """
        return f"b2:{self.bucket_name}:{self.name}"


class SwiftRepository(Repository):
    def __init__(self):
        super().__init__()
        self.restic_password = None
        self.password = None
        self.container_name = None
        self.name = None

    def setup(self):
        """Ensure the required settings are defined in the .env file."""
        check_env(
            DOTENV,
            "OS_AUTH_URL",
            default="https://identity.stack.cloudvps.com/v2.0",
            comment="Auth URL for this openstack environment",
        )
        check_env(
            DOTENV,
            "OS_TENANT_ID",
            default=None,
            comment='Tenant name, comes from the openrc file, or from the auth info, looks like "f8d15....269"',
        )
        check_env(
            DOTENV,
            "OS_TENANT_NAME",
            default="BST000425 productie-backups",
            comment="Project name within openstack, for example 'BST000425 production backups'",
        )
        check_env(
            DOTENV,
            "OS_REGION_NAME",
            default="NL",
            comment="NL is supported, others are unknown.",
        )
        check_env(
            DOTENV,
            "OS_USERNAME",
            default="backup@edwh.nl",
            comment="Username is the openstack username",
        )
        check_env(
            DOTENV,
            "OS_PASSWORD",
            default=None,
            comment="Password belonging to the openstack user",
        )
        check_env(
            DOTENV,
            "OS_CONTAINERNAME",
            default="backups",
            comment="Objectstore container name, should be created automatically if it doesn't exist.",
        )
        check_env(
            DOTENV,
            "OS_NAME",
            default=None,
            comment="Repository name within the bucket",
        )
        check_env(
            DOTENV,
            "OS_RESTIC_PASSWORD",
            default=None,
            comment="Password of the repository within the container",
        )

        # check_env(
        #     DOTENV,
        #     "OS_STORAGE_URL",
        #     default=None,
        #     comment="voer hier de juiste URL in.",
        # )
        # check_env(
        #     DOTENV,
        #     "OS_AUTH_TOKEN",
        #     default=None,
        #     comment="gvoer hier de juiste TOKEN in.",
        # )

    def prepare_for_restic(self, c):
        """read variables out of .env file"""
        env = read_dotenv(DOTENV)
        self.name = env["OS_NAME"]
        self.container_name = env["OS_CONTAINERNAME"]
        os.environ["OS_USERNAME"] = env["OS_USERNAME"]
        os.environ["OS_AUTH_URL"] = env["OS_AUTH_URL"]
        os.environ["OS_TENANT_ID"] = env["OS_TENANT_ID"]
        os.environ["OS_TENANT_NAME"] = env["OS_TENANT_NAME"]
        os.environ["OS_REGION_NAME"] = env["OS_REGION_NAME"]
        # os.environ["OS_STORAGE_URL"] = self.keyid = env["OS_STORAGE_URL"]
        # os.environ["OS_AUTH_TOKEN"] = self.key = env["OS_AUTH_TOKEN"]
        os.environ["OS_PASSWORD"] = self.password = env["OS_PASSWORD"]
        os.environ["RESTIC_PASSWORD"] = self.restic_password = env["OS_RESTIC_PASSWORD"]
        os.environ["RESTIC_REPOSITORY"] = self.uri
        os.environ["RESTIC_HOST"] = self.hostarg
        os.environ["HOST"] = self.hostarg
        os.environ["URI"] = self.uri

    @property
    def uri(self):
        """
        :return: the swift uri with self.containername and self.name
        """
        return f"swift:{self.container_name}:/{self.name}"


def set_env_value(path: Path, target: str, value: str) -> None:
    """update/set environment variables in the .env file, keeping comments intact

    set_env_value(Path('.env'), 'SCHEMA_VERSION', schemaversion)

    Args:
        path: pathlib.Path designating the .env file
        target: key to write, probably best to use UPPERCASE
        value: string value to write, or anything that converts to a string using str()
    """
    with path.open(mode="r") as env_file:
        # open the .env file and read every line in the inlines
        inlines = env_file.read().split("\n")

    outlines = []  # lines for output
    geschreven = False
    for line in inlines:
        if line.strip().startswith("#"):
            # ignore comments
            outlines.append(line)
            continue
        # remove redundant whitespace
        line = line.strip()
        if not line:
            # remove empty lines
            continue
        # convert to tuples
        key, oldvalue = line.split("=", 1)
        # clean the key and value
        key = key.strip()
        if key == target:
            # add the new tuple to the lines
            outlines.append(f"{key}={value}")
            geschreven = True
        else:
            # or leave it as it is
            outlines.append(line)
    if not geschreven:
        # if target in .env file
        outlines.append(f"{target.strip().upper()}={value.strip()}")
    with path.open(mode="w") as env_file:
        # write outlines to .env file
        env_file.write("\n".join(outlines))
        env_file.write("\n")


def read_dotenv(path: Path) -> dict:
    """Reads a .env file at the specified path and returns a dictionary of key - value pairs.

    If the specified key is not found in the.env file, the function prompts the user to enter a value for the key,
    with a default value provided.The key-value pair is then appended to the.env file.

    Args:
        path(Path): The path to the .env file.

    Returns:
        dict: A dictionary containing the key - value pairs in the .env file."""

    if existing := _dotenv_settings.get(path):
        # 'cache'
        return existing

    items = {}
    with path.open(mode="r") as env_file:
        for line in env_file:
            # remove comments and redundant whitespace
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                # just a comment, skip
                # or key without value? invalid, prevent crash:
                continue

            # convert to tuples
            k, v = line.split("=", 1)

            # clean the tuples and add to dict
            items[k.strip()] = v.strip()

    _dotenv_settings[path] = items
    return items


def check_env(
    path: Path,
    key: str,
    default: typing.Optional[str],
    comment: str,
    prefix: str | None = None,
    postfix: str | None = None,
):
    """
    Test if key is in .env file path, appends prompted or default value if missing.
    """
    env = read_dotenv(path)
    if key in env:
        return env[key]
    with path.open(mode="r+") as env_file:
        # get response value from prompt/input
        response = input(f"Enter value for {key} ({comment})\n default=`{default}`: ")
        # if response_value is none make value default else value is response_value
        value = response.strip() or default
        if prefix:
            value = prefix + value
        if postfix:
            value += postfix
        env_file.seek(0, 2)
        # write key and value to .env file
        env_file.write(f"\n{key.upper()}={value}\n")

        # update in memory too:
        env[key] = value
        return value


# the order in which the backup will be saved
# Swift is the most secure for us, because of the keys
# B2 is also very secure, only less
# SFTP is the least secure, if were talking about remote saving
# Local can be a good option, but if your pc got broken, you have lost your backup
CONNECTION_CLASS_MAP = OrderedDict(
    os=SwiftRepository,
    b2=B2Repository,
    sftp=SFTPRepository,
    local=LocalRepository,
)


def cli_repo(connection_choice=None, restichostname=None):
    """
    Create a repository object and set up the connection to the backend.
    :param connection_choice: choose where you want to store the repo (local, SFTP, B2, swift)
    :param restichostname: which hostname to force for restic, or blank for default.
    :return: repository object
    """
    env = read_dotenv(DOTENV)
    if restichostname:
        set_env_value(DOTENV, "RESTICHOSTNAME", restichostname)
    if connection_choice is None:
        # search for the most important backup and use it as default
        for connection_choice in CONNECTION_CLASS_MAP.keys():
            connection_lowercase = connection_choice.lower()
            if f"{connection_choice.upper()}_NAME" in env:
                break
    else:
        connection_lowercase = connection_choice.lower()
    repoclass = CONNECTION_CLASS_MAP[connection_lowercase]
    print("Use connection: ", connection_lowercase)
    repo = repoclass()
    repo.setup()
    return repo


@task
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
def backup(c, target="", connection_choice=None, message=None, verbose=True):
    """Performs a backup operation using restic on a local or remote/cloud file system.

    Args:
        target (str): The path of the file or directory to backup. Defaults to an empty string.
        connection_choice (str): The name of the connection to use for the backup.
            Defaults to None, which means the default connection will be used.
        message (str): A message to attach to the backup snapshot.
            Defaults to None, which means no message will be attached.
        verbose (bool): If True, outputs more information about the backup process. Defaults to False.

    Raises:
        Exception: If an error occurs during the backup process.

    """
    # After 'backup', a file path can be specified.In this script, a test file is chosen at './test/testbestand'.
    # It can be replaced with the desired path over which restic should perform a backup.
    # The option --verbose provides more information about the backup that is made.It can be removed if desired.

    # By using additions, it is possible to specify what should be included:
    # --exclude ,Specified one or more times to exclude one or more items.
    # --iexclude, Same as --exclude but ignores the case of paths.
    # --exclude-caches, Specified once to exclude folders containing this special file.
    # --exclude-file, Specified one or more times to exclude items listed in a given file.
    # --iexclude-file, Same as exclude-file but ignores cases like in --iexclude.
    # --exclude-if-present 'foo', Specified one or more times to exclude a folder’s content if it contains.
    # a file called 'foo' (optionally having a given header, no wildcards for the file name supported).
    # --exclude-larger-than 'size', Specified once to excludes files larger than the given size.
    # Please see 'restic help backup' for more specific information about each exclude option.

    cli_repo(connection_choice).backup(c, verbose, target, message)


@task
def restore(c, connection_choice=None, snapshot="latest", target="", verbose=True):
    """
    The restore function restores the latest backed-up files by default and puts them in a restore folder.

    IMPORTANT: please provide -t for the path where the restore should go. Also remember to include -c for the service
    where the backup is stored.

    :param connection_choice: the service where the files are backed up, e.g., 'local' or 'openstack'.
    :param snapshot: the ID where the files are backed up, default value is 'latest'.
    :param target: the location where the backup should be restored.
    :param verbose: display verbose logs (inv restore -v).
    :return: None
    """
    # For restore, --target is the location where the restore should be placed, --path is the file/path that should be
    # retrieved from the repository.
    # 'which_restore' is a user input to enable restoring an earlier backup (default = latest).
    # Stop the postgres services.
    c.run("docker-compose stop -t 1 pg-0 pg-1 pgpool", warn=True, hide=True)

    # Get the volumes that are being used.
    docker_inspect: invoke.Result = c.run(
        "docker inspect pg-0 pg-1", hide=True, warn=True
    )
    if docker_inspect.ok:
        # Only if ok, because if pg-0 and pg-1 do not exist, this does not exist either, and nothing needs to be removed
        inspected = json.loads(docker_inspect.stdout)
        volumes_to_remove = []
        for service in inspected:
            volumes_to_remove.extend(
                mount["Name"]
                for mount in service["Mounts"]
                if mount["Type"] == "volume"
            )
        # Remove the containers before a volume can be removed.
        c.run("docker-compose rm -f pg-0 pg-1")
        # Remove the volumes.
        for volume_name in volumes_to_remove:
            c.run(f"docker volume rm {volume_name}")

    cli_repo(connection_choice).restore(c, verbose, target, snapshot)
    # print("`inv up` to restart the services.")


@task(iterable=["tag"])
def snapshots(c, connection_choice=None, tag=None, n=1):
    """
    With this je can see per repo which repo is made when and where, the repo-id can be used at inv restore as an option
    :param connection_choice: service
    :param tag: files, stream ect
    :param n: amount of snapshot to view, default=1(latest)
    :return: None
    """
    # if tags is None set tag to default tags
    if tag is None:
        tag = ["files", "stream"]

    cli_repo(connection_choice).snapshot(c, tags=tag, n=n)


@task()
def run(c, connection_choice=None):
    """
    This function prepares for restic and runs the input command until the user types "exit".

    :param connection_choice (str): The connection name of the repository.
    """

    cli_repo(connection_choice).prepare_for_restic(c)
    while (command:=input("> ")) != "exit":
        print(c.run(command, hide=True, warn=True, pty=True))
