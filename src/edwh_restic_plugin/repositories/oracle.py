import os

from edwh.helpers import generate_password
from invoke import Context

from . import register
from .s3 import S3Repository


@register()
class OracleRepository(S3Repository):
    # https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm

    def setup(self) -> None:
        self.check_env(
            "ORACLE_NAME",
            default=None,
            comment="Bucket to store your backups",
        )

        self.check_env(
            "ORACLE_PASSWORD",
            default=generate_password(silent=True),
            comment="Create a Restic password, keep it safe.",
        )

        # Namespace is shown on the bucket page and transformed into an S3-compat uri automatically
        self.check_env("ORACLE_NAMESPACE", default=None, comment="Oracle Object Storage Namespace")

        # https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/managingcredentials.htm#Working2
        self.check_env("ORACLE_ACCESS_KEY_ID", default=None, comment="(Customer) Access Key")
        self.check_env("ORACLE_SECRET_ACCESS_KEY", default=None, comment="(Customer) Access Secret")

        self.check_env(
            "ORACLE_REGION", default="eu-amsterdam-1", comment="In which Oracle region is your bucket located?"
        )

    def prepare_for_restic(self, _: Context) -> None:
        env = self.env_config
        os.environ["RESTIC_PASSWORD"] = env["ORACLE_PASSWORD"]
        os.environ["RESTIC_REPOSITORY"] = self.uri

        os.environ["AWS_ACCESS_KEY_ID"] = env["ORACLE_ACCESS_KEY_ID"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = env["ORACLE_SECRET_ACCESS_KEY"]

    @property
    def uri(self) -> str:
        env = self.env_config
        return "s3:{namespace}.compat.objectstorage.{region}.oraclecloud.com/{bucket}".format(
            namespace=env.get("ORACLE_NAMESPACE", "?"),
            region=env.get("ORACLE_REGION", "?"),
            bucket=env.get("ORACLE_NAME", "?"),
        )
