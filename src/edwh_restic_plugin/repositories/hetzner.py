import os

from edwh.helpers import generate_password
from invoke import Context

from . import register
from .s3 import S3Repository


@register()
class HetznerRepository(S3Repository):
    def setup(self) -> None:
        self.check_env(
            "HETZNER_BUCKET",
            default=None,
            comment="Bucket to store your backups",
        )

        self.check_env("HETZNER_REGION", default="fsn1", comment="Short name of the region (fsn1, nbg1, hel1)")

        self.check_env("HETZNER_ACCESS_KEY_ID", default=None, comment="(Customer) Access Key")
        self.check_env("HETZNER_SECRET_ACCESS_KEY", default=None, comment="(Customer) Access Secret")

        self.check_env(
            "HETZNER_PASSWORD",
            default=generate_password(silent=True),
            comment="Create a Restic password, keep it safe.",
        )

    def prepare_for_restic(self, _: Context) -> None:
        env = self.env_config
        os.environ["RESTIC_PASSWORD"] = env["HETZNER_PASSWORD"]
        os.environ["RESTIC_REPOSITORY"] = self.uri

        os.environ["AWS_DEFAULT_REGION"] = env["HETZNER_REGION"]
        os.environ["AWS_ACCESS_KEY_ID"] = env["HETZNER_ACCESS_KEY_ID"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = env["HETZNER_SECRET_ACCESS_KEY"]

    @property
    def uri(self) -> str:
        env = self.env_config
        return "s3:{region}.your-objectstorage.com/{bucket}".format(
            account_id=env.get("HETZNER_ACCOUNT_ID", "?"),
            region=env.get("HETZNER_REGION", "?"),
            bucket=env.get("HETZNER_BUCKET", "?"),
        )
