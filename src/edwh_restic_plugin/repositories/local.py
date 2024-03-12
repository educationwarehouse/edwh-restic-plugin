import os

from edwh.helpers import generate_password
from invoke import Context

from . import Repository, register


@register(priority=4)
class LocalRepository(Repository):
    def __init__(self):
        super().__init__()
        self.password = None
        self.name = None

    def setup(self):
        """Ensure the required settings are defined in the .env file."""
        self.name = self.check_env(
            "LOCAL_NAME",
            default=None,
            comment="Repository name  is mandatory (directory)",
        )
        self.password = self.check_env(
            "LOCAL_PASSWORD",
            default=generate_password(silent=True),
            comment="Create a password, keep it safe.",
        )

    def prepare_for_restic(self, _: Context):
        """No environment variables need be defined for local"""
        env = self.env_config

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
        return getattr(self, "name", None) or self.env_config.get("LOCAL_NAME")
