# Custom repository types

Add a restic-supported backend this package does not ship by subclassing `Repository` and
registering it. Three members are required: `setup`, `prepare_for_restic`, and `uri`.

The example below is an **untested sketch** to show the shape, not a working Azure implementation
or a recommendation of any particular provider:

```python
import os

from edwh_restic_plugin.plugins import Repository, register


@register("example", aliases=("ex",), priority=20)
class ExampleRepository(Repository):
    def setup(self) -> None:
        self.check_env("EXAMPLE_NAME", None, "Container to store backups in")
        self.check_env("EXAMPLE_PASSWORD", None, "Restic password")
        self.check_env("EXAMPLE_ACCOUNT_KEY", None, "Account key")

    def prepare_for_restic(self, c) -> None:
        env = self.env_config
        os.environ["RESTIC_PASSWORD"] = env["EXAMPLE_PASSWORD"]
        os.environ["EXAMPLE_ACCOUNT_KEY"] = env["EXAMPLE_ACCOUNT_KEY"]

    @property
    def uri(self) -> str:
        return f"example:{self.env_config['EXAMPLE_NAME']}:/"
```

`prepare_env_for_restic` fills common variables such as `RESTIC_REPOSITORY` from `uri`, so
`prepare_for_restic` only needs to add backend-specific environment.

## Discovery

Declare an entry point in your **`pyproject.toml`**, using the same group as notifiers:

```toml
[project.entry-points.edwh_restic_plugin]
example = "my_package"
```

For an unpackaged plugin, register the module in `.toml` instead:

```toml
[restic.plugins]
repositories = ["my_package"]
```

The entry point group is shared by both plugin kinds; which registry a plugin lands in is decided
by the decorator it uses.

## Optional additions

- `wipe`, `bucket` and `prepare_rclone_config` are only needed for `restic.wipe` and
  `restic.move`. Without them those two commands report that your backend does not support the
  operation; everything else works.
- `display_name` is what notifications show. It defaults to the registered short name, which
  discloses only the provider. Override it to add something informative but safe, like
  `f"example:{self.bucket}"`. Never return `uri`, which can contain credentials.

## Conventions

- `<SHORTNAME>_PASSWORD` in `.env` is how a default repository is auto-selected when
  `--connection-choice` is omitted. A different name makes your repository unselectable by default.
- `_short_name` and aliases feed forget-policy lookup, so `[restic.forget.example]` works for free.
