import tempfile
from contextlib import chdir
from pathlib import Path

import pytest
from tomlkit.exceptions import ParseError

from src.edwh_restic_plugin.forget import ResticForgetPolicy


def test_from_string():
    input_args = ["--keep-last", "123", "--keep-hourly", "123213", "--no-prune"]
    expected_output = {"keep_last": 123, "keep_hourly": 123213, "prune": False}

    policy = ResticForgetPolicy.from_string(*input_args)

    for key, value in expected_output.items():
        actual_value = getattr(policy, key)
        assert actual_value == value, (
            f"Expected {type(value).__name__}({value}) for attribute '{key}', "
            f"but got {type(actual_value).__name__}({actual_value})"
        )

    input_args = [
        "--keep-last 123",
        "--keep-hourly=123213",
        "--prune",
        "--keep-tag test",
    ]
    expected_output = {
        "keep_last": 123,
        "keep_hourly": 123213,
        "prune": True,
        "keep_tag": ["test"],
    }

    policy = ResticForgetPolicy.from_string(*input_args)

    for key, value in expected_output.items():
        actual_value = getattr(policy, key)
        assert actual_value == value, (
            f"Expected {type(value).__name__}({value}) for attribute '{key}', "
            f"but got {type(actual_value).__name__}({actual_value})"
        )

    expected_output = {
        "prune": False,
        "keep_tag": [],
    }

    policy = ResticForgetPolicy.from_string("")
    for key, value in expected_output.items():
        actual_value = getattr(policy, key)
        assert actual_value == value, (
            f"Expected {type(value).__name__}({value}) for attribute '{key}', "
            f"but got {type(actual_value).__name__}({actual_value})"
        )


def test_to_string_round_trip():
    # Create a policy instance
    original_policy = ResticForgetPolicy(keep_last=5, keep_hourly=10, keep_tag=["test"], prune=False)

    # Convert to string
    policy_string = original_policy.to_string()

    # Convert back from string
    new_policy = ResticForgetPolicy.from_string(*policy_string.split())

    # Assert that the original and new policies are equivalent
    for attr in vars(original_policy):
        original_value = getattr(original_policy, attr)
        new_value = getattr(new_policy, attr)
        assert original_value == new_value, (
            f"Expected {type(original_value).__name__}({original_value}) for attribute '{attr}', "
            f"but got {type(new_value).__name__}({new_value})"
        )


def test_from_toml_file():
    with tempfile.TemporaryDirectory() as tempdir:
        toml_path = Path(tempdir) / "scratch_14.toml"

        # Create a sample TOML file
        toml_content = """
        [restic.forget.s3]
        keep_last = "4"
        keep_hourly = 3
        prune = 1

        [restic.forget.s4]
        keep-last = "123"
        keep-hourly = 123213
        prune = false

        [restic.forget.s5]
        keep-last = 5
        keep-hourly = 10
        keep-tag = []
        prune = true
        """
        toml_path.write_text(toml_content)

        policy_s4 = ResticForgetPolicy.from_toml_file("s4", str(toml_path))
        expected_output_s4 = {"keep_last": 123, "keep_hourly": 123213, "prune": False}

        for key, value in expected_output_s4.items():
            actual_value = getattr(policy_s4, key)
            assert actual_value == value, (
                f"Expected {type(value).__name__}({value}) for attribute '{key}' in policy s4, "
                f"but got {type(actual_value).__name__}({actual_value})"
            )

        policy_s3 = ResticForgetPolicy.from_toml_file("s3", str(toml_path))
        expected_output_s3 = {"keep_last": 4, "keep_hourly": 3, "prune": True}

        for key, value in expected_output_s3.items():
            actual_value = getattr(policy_s3, key)
            assert actual_value == value, (
                f"Expected {type(value).__name__}({value}) for attribute '{key}' in policy s3, "
                f"but got {type(actual_value).__name__}({actual_value})"
            )


def test_to_toml():
    with tempfile.TemporaryDirectory() as tempdir:
        toml_path = Path(tempdir) / "scratch_14.toml"

        # Create a policy instance
        policy = ResticForgetPolicy(keep_last=5, keep_hourly=10, prune=True)

        # Write to the TOML file under subkey 's5'
        policy.to_toml(
            "s5",
            toml_path,
        )

        # Verify by reading back the data
        written_policy = ResticForgetPolicy.from_toml_file("s5", toml_path)
        expected_output = {"keep_last": 5, "keep_hourly": 10, "prune": True}

        for key, value in expected_output.items():
            actual_value = getattr(written_policy, key)
            assert actual_value == value, (
                f"Expected {type(value).__name__}({value}) for attribute '{key}' in policy s5, "
                f"but got {type(actual_value).__name__}({actual_value})"
            )


def test_get_or_copy_policy():
    with tempfile.TemporaryDirectory() as tempdir, chdir(tempdir):
        toml_path = Path(tempdir) / ".toml"
        default_toml_path = Path(tempdir) / "default.toml"
        empty_toml_path = Path(tempdir) / "empty.toml"

        # Create a default TOML file
        default_toml_content = """
        [restic.forget.default]
        keep-last = 7
        keep-hourly = 8
        prune = true
        """
        default_toml_path.write_text(default_toml_content)

        empty_toml_path.write_text("")

        # Test case 1: Policy exists in the main TOML file
        policy = ResticForgetPolicy.get_or_copy_policy("s5", toml_path, default_toml_path)
        assert policy is not None, "Expected a policy to be returned from the main TOML file."

        # Test case 2: Policy does not exist in the main TOML file but exists in the default TOML file
        policy = ResticForgetPolicy.get_or_copy_policy("s6", toml_path, default_toml_path)
        assert policy is not None, "Expected a policy to be copied from the default TOML file."

        # Verify that the data was copied to .toml under the correct subkey using from_toml_file
        loaded_policy = ResticForgetPolicy.from_toml_file("s6", toml_path)
        expected_section = {"keep_last": 7, "keep_hourly": 8, "prune": True}

        for key, value in expected_section.items():
            actual_value = getattr(loaded_policy, key)
            assert actual_value == value, (
                f"Expected {type(value).__name__}({value}) for attribute '{key}' in policy s6, "
                f"but got {type(actual_value).__name__}({actual_value})"
            )

        # Test case 3: Policy does not exist in either TOML file
        policy = ResticForgetPolicy.get_or_copy_policy("nonexistent", toml_path, empty_toml_path)
        assert policy is None, "Expected no policy to be found in either TOML file."

        # Test case 4: Only subkey is provided
        policy = ResticForgetPolicy.get_or_copy_policy("default")
        assert policy is not None, "Expected a policy to be copied from the default TOML file using only the subkey."

        # Verify that the data was copied to .toml under the correct subkey using from_toml_file
        loaded_policy = ResticForgetPolicy.from_toml_file("default", toml_path)
        for key, value in expected_section.items():
            actual_value = getattr(loaded_policy, key)
            assert actual_value == value, (
                f"Expected {type(value).__name__}({value}) for attribute '{key}' in policy default, "
                f"but got {type(actual_value).__name__}({actual_value})"
            )


def test_invalid_from_string():
    invalid_args = [
        "--keep-last",
        "abc",  # Invalid integer
        "--keep-hourly",
        "123213",
        "--no-prune",
    ]
    with pytest.raises(ValueError):
        ResticForgetPolicy.from_string(*invalid_args)


def test_missing_value_from_string():
    missing_value_args = ["--keep-last"]  # Missing value
    with pytest.raises(ValueError):
        ResticForgetPolicy.from_string(*missing_value_args)


def test_invalid_toml_file():
    with tempfile.TemporaryDirectory() as tempdir:
        toml_path = Path(tempdir) / "invalid.toml"
        toml_path.write_text("invalid content")
        with pytest.raises(ParseError):
            ResticForgetPolicy.from_toml_file("s4", str(toml_path))
