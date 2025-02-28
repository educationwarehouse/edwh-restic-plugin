import shlex
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Self, get_type_hints

import tomlkit


@dataclass
class ResticForgetPolicy:
    """
    Represents a policy for forgetting backups using restic, with various retention options.

    Attributes:
        keep_last (Optional[int]): Number of latest snapshots to keep.
        keep_hourly (Optional[int]): Number of hourly snapshots to keep.
        keep_daily (Optional[int]): Number of daily snapshots to keep.
        keep_weekly (Optional[int]): Number of weekly snapshots to keep.
        keep_monthly (Optional[int]): Number of monthly snapshots to keep.
        keep_yearly (Optional[int]): Number of yearly snapshots to keep.
        keep_tag (list[str]): List of tags for which to apply the retention policy.
        keep_within (Optional[str]): Retention period within which to keep snapshots.
        keep_within_hourly (Optional[str]): Hourly retention period within which to keep snapshots.
        keep_within_daily (Optional[str]): Daily retention period within which to keep snapshots.
        keep_within_weekly (Optional[str]): Weekly retention period within which to keep snapshots.
        keep_within_monthly (Optional[str]): Monthly retention period within which to keep snapshots.
        keep_within_yearly (Optional[str]): Yearly retention period within which to keep snapshots.
        prune (bool): Whether to purge old snapshots. Default is True.
    """

    keep_last: Optional[int] = None
    keep_hourly: Optional[int] = None
    keep_daily: Optional[int] = None
    keep_weekly: Optional[int] = None
    keep_monthly: Optional[int] = None
    keep_yearly: Optional[int] = None
    keep_tag: list[str] = field(default_factory=list)
    keep_within: Optional[str] = None
    keep_within_hourly: Optional[str] = None
    keep_within_daily: Optional[str] = None
    keep_within_weekly: Optional[str] = None
    keep_within_monthly: Optional[str] = None
    keep_within_yearly: Optional[str] = None
    prune: bool = False
    dry_run: bool = False

    def to_string(self) -> str:
        """
        Converts the policy into a string of restic command line arguments.

        Returns:
            str: A string representing the restic command line arguments for this policy.
        """
        args = []
        for attr, value in vars(self).items():
            if value is not None:
                # Replace underscores with hyphens once and store in attr
                attr = attr.replace("_", "-")
                option = "--" + attr
                if isinstance(value, list):
                    args.extend(f"{option} {shlex.quote(str(v))}" for v in value)
                elif isinstance(value, bool):
                    # Append --no-<attr> if the value is False
                    if value:
                        args.append(option)
                    # ↓ this works for parsing it ourselves but not for actual restic:
                    # else:
                    #     args.append(f"--no-{attr}")
                else:
                    args.append(f"{option} {shlex.quote(str(value))}")
        return " ".join(args)

    @classmethod
    def from_string(cls, *args: str) -> Self:
        """
        Creates a policy instance from a string of restic command line arguments.

        Args:
            *args (str): A variable number of strings representing the restic command line arguments.

        Returns:
            ResticForgetPolicy: An instance of ResticForgetPolicy created from the provided arguments.

        Raises:
            ValueError: If a required argument is missing or incorrectly formatted.
        """
        options = {}
        parsed_args = shlex.split(" ".join(args))
        type_hints = get_type_hints(cls)

        iterator = iter(parsed_args)
        for arg in iterator:
            if arg.startswith("--"):
                key = arg[2:].replace("-", "_")

                # Handle boolean flags with --no- prefix
                if key.startswith("no_"):
                    key = key[3:]
                    attr_type = type_hints.get(key, bool)
                    if attr_type is bool:
                        options[key] = False
                        continue

                attr_type = type_hints.get(key, str)

                # Handle boolean flags without value
                if attr_type is bool:
                    options[key] = True
                    continue

                if "=" in arg:
                    key, value = arg[2:].split("=", 1)
                else:
                    try:
                        value = next(iterator)
                    except StopIteration:
                        raise ValueError(f"Missing value for {arg}")

                key = key.replace("-", "_")
                attr_type = type_hints.get(key, str)

                # Determine if the attribute type is an integer or optional integer
                origin = typing.get_origin(attr_type)
                if origin is typing.Union:
                    if int in typing.get_args(attr_type):
                        options[key] = int(value)
                elif origin is list:
                    options.setdefault(key, []).append(value)
                elif attr_type is int:
                    options[key] = int(value)
                else:
                    options[key] = value

        return cls(**options)

    @classmethod
    def from_toml_file(cls, subkey: str = "default", toml_path: Optional[str | Path] = None) -> Optional[Self]:
        """
        Creates a policy instance from a TOML file.

        Args:
            subkey (str): The key under which the policy is stored in the TOML file.
            toml_path (Optional[str | Path]): The path to the TOML configuration file. Defaults to '.toml' in the current directory.

        Returns:
            ResticForgetPolicy: An instance of ResticForgetPolicy created from the provided TOML file and key, or None if not found.

        Raises:
            FileNotFoundError: If the specified TOML file does not exist.
            KeyError: If the specified subkey is not found in the TOML file.
        """
        # Set default toml_path if not provided
        if toml_path is None:
            toml_path = Path.cwd() / ".toml"
        else:
            toml_path = Path(toml_path)

        try:
            data = tomlkit.parse(toml_path.read_text())
            forget = data["restic"]["forget"]
        except (KeyError, OSError):
            return None

        if not (section := (forget.get(subkey) or forget.get("default"))):
            return None

        policy_dict = {}
        type_hints = get_type_hints(cls)

        for key, value in section.items():
            # Convert keys to snake_case
            snake_key = key.replace("-", "_")
            attr_type = type_hints.get(snake_key, str)

            if attr_type is bool:
                policy_dict[snake_key] = bool(value)
            else:
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    pass  # If it's not a valid integer, leave it as is
                policy_dict[snake_key] = value

        return cls(**policy_dict)

    def to_toml(
        self,
        subkey: str,
        toml_path: str | Path,
    ) -> None:
        """
        Writes the policy to a TOML file under the specified subkey, replacing it if it exists.

        Args:
            toml_path (str | Path): The path to the TOML configuration file.
            subkey (str): The key under which the policy should be stored in the TOML file.
        """
        toml_path = Path(toml_path)

        # Read existing TOML data or create new if not exists
        if toml_path.exists():
            data = tomlkit.parse(toml_path.read_text())
        else:
            data = tomlkit.document()

        # Ensure 'restic' and 'forget' sections exist
        if "restic" not in data:
            data["restic"] = tomlkit.table()
        if "forget" not in data["restic"]:
            data["restic"]["forget"] = tomlkit.table()

        # Prepare the policy data to write
        policy_data = {}
        for attr, value in vars(self).items():
            if value is not None:
                key = attr.replace("_", "-")
                policy_data[key] = value

        # Update the subkey
        data["restic"]["forget"][subkey] = policy_data

        # Write back to the TOML file
        toml_path.write_text(tomlkit.dumps(data))

    @classmethod
    def get_or_copy_policy(
        cls, subkey: str, toml_path: Optional[str | Path] = None, default_toml_path: Optional[str | Path] = None
    ) -> Optional[Self]:
        """
        Retrieves a policy from the TOML file or copies it from the default TOML file if not present.

        Args:
            subkey (str): The key under which the policy is stored in the TOML file.
            toml_path (Optional[str | Path]): The path to the TOML configuration file. Defaults to '.toml' in the current directory.
            default_toml_path (Optional[str | Path]): The path to the default TOML configuration file. Defaults to 'default.toml' in the same directory as toml_path.

        Returns:
            ResticForgetPolicy: An instance of ResticForgetPolicy created from the provided TOML file and key,
                                or copied from the default TOML file, or None if not found.
        """
        # Ensure toml_path is a Path object
        if toml_path is None:
            toml_path = Path.cwd() / ".toml"
        else:
            toml_path = Path(toml_path)

        # Set default_toml_path if not provided
        if default_toml_path is None:
            default_toml_path = toml_path.parent / "default.toml"
        else:
            default_toml_path = Path(default_toml_path)

        # Try to get the policy from the main TOML file
        policy = cls.from_toml_file(subkey, toml_path)
        if policy:
            return policy

        # Try to get the policy from the default TOML file
        policy = cls.from_toml_file(subkey, default_toml_path)
        if policy:
            # Copy the policy to the main TOML file
            policy.to_toml(
                subkey,
                toml_path,
            )
            return policy

        # Try to get the default policy from the default TOML file
        policy = cls.from_toml_file("default", default_toml_path)
        if policy:
            # Copy the default policy to the main TOML file under the subkey
            policy.to_toml(
                "default",
                toml_path,
            )
            return policy

        return None

    # since dry and prune are mutually exclusive, create helper here:

    @property
    def dry(self) -> bool:
        return self.dry_run

    @dry.setter
    def dry(self, value: bool) -> None:
        self.dry_run = value
        self.prune = not value
