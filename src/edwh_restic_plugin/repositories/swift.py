import os

from invoke import Context

from . import Repository, register


@register(
    short_name="os",
    aliases=("swift", "openstack"),
    priority=1,  # high prio
)
class SwiftRepository(Repository):
    def __init__(self):
        super().__init__()
        self.restic_password = None
        self.password = None
        self.container_name = None
        self.name = None

    def setup(self):
        """Ensure the required settings are defined in the .env file."""
        self.check_env(
            "OS_AUTH_URL",
            default="https://auth.teamblue.cloud/v3",
            comment="Auth URL for this openstack environment",
        )
        self.check_env(
            "OS_PROJECT_ID",
            default=None,
            comment='Project ID, comes from the openrc file, or from the auth info, looks like "f8d15....269"',
        )
        self.check_env(
            "OS_PROJECT_NAME",
            default="BST000425 productie-backups",
            comment="Project name within openstack, for example 'BST000425 production backups'",
        )
        self.check_env(
            "OS_PROJECT_DOMAIN_NAME",
            default="transip",
            comment="The domain name of the project",
        )
        self.check_env(
            "OS_REGION_NAME",
            default="NL",
            comment="NL is supported, others are unknown.",
        )
        self.check_env(
            "OS_USERNAME",
            default="backup@edwh.nl",
            comment="Username is the openstack username",
        )
        self.check_env(
            "OS_PASSWORD",
            default=None,
            comment="Password belonging to the openstack user",
        )
        self.check_env(
            "OS_CONTAINERNAME",
            default="backups",
            comment="Objectstore container name, should be created automatically if it doesn't exist.",
        )
        self.check_env(
            "OS_NAME",
            default=None,
            comment="Repository name within the bucket",
        )
        self.check_env(
            "OS_RESTIC_PASSWORD",
            default=None,
            comment="Password of the repository within the container",
        )

    def prepare_for_restic(self, _: Context):
        """read variables out of .env file"""
        env = self.env_config

        self.name = env["OS_NAME"]
        self.container_name = env["OS_CONTAINERNAME"]

        os.environ["OS_USERNAME"] = env["OS_USERNAME"]
        os.environ["OS_AUTH_URL"] = env["OS_AUTH_URL"]
        os.environ["OS_PROJECT_ID"] = env["OS_PROJECT_ID"]
        os.environ["OS_USER_DOMAIN_NAME"] = env["OS_PROJECT_DOMAIN_NAME"]
        os.environ["OS_PROJECT_NAME"] = env["OS_PROJECT_NAME"]
        os.environ["OS_REGION_NAME"] = env["OS_REGION_NAME"]
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
