"""
Exceptions raised deliberately by this plugin.

Library code must never call sys.exit() or exit(): a bare process exit prevents any
except/finally in a caller from running, which makes failure reporting impossible. Raise one of
these instead and let the task layer turn it into a process exit code.
"""

import dataclasses
import typing


@dataclasses.dataclass(frozen=True)
class ScriptFailure:
    """A single captain-hooks script that exited non-zero."""

    script: str
    exit_code: int


class ResticError(Exception):
    """Base for every failure this plugin raises on purpose.

    exit_code is what the task layer passes to sys.exit(), so it stays compatible with the
    process exit codes this plugin produced before these exceptions existed.
    """

    exit_code: int = 1


class NoScriptsFound(ResticError):
    """No captain-hooks script matched the requested verb and target."""

    exit_code = 255

    def __init__(self, verb: str, target: str, folder: "typing.Any") -> None:
        self.verb = verb
        self.target = target
        self.folder = folder
        super().__init__(f"no files found in {folder}/ matching '{verb}_{target}*'")


class ResticScriptError(ResticError):
    """One or more captain-hooks scripts exited non-zero."""

    def __init__(self, failures: "typing.Iterable[ScriptFailure]") -> None:
        self.failures = tuple(failures)
        if not self.failures:
            raise ValueError("ResticScriptError requires at least one failure")

        # Mirror the worst script's code, as `exit(max(file_codes))` intended to do.
        self.exit_code = max(failure.exit_code for failure in self.failures)
        detail = ", ".join(f"{f.script}({f.exit_code})" for f in self.failures)
        super().__init__(f"{len(self.failures)} script(s) failed: {detail}")


class ResticConnectionError(ResticError):
    """The repository backend could not be reached or is misconfigured."""


class UnsupportedOperation(ResticError):
    """This repository type does not implement the requested operation.

    Not every backend can do everything -- `wipe` and `move` need provider-specific bucket and
    rclone support that a plain restic target does not have. Declaring those abstract would make
    every third-party repository implement three methods it does not need just to be
    instantiable, so they degrade at the point of use instead.
    """

    def __init__(self, repository: str, operation: str) -> None:
        self.repository = repository
        self.operation = operation
        super().__init__(f"repository '{repository}' does not support {operation}")
