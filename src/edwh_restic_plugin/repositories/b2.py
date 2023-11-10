import os

from invoke import Context

from . import Repository, register


@register(priority=2)
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
        self.check_env(
            "B2_NAME",
            default=None,
            comment="Repository name  is mandatory (directory)",
        )
        self.check_env(
            "B2_PASSWORD",
            default=None,
            comment="Create a password, keep it safe.",
        )
        self.check_env(
            "B2_BUCKETNAME",
            default=None,
            comment="Use the correct bucketname (directory above repo name",
        )
        self.check_env(
            "B2_ACCOUNT_ID",
            default=None,
            comment="enter the correct KEY ID here.",
        )
        self.check_env(
            "B2_ACCOUNT_KEY",
            default=None,
            comment="enter the correct KEY here.",
        )

    def prepare_for_restic(self, _: Context):
        """read variables out of .env file"""
        env = self.env_config

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
