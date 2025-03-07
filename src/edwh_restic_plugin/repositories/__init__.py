import abc
import contextlib
import datetime
import heapq
import importlib
import importlib.util
import io
import os
import re
import sys
import typing
from collections import OrderedDict, defaultdict
from pathlib import Path

import invoke
from invoke import Context
from invoke.exceptions import AuthFailure
from termcolor import cprint
from tqdm import tqdm
from typing_extensions import NotRequired

from ..env import DOTENV, check_env, read_dotenv
from ..forget import ResticForgetPolicy
from ..helpers import _require_restic, camel_to_snake, fix_tags

# the path where the restic command is going to be executed
DEFAULT_BACKUP_FOLDER = Path("captain-hooks")


class SortableMeta(abc.ABCMeta):
    """
    Allows sorting the class objects (not instances), which is useful for storing the class in a heapq.

    The sort actually doesn't do anything, so you should store a tuple with a priority as the first item.
    The class can then be included simply for lookup, not for any sorting purposes.
    """

    def __lt__(self, other: typing.Any) -> bool:
        return False

    def __gt__(self, other: typing.Any) -> bool:
        return False


class Repository(abc.ABC, metaclass=SortableMeta):
    # these are set via @register:
    _short_name: str
    _aliases: tuple[str, ...]
    _priority: int

    ####################
    # IMPLEMENT THESE: #
    ####################

    @abc.abstractmethod
    def setup(self) -> None:
        """Ensure that the settings are in the .env file"""
        # you probably want some `self.check_env(...)` statements here
        # You need at least a <REPO>_NAME and <REPO>_PASSWORD variable,
        # where <REPO> is the name of your Restic repository type.
        raise NotImplementedError("Setup undefined")

    @abc.abstractmethod
    def prepare_for_restic(self, c: Context) -> None:
        """No environment variables need to be defined for local"""
        # prepare_for_restic implementations should probably start with:
        # env = self.env_config
        # os.environ["RESTIC_REPOSITORY"] = self.uri
        # os.environ["RESTIC_PASSWORD"] = env["<REPO>_PASSWORD"]
        raise NotImplementedError("Prepare for restic undefined")

    @property
    @abc.abstractmethod
    def uri(self) -> str:
        """Return the prefix required for restic to indicate the protocol, for example sftp:hostname:"""
        raise NotImplementedError("Prefix unknown in base class")

    ###########################
    # END OF NOT IMPLEMENTED, #
    #    START BASE CLASS:    #
    ###########################

    def _add_missing_boilerpalte_restic_vars(self):
        """
        HOST, URI, RESTIC_REPOSITORY and RESTIC_HOST are usually the same so if those aren't set yet, \
            use a sensible default
        """
        os.environ["HOST"] = os.environ.get("HOST") or self.hostarg
        os.environ["URI"] = os.environ.get("URI") or self.uri
        os.environ["RESTIC_HOST"] = os.environ.get("RESTIC_HOST") or self.hostarg
        os.environ["RESTIC_REPOSITORY"] = os.environ.get("RESTIC_REPOSITORY") or self.uri

    def prepare_env_for_restic(self, c: Context):
        self.prepare_for_restic(c)  # <- abstract method used by all Repositories
        self._add_missing_boilerpalte_restic_vars()  # <- add $HOST and other common variables that could be missing

    def __repr__(self):
        cls = self.__class__.__name__
        try:
            uri = self.uri
        except Exception:
            uri = "?"

        return f"<{cls}({uri})>"

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

    _env_path: Path
    env_config: dict[str, str]

    def _require_restic(self):
        _require_restic()

    def __init__(self, env_path: Path = DOTENV) -> None:
        super().__init__()
        self._require_restic()
        env_path.touch(exist_ok=True)
        print("start repo init", self.__class__.__name__)
        self._env_path = env_path
        self.env_config = env = read_dotenv(env_path)
        os.environ |= env
        self._restichostname = env.get("RESTICHOSTNAME")  # or None if it is not there
        print("end repo init", self)

    def check_env(
        self,
        key: str,
        default: str | None,
        comment: str,
        prefix: str = None,
        suffix: str = None,
        postfix: str = None,
        path: Path = None,
    ):
        value = check_env(
            key=key,
            default=default,
            comment=comment,
            prefix=prefix,
            suffix=suffix,
            postfix=postfix,
            path=path or self._env_path,
        )

        # update local variant too:
        self.env_config[key] = value
        return value

    def _restic_self_update(self, c: Context) -> None:
        if not c.run("restic self-update", hide=True, warn=True):
            # done
            return

        with contextlib.suppress(AuthFailure):
            return c.sudo("restic self-update", hide=True, warn=True)

    def configure(self, c: Context):
        """Configure the backup environment variables."""
        self.prepare_env_for_restic(c)
        print("configure")
        # First, make sure restic is up-to-date
        self._restic_self_update(c)
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
    def get_snapshot_from(stdout: str) -> str:
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
        c: Context,
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
        self.prepare_env_for_restic(c)

        # set snapshot available in environment for sh files
        os.environ["SNAPSHOT"] = snapshot

        # Here you can make a message that you will see in the snapshots list
        if message is None:
            # If no message is provided, use the current local time as the backup message
            message = f"{datetime.datetime.now()} localtime"

        # set MSG in environment for sh files
        os.environ["MSG"] = message

        # get files by target and verb. see self.get_scripts for more info
        files = self.get_scripts(target, verb)

        snapshots_created = []
        file_codes = []
        # run all backup/restore files
        for file in tqdm(files):
            if verbose:
                print("\033[1m running", file, "\033[0m")

            # run the script by default with pty=True,
            # when the script crashes run the script again but then grab the stdout

            print(f"{file} output: " if verbose else "", file=sys.stderr)
            try:
                ran_script: invoke.runners.Result = c.run(file, hide=not verbose, pty=True)
                file_codes.append(0)
            except invoke.exceptions.UnexpectedExit as e:
                ran_script = e.result
                file_codes.append(e.result.exited)

            snapshot = self.get_snapshot_from(ran_script.stdout)
            snapshots_created.append(snapshot)

        # send message with backup. see message for more info
        # also if a tag in tags is None it will be removed by fix_tags
        if verb != "restore":
            tags = fix_tags(["message", *snapshots_created])
            c.run(
                f"restic {self.hostarg} -r {self.uri} backup --tag {','.join(tags)} --stdin --stdin-filename message",
                in_stream=io.StringIO(message),
                hide=True,
            )

        print("\n\nfile status codes:")

        for filename, status_code in zip(files, file_codes):
            if status_code == 0:
                cprint(f"[success] {filename}", color="green")
            else:
                cprint(f"[failure ({status_code})] {filename}", color="red")

        if worst_status_code := max(file_codes) > 0:
            exit(worst_status_code)

    def backup(self, c, verbose: bool, target: str, message: str | None):
        """
        Backs up the specified target.

        Args:
        - verbose (bool): A flag indicating whether to display verbose output.
        - target (str): The target of the backup (e.g. 'files', 'stream'; default is all types).
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
        self.prepare_env_for_restic(c)
        c.run(f"restic {self.hostarg} -r {self.uri} check --read-data")

    def snapshot(self, c: Context, tags: list[str] = None, n: int = 2, verbose: bool = False):
        """
        a list of all the backups with a message

        Args:
        - tags (list, optional): A list of tags to use for the snapshot. Defaults to None.
        - n (int, optional): The number of latest snapshots to show. Defaults to 2.
        - verbose (bool): Show more info about what's happening?

        Returns:
        None. This function only prints the output to the console.
        """
        # choose to see only the files or the stream snapshots
        if tags is None:
            tags = ["files", "stream"]

        self.prepare_env_for_restic(c)
        tags_flag = "--tag " + " --tag ".join(tags) if tags else ""
        command = f"restic {self.hostarg} -r {self.uri} snapshots --latest {n} {tags_flag} -c"
        if verbose:
            print("$", command, file=sys.stderr)

        stdout = c.run(
            command,
            hide=True,
        ).stdout

        if verbose:
            print(stdout, file=sys.stderr)

        snapshot_lines = re.findall(r"^([0-9a-z]{8})\s", stdout, re.MULTILINE)
        main_tag_per_snapshot = {
            snapshot: re.findall(rf"^{snapshot}.*?(\w*)$", stdout, re.MULTILINE)
            for snapshot in snapshot_lines
            # snapshot: re.findall(rf"^{snapshot}", stdout) for snapshot in snapshots
        }

        message_snapshot_per_snapshot = defaultdict(list)  # key is source, value is snapshot containing the message
        for snapshot, possible_tag_names in main_tag_per_snapshot.items():
            tag_name = possible_tag_names[0]
            if tag_name != "message":
                continue
            for _, is_message_for_snapshot_id in re.findall(rf"\n{snapshot}.*(\n\s+(.*)\n)+", stdout):
                message_snapshot_per_snapshot[is_message_for_snapshot_id].append(snapshot)

        for snapshot, message_snapshots in message_snapshot_per_snapshot.items():
            # print all Restic messages
            command = f"restic {self.hostarg} -r {self.uri} dump {message_snapshots[0]} --tag message message"
            if verbose:
                print("$", command, file=sys.stderr)

            restore_output = c.run(
                command,
                hide=True,
                warn=True,
            ).stdout

            if verbose:
                print(restore_output, file=sys.stderr)

            message = restore_output.strip()
            stdout = re.sub(rf"\n{snapshot}(.*)\n", rf"\n{snapshot}\1 : [{message}]\n", stdout)

            if verbose:
                print("---\n", file=sys.stderr)

        print(stdout)

    def determine_forget_policy(self) -> typing.Optional[ResticForgetPolicy]:
        for option in (
            self._short_name,
            *self._aliases,
            "default",
        ):
            if policy := ResticForgetPolicy.get_or_copy_policy(option):
                return policy

        return None

    def forget(self, c: Context, policy: typing.Optional[ResticForgetPolicy] = None, dry: bool = False) -> None:
        """
        Prepare environment and execute restic forget command.

        Args:
            c (Context): The context in which the task is executed.
            policy (Optional[ResticForgetPolicy]): An optional forgetting policy. If not provided, it will be determined
                based on the active connection's name or aliases.
            dry (bool): If set to True, performs a dry run of the forget operation without making any changes.
                Defaults to False.
        """
        self.prepare_env_for_restic(c)

        policy = policy or self.determine_forget_policy()

        if not policy:
            return cprint("Error: no forget policy could be found. Update your .toml or specify --policy", color="red")

        policy.dry = dry

        args = policy.to_string()

        cprint(f"$ restic forget {args}", color="blue")
        c.run(
            f"restic forget {args}",
            pty=True,
        )

    # noop gt, lt etc methods

    def __gt__(self, other):
        return False

    def __lt__(self, other):
        return False


class RepositoryRegistration(typing.TypedDict):
    short_name: str
    aliases: NotRequired[tuple[str, ...]]
    priority: NotRequired[int]


class RepositoryRegistrations:
    def __init__(self) -> None:
        # _queue is for internal use by heapq only!
        # external api should use .queue !!!
        self._queue: list[tuple[int, typing.Type[Repository], RepositoryRegistration]] = []
        # aliases stores a reference for each name to the Repo class
        self._aliases: dict[str, typing.Type[Repository]] = {}

    def push(self, repo: typing.Type[Repository], settings: RepositoryRegistration):
        priority = settings.get("priority", -1)
        if priority < 0:
            priority = sys.maxsize - priority  # very high int

        heapq.heappush(self._queue, (priority, repo, settings))
        self._aliases[settings["short_name"]] = repo
        for alias in settings.get("aliases", []):
            self._aliases[alias] = repo

    @property
    def queue(self):
        if not self._queue:
            self._find_items()

        return self._queue

    def clear(self):
        self._queue = []
        self._aliases = {}

    def get(self, name: str) -> typing.Type[Repository] | None:
        return self._aliases.get(name)

    def to_sorted_list(self):
        # No need for sorting here; heapq maintains the heap property
        return list(self)

    def to_ordered_dict(self) -> OrderedDict[str, typing.Type[Repository]]:
        ordered_dict = OrderedDict()
        for _, item, settings in self.queue:
            ordered_dict[settings["short_name"]] = item
        return ordered_dict

    def __iter__(self) -> typing.Generator[typing.Type[Repository], None, None]:
        return (item[1] for item in self.queue)

    def __bool__(self):
        return bool(self.queue)

    def _find_items(self) -> None:
        # import all registrations in this folder, so @register adds them to _queue
        package_path = Path(__file__).resolve().parent

        for file_path in package_path.glob("*.py"):
            pkg = file_path.stem
            if not pkg.startswith("__"):
                importlib.import_module(f".{pkg}", package=__name__)


def register(
    short_name: typing.Optional[str] = None,
    aliases: tuple[str, ...] = (),
    priority: int = -1,
    # **settings: Unpack[RepositoryRegistration] # <- not really supported yet!
) -> typing.Callable[[typing.Type[Repository]], typing.Type[Repository]]:
    if isinstance(short_name, type):
        raise SyntaxError("Please call @register() with parentheses!")

    def wraps(cls: typing.Type[Repository]) -> typing.Type[Repository]:
        if not (isinstance(cls, type) and issubclass(cls, Repository)):
            raise TypeError(f"Decorated class {cls} must be a subclass of Repository!")

        name_or_derived = short_name or camel_to_snake(cls.__name__).removesuffix("_repository")

        settings: RepositoryRegistration = {
            "short_name": name_or_derived,
            "aliases": aliases,
            "priority": priority,
        }

        registrations.push(cls, settings)
        cls._short_name = name_or_derived
        cls._aliases = aliases
        cls._priority = priority
        return cls

    return wraps


registrations = RepositoryRegistrations()
