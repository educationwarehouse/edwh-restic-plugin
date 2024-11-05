import os
import warnings
from pathlib import Path
from typing import Optional

# the path where the environment variables are going
DOTENV = Path(".env")
DOTENV.touch(exist_ok=True)

_dotenv_settings: dict[Path, dict[str, str]] = {}


def read_dotenv(path: Optional[Path] = None) -> dict[str, str]:
    """Reads a .env file at the specified path and returns a dictionary of key - value pairs.

    If the specified key is not found in the.env file, the function prompts the user to enter a value for the key,
    with a default value provided.The key-value pair is then appended to the.env file.

    Args:
        path(Path): The path to the .env file.

    Returns:
        dict: A dictionary containing the key - value pairs in the .env file."""
    path = path or DOTENV

    if existing := _dotenv_settings.get(path):
        # 'cache'
        return existing

    items = {}
    with path.open(mode="r") as env_file:
        for line in env_file:
            # remove comments and redundant whitespace
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                # just a comment, skip
                # or key without value? invalid, prevent crash:
                continue

            # convert to tuples
            k, v = line.split("=", 1)

            # clean the tuples and add to dict
            items[k.strip()] = v.strip()

    _dotenv_settings[path] = items
    return items


def set_env_value(path: Path, target: str, value: str) -> None:
    """
    update/set environment variables in the .env file, keeping comments intact

    set_env_value(Path('.env'), 'SCHEMA_VERSION', schemaversion)

    Args:
        path: pathlib.Path designating the .env file
        target: key to write, probably best to use UPPERCASE
        value: string value to write, or anything that converts to a string using str()
    """
    with path.open(mode="r") as env_file:
        # open the .env file and read every line in the inlines
        inlines = env_file.read().split("\n")

    outlines = []  # lines for output
    geschreven = False
    for line in inlines:
        if line.strip().startswith("#"):
            # ignore comments
            outlines.append(line)
            continue
        # remove redundant whitespace
        line = line.strip()
        if not line:
            # remove empty lines
            continue
        # convert to tuples
        key, oldvalue = line.split("=", 1)
        # clean the key and value
        key = key.strip()
        if key == target:
            # add the new tuple to the lines
            outlines.append(f"{key}={value}")
            geschreven = True
        else:
            # or leave it as it is
            outlines.append(line)
    if not geschreven:
        # if target in .env file
        outlines.append(f"{target.strip().upper()}={value.strip()}")
    with path.open(mode="w") as env_file:
        # write outlines to .env file
        env_file.write("\n".join(outlines))
        env_file.write("\n")


def check_env(
    key: str,
    default: str | None,
    comment: str,
    prefix: str = None,
    suffix: str = None,
    postfix: str = None,
    path: Path = None,
):
    """
    Test if key is in .env file path, appends prompted or default value if missing.
    """
    path = path or DOTENV

    if postfix:
        warnings.warn("`postfix` option passed. Please use `suffix` instead!", category=DeprecationWarning)

    suffix = suffix or postfix

    env = read_dotenv(path)
    if key in env:
        return env[key]

    # get response value from prompt/input
    # if response_value is empty make value default else value is response_value
    value = input(f"Enter value for {key} ({comment})\n default=`{default}`: ").strip() or default or ""
    if value.startswith("~/") and Path(value).expanduser().exists():
        value = str(Path(value).expanduser())
    if prefix:
        value = prefix + value
    if suffix:
        value += suffix

    with path.open(mode="r+") as env_file:
        env_file.seek(0, 2)
        # write key and value to .env file
        env_file.write(f"\n{key.upper()}={value}")

    # update in memory too:
    os.environ[key] = env[key] = value
    return value
