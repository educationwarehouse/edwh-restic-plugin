from collections import defaultdict, OrderedDict

import invoke
from invoke import task, run, Result, Context

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


class Repository:

    # _targets: a list of file and directory paths that should be included in the backup.
    # _excluded: a list of file and directory paths that should be excluded from the backup.
    # _stream_backup_cmd: a command line string that will be used to create a backup of a stream.
    # _stream_restore_cmd: a command line string that will be used to restore a backup from a stream.
    # _stream_filename: the name of the file where the stream is saved.
    # _should_exist: the path of a file that should exist.


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
    # _stream_backup_cmd: a command line string that will be used to create a backup of a stream.
    _stream_backup_cmd = "docker-compose run -T  --rm pg-0 pg_dump --format=p --dbname=backend --clean --create -h pgpool -U postgres"
    # _stream_restore_cmd: a command line string that will be used to restore a backup from a stream.
    _stream_restore_cmd = "cat - > ./migrate/data/database_to_restore.sql"
    # naam van de stream, dus waar die in zit
    _stream_filename = "/postgres-backend-database.sql"
    _should_exist = "/.env"

    def __init__(self) -> None:
        super().__init__()
        self._restichostname = read_dotenv(DOTENV).get(
            "RESTICHOSTNAME"
        )  # or None als niet voorkomt

    @property
    def uri(self):
        """Return de prefix nodig voor restic om het protocol aan te geven, bijvoorbeeld sftp:hostname:"""
        raise NotImplementedError("Prefix onbekend op base class")

    def setup(self):
        """Zorg ervoor dat de settings in de .env file staan"""
        raise NotImplementedError("Setup undefined")

    def prepare_for_restic(self, c):
        """No environment variables need be defined for local"""
        raise NotImplementedError("Prepare for restic undefined")

    def configure(self, c):
        self.prepare_for_restic(c)
        print("configure")
        # eerst ervoor zorgen dat restic up-to-date is
        c.run("sudo restic self-update", hide=True, warn=True)
        # Dit is de command die gebruik wordt om de omgevingsvariabelen goed te configureren.
        c.run(f"restic init --repository-version 2 -r {self.uri}")

    @property
    def hostarg(self):
        return f" --host {self._restichostname} " if self._restichostname else ""

    @property
    def targets(self):
        # Hierin staat de target
        return " ".join(self._targets)

    @property
    def excluded(self):
        # wat moet er uitgesloten worden?
        return " --exclude ".join(self._excluded)

    def get_snapshot_from(self, stdout: str):
        snapshots_ids = re.findall(r"snapshot (.*?) saved", stdout)
        return snapshots_ids[-1] if snapshots_ids else None

    def get_scripts(self, target, verb):
        print("target =", target, "verb =", verb)
        files = []
        print(verb + "_" + target + "*")
        for file in DEFAULT_BACKUP_FOLDER.glob(verb + "_" + target + "*"):
            files.append(str(file))

        # check if no files are found
        if len(files) == 0:
            print("no files found with target:", target)
            sys.exit(255)

        return files

    def execute_files(self, c, verbose: bool, target: str, verb: str, message: str = None, snapshot: str = "latest"):
        self.prepare_for_restic(c)
        os.environ["SNAPSHOT"] = snapshot

        if message is None:
            message = str(datetime.datetime.now()) + " localtime"

        # set MSG in envirement for sh files
        os.environ["MSG"] = message

        files = self.get_scripts(target, verb)

        snapshots_created = []
        # run all backup/restore files
        for file in tqdm(files):
            if verbose:
                print("running", file)

            script_stdout = c.run(file).stdout
            snapshot = self.get_snapshot_from(script_stdout)
            snapshots_created.append(snapshot)

        # send message with backup. see message for more info
        tags = ["message"] + snapshots_created
        c.run(
            f"restic {self.hostarg} -r {self.uri} backup --tag {','.join(tags)} --stdin --stdin-filename message",
            in_stream=io.StringIO(message),
        )

    def backup(self, c, verbose: bool, target: str, verb: str, message: str):
        self.execute_files(c, verbose, target, verb, message)

    def restore(self, c, verbose: bool, target: str, verb: str, snapshot: str = "latest"):
        self.execute_files(c, verbose, target, verb, None, snapshot)

    def check(self, c):
        self.prepare_for_restic(c)
        # Dit is de command die gebruik wordt om een check uit te voeren.
        print("check")
        c.run(f"restic {self.hostarg} -r {self.uri} check --read-data")

    def snapshot(self, c, tags: list = None, n=2):
        if tags is None:
            tags = ["files", "stream"]

        self.prepare_for_restic(c)
        # Dit is de command die gebruik wordt om een snapshot uit te voeren.
        tags = "--tag " + " --tag ".join(tags) if tags else ""
        stdout = c.run(
            f"restic {self.hostarg} -r {self.uri} snapshots --latest {n} {tags} -c",
            hide=True,
        ).stdout

        snapshots = re.findall(r"^([0-9a-z]{8})\s", stdout, re.MULTILINE)
        main_tag_per_snapshot = {
            snapshot: re.findall(rf"^{snapshot}.*?(\w*)$", stdout, re.MULTILINE)
            for snapshot in snapshots
            # snapshot: re.findall(rf"^{snapshot}", stdout) for snapshot in snapshots
        }

        message_snapshot_per_snapshot = defaultdict(
            list
        )  # key is source, value is snapshot containing the message
        for snapshot, possible_tag_names in main_tag_per_snapshot.items():
            tag_name = possible_tag_names[0]
            if tag_name not in ["message"]:
                continue
            # print(snapshot, tag_name)
            for _, is_message_for_snapshot_id in re.findall(
                    rf"\n{snapshot}.*(\n\s+(.*)\n)+", stdout
            ):
                message_snapshot_per_snapshot[is_message_for_snapshot_id].append(
                    snapshot
                )

        for snapshot, message_snapshots in message_snapshot_per_snapshot.items():
            # print de inhoud van de message  die hoort bij deze snapshot
            restore_output = c.run(
                f"restic {self.hostarg} -r {self.uri} dump {message_snapshots[0]} --tag message message",
                hide=True,
                warn=True,
            ).stdout
            message = restore_output.strip()
            stdout = re.sub(
                rf"\n{snapshot}(.*)\n", rf"\n{snapshot}\1 : [{message}]\n", stdout
            )
        print(stdout)

    def new_snapshots(self, c, tags: list = None, n=2, messages=True):
        tags = "--tag " + " --tag ".join(tags) if tags else ""


class LocalRepository(Repository):
    def setup(self):
        """Zorg ervoor dat de settings in de .env file staan"""
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
        os.environ["RESTIC_PASSWORD"] = self.password = env["LOCAL_PASSWORD"]

    @property
    def uri(self):
        """
        Get the URI of the class instance.

        The function returns the value of the 'name' attribute, which represents the URI of the class instance.
        """
        return self.name


class SFTPRepository(Repository):
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
        os.environ["RESTIC_PASSWORD"] = self.password
        try:
            c.run(f'ssh {self.hostname} "exit"')
        except:
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
        return "sftp:" + self.hostname + ":" + self.name


class B2Repository(Repository):
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
            comment="voer hier de juiste bucketname in.",
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
        self.bucketname = env["B2_BUCKETNAME"]
        self.keyid = env["B2_ACCOUNT_ID"]
        self.key = env["B2_ACCOUNT_KEY"]
        os.environ["B2_ACCOUNT_ID"] = self.keyid
        os.environ["B2_ACCOUNT_KEY"] = self.key
        os.environ["RESTIC_PASSWORD"] = self.password
        os.environ["HOST"] = self.hostarg
        os.environ["URI"] = self.uri

    @property
    def uri(self):
        """
        :return: uri of b2 with self.bucketname and self.name
        """
        return "b2:" + self.bucketname + ":" + self.name


class SwiftRepository(Repository):
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
        self.containername = env["OS_CONTAINERNAME"]
        os.environ["OS_USERNAME"] = env["OS_USERNAME"]
        os.environ["OS_AUTH_URL"] = env["OS_AUTH_URL"]
        os.environ["OS_TENANT_ID"] = env["OS_TENANT_ID"]
        os.environ["OS_TENANT_NAME"] = env["OS_TENANT_NAME"]
        os.environ["OS_REGION_NAME"] = env["OS_REGION_NAME"]
        # os.environ["OS_STORAGE_URL"] = self.keyid = env["OS_STORAGE_URL"]
        # os.environ["OS_AUTH_TOKEN"] = self.key = env["OS_AUTH_TOKEN"]
        os.environ["OS_PASSWORD"] = self.password = env["OS_PASSWORD"]
        os.environ["RESTIC_PASSWORD"] = self.restic_password = env["OS_RESTIC_PASSWORD"]
        os.environ["HOST"] = self.hostarg
        os.environ["URI"] = self.uri

    @property
    def uri(self):
        """
        :return: de swift uri met self.containername and self.name
        """
        return "swift:" + self.containername + ":/" + self.name


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
        outlines.append(f"{target.strip().upper()}={str(value).strip()}")
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
    if key not in env:
        with path.open(mode="r+") as env_file:
            # get response value from promt/input
            response = input(
                f"Enter value for {key} ({comment})\n default=`{default}`: "
            )
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
    else:
        return env[key]


CONNECTION_CLASS_MAP = OrderedDict(
    os=SwiftRepository,
    b2=B2Repository,
    sftp=SFTPRepository,
    local=LocalRepository,
)


def cli_repo(c, connection_choice=None, restichostname=None):
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
        #  zoek de meest belangrijke backup mogelijk op als default.
        for connection_choice in CONNECTION_CLASS_MAP.keys():
            connection_lowercase = connection_choice.lower()
            if connection_choice.upper() + "_NAME" in env:
                break
    else:
        connection_lowercase = connection_choice.lower()
    repoclass = CONNECTION_CLASS_MAP[connection_lowercase]
    print("Gebruikt connectie: ", connection_lowercase)
    repo = repoclass()
    repo.setup()
    return repo


@task
def configure(c, connection_choice=None, restichostname=None):
    """Setup or update the backup command for your environment.
    connection_choice: choose where you want to store the repo (local, SFTP, B2, swift)
    restichostname: which hostname to force for restic, or blank for default.
    """

    # Er is gekozen om voor elke repository een hoofdpad aan te maken genaamd 'backups'.
    # Deze kan eventueel veranderd en weggehaald worden indien gewenst.
    # Een password wordt slechts bij een aantal fucnties meegegeven.
    cli_repo(c, connection_choice, restichostname).configure(c)


@task
def backup(c, target="", connection_choice=None, message=None, verbose=False):
    """
    Performs a backup operation using restic, a backup program that encrypts and compresses data
    and stores it to a repository. The repository can be on a local or remote file system or in a cloud storage.

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

    # Door gebruik te maken van toevoegingen kan specifiek weergegeven worden wat meegenomen moet worden:
    # --exclude ,Specified one or more times to exclude one or more items.
    # --iexclude, Same as --exclude but ignores the case of paths.
    # --exclude-caches, Specified once to exclude folders containing this special file.
    # --exclude-file, Specified one or more times to exclude items listed in a given file.
    # --iexclude-file, Same as exclude-file but ignores cases like in --iexclude.
    # --exclude-if-present 'foo', Specified one or more times to exclude a folder’s content if it contains.
    # a file called 'foo' (optionally having a given header, no wildcards for the file name supported).
    # --exclude-larger-than 'size', Specified once to excludes files larger than the given size.
    # Please see 'restic help backup' for more specific information about each exclude option.

    cli_repo(c, connection_choice).backup(c, verbose, target, "backup", message)


@task
def restore(
    c,
    connection_choice=None,
    snapshot="latest",
    target="",
    verbose=False
):
    """
    IMPORTANT: please fill in -t for a path where the restore can go. Also remember to put in a -c for at what service
    you stored the backup.

    the restore function restores the latest backup-ed files by default and puts it into a restore folder.
    :param c: invoke
    :param connection_choice: service where the files are backed op, think about local or openstack
    :param snapshot: by default snapshot is latest, snapshot is the id where the files are backed-up
    :param target: location where the backup gets dumped
    :param verbose: logs(inv restore -v)
    :return: None
    """
    # Bij restore is --target de locatie waarin de restore geplaatst mag worden, --path is het bestand/pad die uit de
    # repository gehaald mag worden. 'which_restore' is hierbij een input van de gebruiker, om zo een eerdere restore
    # mogelijk te maken (default = latest).
    # stoppen van de postgres services
    c.run("docker-compose stop -t 1 pg-0 pg-1 pgpool", warn=True, hide=True)

    # opvragen welke volumes gebruikt worden
    docker_inspect: invoke.Result = c.run(
        "docker inspect pg-0 pg-1", hide=True, warn=True
    )
    if docker_inspect.ok:
        # alleen als ok, want als pg-0 en pg-1 niet bestaan, is dit er niet, en hoeft er ook niets verwijderd te worden.
        inspected = json.loads(docker_inspect.stdout)
        te_verwijderen_volumes = []
        for service in inspected:
            for mount in service["Mounts"]:
                if mount["Type"] == "volume":
                    te_verwijderen_volumes.append(mount["Name"])
        # containers verwijderen, voordat een volume verwijderd kan worden
        c.run("docker-compose rm -f pg-0 pg-1")
        # volumes verwijderen
        for volume_name in te_verwijderen_volumes:
            c.run("docker volume rm " + volume_name)

    cli_repo(c, connection_choice).restore(c, verbose, target, "restore", snapshot)
    # print("`inv up` om de services te herstarten ")


@task(iterable=['tag'])
def snapshots(c, connection_choice=None, tag=None, n=1):
    """
    With this je can see per repo which repo is made when and where, the repo-id can be used at inv restore as an option
    :param c: invoke
    :param connection_choice: service
    :param tag: files, stream ect
    :param n: amount of snapshot to view, default=1(latest)
    :return: None
    """
    # if tags is None set tag to default tags
    if tag is None:
        tag = ["files", "stream"]

    cli_repo(c, connection_choice).snapshot(c, tags=tag, n=n)
