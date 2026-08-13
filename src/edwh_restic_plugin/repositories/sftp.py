import os
import textwrap

from restic_reaper import SftpConfig, wipe_repository_sync

from ..exceptions import ResticConnectionError
from . import Repository, register


@register("sftp", priority=10)
class SFTPRepository(Repository):
    def __init__(self):
        super().__init__()
        self.hostname = None
        self.password = None
        self.name = None

    def setup(self):
        """Ensure the required settings are defined in the .env file."""
        self.check_env(
            "SFTP_NAME",
            default=None,
            comment="Repository name  is mandatory (directory)",
        )
        self.check_env(
            "SFTP_PASSWORD",
            default=None,
            comment="Create a password, keep it safe.",
        )
        self.check_env(
            "SFTP_HOSTNAME",
            default=None,
            comment="Use the correnct hostname (directory above the repo name)",
        )  #
        self.check_env(
            "SFTP_USERNAME",
            default=None,
            comment="Username for connecting to the SFTP server (for wipe operations; optional).",
        )
        self.check_env(
            "SFTP_PRIVATE_KEY",
            default=None,
            comment="Path to the private key file used for SFTP authentication (for wipe operations; optional).",
        )

    def prepare_for_restic(self, c):
        """read out of .env file"""
        env = self.env_config

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
            raise ResticConnectionError(
                f"""could not ssh to '{self.hostname}'.
                SSH config file not (properly) configured, configure according to the following format:
                Host romy
                HostName romy.edwh.nl
                User ubuntu
                IdentityFile ~/romy.key
                To save a new host in the ssh config file, go to ~/.ssh and edit the config file there.
                For more information, read the ssh_config manual (man ssh_config)
                """,
            )

    @property
    def uri(self):
        """
        :return: sftp uri with self.hostname and self.name
        """
        return f"sftp:{self.hostname}:{self.name}"

    @property
    def bucket(self):
        return self.env_config["SFTP_NAME"]

    def prepare_rclone_config(self):
        env = self.env_config
        return textwrap.dedent(
            f"""
            type = sftp
            host = {env["SFTP_HOSTNAME"]}
            user = {env["SFTP_USERNAME"]}
            key_user_agent = true
            shell_type = unix
        """,
        )

    def wipe(self, dry: bool = False):
        env = self.env_config
        config = SftpConfig(
            endpoint=env["SFTP_HOSTNAME"],
            user=env["SFTP_USERNAME"],
            key=env["SFTP_PRIVATE_KEY"],
            root=env["SFTP_NAME"],
        )

        return wipe_repository_sync(dry=dry, **config)  # ty: ignore[invalid-argument-type]
