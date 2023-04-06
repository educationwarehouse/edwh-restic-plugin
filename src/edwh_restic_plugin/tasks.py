import invoke
from invoke import task
import datetime
import io
import os
import re
import sys
import typing
import json
from pathlib import Path
from collections import defaultdict, OrderedDict


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

        if len(files) == 0:
            print("no files found with target:", target)
            sys.exit(255)

        return files

    def execute_files(self, c, verbose: bool, target: str, verb: str, message: str = None, snapshot: str = "latest"):
        self.prepare_for_restic(c)
        os.environ["SNAPSHOT"] = snapshot

        if message is None:
            message = str(datetime.datetime.now()) + " localtime"

        os.environ["MSG"] = message

        files = self.get_scripts(target, verb)

        snapshots_created = []
        for file in files:
            if verbose:
                print("running", file)

            script_stdout = c.run(file).stdout
            snapshot = self.get_snapshot_from(script_stdout)
            snapshots_created.append(snapshot)

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
            comment="reponaam is verplicht",
        )
        self.password = check_env(
            DOTENV,
            "LOCAL_PASSWORD",
            default=None,
            comment="maak een wachtwoord, bewaar deze goed.",
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
        return self.name


class SFTPRepository(Repository):
    def setup(self):
        """Zorg ervoor dat de settings in de .env file staan"""
        check_env(
            DOTENV,
            "SFTP_NAME",
            default=None,
            comment="reponaam is verplicht",
        )
        check_env(
            DOTENV,
            "SFTP_PASSWORD",
            default=None,
            comment="maak een wachtwoord, bewaar deze goed.",
        )
        check_env(
            DOTENV,
            "SFTP_HOSTNAME",
            default=None,
            comment="voer hier de juiste hostname in.",
        )  #

    def prepare_for_restic(self, c):
        """lees variableen uit de .env file"""
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
            SSH config bestand niet (goed) geconfigureerd, configureer volgens volgend format:
            Host romy
                HostName romy.edwh.nl
                User ubuntu
                IdentityFile /home/romy/romy.key
            Om een nieuwe host op te slaan in de ssh config file ga naar ~/.ssh en ga daar de config file in.
            Voor meer informatie lees de ssh_config manual (man ssh_config)
            """
            )
            exit(1)

    @property
    def uri(self):
        return "sftp:" + self.hostname + ":" + self.name


class B2Repository(Repository):
    def setup(self):
        """Zorg ervoor dat de settings in de .env file staan"""
        check_env(
            DOTENV,
            "B2_NAME",
            default=None,
            comment="reponaam is verplicht",
        )
        check_env(
            DOTENV,
            "B2_PASSWORD",
            default=None,
            comment="maak een wachtwoord, bewaar deze goed.",
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
            comment="voer hier de juiste KEY ID in.",
        )
        check_env(
            DOTENV,
            "B2_ACCOUNT_KEY",
            default=None,
            comment="gvoer hier de juiste KEY in.",
        )

    def prepare_for_restic(self, c):
        """lees variableen uit de .env file"""
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
        return "b2:" + self.bucketname + ":" + self.name


class SwiftRepository(Repository):
    def setup(self):
        """Zorg ervoor dat de settings in de .env file staan"""
        check_env(
            DOTENV,
            "OS_AUTH_URL",
            default="https://identity.stack.cloudvps.com/v2.0",
            comment="Auth URL voor deze openstack omgeving",
        )
        check_env(
            DOTENV,
            "OS_TENANT_ID",
            default=None,
            comment='Tenant naam, komt uit de openrc file, of uit de auth info, lijkt op "f8d15....269"',
        )
        check_env(
            DOTENV,
            "OS_TENANT_NAME",
            default="BST000425 productie-backups",
            comment="Projectnaam binnen openstack, bijvoorbeeld 'BST000425 productie-backups'",
        )
        check_env(
            DOTENV,
            "OS_REGION_NAME",
            default="NL",
            comment="NL wordt ondersteund, andere zijn onbekend.",
        )
        check_env(
            DOTENV,
            "OS_USERNAME",
            default="backup@edwh.nl",
            comment="Username is het openstack username",
        )
        check_env(
            DOTENV,
            "OS_PASSWORD",
            default=None,
            comment="Password behorend bij de openstack gebruiker",
        )
        check_env(
            DOTENV,
            "OS_CONTAINERNAME",
            default="backups",
            comment="Objectstore container naam, zou automatisch aangemaakt moeten worden als het niet bestaat.",
        )
        check_env(
            DOTENV,
            "OS_NAME",
            default=None,
            comment="Repository naam binnen de bucket",
        )
        check_env(
            DOTENV,
            "OS_RESTIC_PASSWORD",
            default=None,
            comment="Wachtwoord van de repository binnen de container",
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
        """lees variableen uit de .env file"""
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
        outlines.append(f"{target.strip().upper()}={str(value).strip()}")
    with path.open(mode="w") as env_file:
        env_file.write("\n".join(outlines))
        env_file.write("\n")


def read_dotenv(path: Path) -> dict:
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
            response = input(
                f"Enter value for {key} ({comment})\n default=`{default}`: "
            )
            value = response.strip() or default
            if prefix:
                value = prefix + value
            if postfix:
                value += postfix
            env_file.seek(0, 2)
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
    # Na 'backup' kan een bestandspad worden opgegeven, in dit script is gekozen voor een testbestand.
    # './test/testbestand' kan vervangen worden door het gewenste pad waarover restic een backup moet uitvoeren.
    # De optie --verbose geeft meet informatie mee over de backup die gemaakt is. Deze kan ev. weggehaald worden.

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
    # cli_repo(c, connection_choice).backup(c, files, message)

    try:
        cli_repo(c, connection_choice).backup(c, verbose, target, "backup", message)
    except:
        print(f"[ERROR] while backing up to {connection_choice or 'default connection'}")
        raise


@task
def restore(
        c,
        connection_choice=None,
        snapshot="latest",
        target="",
        verbose=False,
) -> None:
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
def snapshots(c, connection_choice=None, tag=None, n=1) -> None:
    """
    With this je can see per repo which repo is made when and where, the repo-id can be used at inv restore as an option
    :param c: invoke
    :param connection_choice: service
    :param tag: files, stream ect
    :param n: amount of snapshot to view, default=1(latest)
    :return: None
    """
    if tag is None:
        tag = ["files", "stream"]

    cli_repo(c, connection_choice).snapshot(c, tags=tag, n=n)
