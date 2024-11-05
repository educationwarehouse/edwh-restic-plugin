import os

from edwh.helpers import generate_password
from invoke import Context

from . import register
from .s3 import S3Repository


@register()
class R2Repository(S3Repository):
    # https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm

    def setup(self) -> None:
        self.check_env(
            "R2_NAME",
            default=None,
            comment="Bucket to store your backups",
        )

        self.check_env(
            "R2_ACCOUNT_ID",
            default=None,
            comment="The ID of the account this bucket belongs to. "
            "Usually seen in the url: https://dash.cloudflare.com/your-id-goes-here/",
        )

        self.check_env(
            "R2_PASSWORD",
            default=generate_password(silent=True),
            comment="Create a Restic password, keep it safe.",
        )

        self.check_env("R2_ACCESS_KEY_ID", default=None, comment="(Customer) Access Key")
        self.check_env("R2_SECRET_ACCESS_KEY", default=None, comment="(Customer) Access Secret")

    def prepare_for_restic(self, _: Context) -> None:
        env = self.env_config
        os.environ["RESTIC_PASSWORD"] = env["R2_PASSWORD"]
        os.environ["RESTIC_REPOSITORY"] = self.uri

        os.environ["AWS_ACCESS_KEY_ID"] = env["R2_ACCESS_KEY_ID"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = env["R2_SECRET_ACCESS_KEY"]

    @property
    def uri(self) -> str:
        env = self.env_config
        return "s3:{account_id}.r2.cloudflarestorage.com/{bucket}".format(
            account_id=env.get("R2_ACCOUNT_ID", "?"),
            bucket=env.get("R2_NAME", "?"),
        )
