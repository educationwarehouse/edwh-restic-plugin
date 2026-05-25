import os

from edwh.helpers import generate_password
from invoke import Context
from restic_reaper import S3Config, wipe_repository_sync

from . import Repository, register


@register("s3", priority=5)
class S3Repository(Repository):
    # todo: currently tested on Oracle s3 compat and other non-Amazon S3 compatible services, so check with actual S3!

    def setup(self) -> None:
        self.check_env(
            "S3_NAME",
            default=None,
            comment="Bucket to store your backups",
        )

        self.check_env(
            "S3_PASSWORD",
            default=generate_password(silent=True),
            comment="Create a Restic password, keep it safe.",
        )

        self.check_env("S3_URL", default=None, comment="Full S3 storage URL")

        self.check_env(
            "S3_ACCESS_KEY_ID", default=None, comment="Specifies an AWS access key associated with an IAM account."
        )
        self.check_env(
            "S3_SECRET_ACCESS_KEY", default=None, comment="Specifies the secret key associated with the access key."
        )

    def prepare_for_restic(self, _: Context) -> None:
        env = self.env_config
        os.environ["RESTIC_PASSWORD"] = env["S3_PASSWORD"]
        os.environ["RESTIC_REPOSITORY"] = self.uri

        os.environ["AWS_ACCESS_KEY_ID"] = env["S3_ACCESS_KEY_ID"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = env["S3_SECRET_ACCESS_KEY"]

    @property
    def uri(self) -> str:
        bucket = self.env_config["S3_NAME"]
        base = self.env_config["S3_URL"]
        # make sure prefix and suffix are there but only once:
        base = base.removeprefix("s3:").removesuffix(f"/{bucket}").strip("/")
        return f"s3:{base}/{bucket}"

    def wipe(self, dry: bool = False):
        env = self.env_config
        config = S3Config(
            bucket=env["S3_NAME"],
            endpoint=env["S3_URL"],
            access_key_id=env["S3_ACCESS_KEY_ID"],
            secret_access_key=env["S3_SECRET_ACCESS_KEY"],
        )

        return wipe_repository_sync(**config, dry=dry)
