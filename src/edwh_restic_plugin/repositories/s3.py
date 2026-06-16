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
    def bucket(self):
        return self.env_config["S3_NAME"]

    @property
    def uri(self) -> str:
        bucket = self.bucket
        base = self.env_config["S3_URL"]
        # make sure prefix and suffix are there but only once:
        base = base.removeprefix("s3:").removesuffix(f"/{bucket}").strip("/")
        return f"s3:{base}/{bucket}"

    def s3_wipe_config(self) -> S3Config:
        bucket = self.bucket
        endpoint = self.uri.removeprefix("s3:").removesuffix(f"/{bucket}").strip("/")
        return S3Config(
            bucket=bucket,
            endpoint=endpoint,
            access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        )

    def wipe(self, dry: bool = False):
        # assumes 'prepare_for_restic' was ran
        config = self.s3_wipe_config()
        return wipe_repository_sync(**config, dry=dry)

    def prepare_rclone_config(self):
        env = self.env_config
        return f"""type = s3
provider = Other
access_key_id = {env["AWS_ACCESS_KEY_ID"]}
secret_access_key = {env["AWS_SECRET_ACCESS_KEY"]}
region = hel1
endpoint = hel1.your-objectstorage.com"""
