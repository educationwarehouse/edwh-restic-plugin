"""
Plugin registry and discovery primitives.

This is the low layer: `notify` and `repositories` build on it, and `plugins` re-exports the whole
public surface on top. Import from `edwh_restic_plugin.plugins` unless you are inside this package.

A plugin is found in one of three ways, in ascending precedence:

1. a module inside this package (repositories only, the original behaviour);
2. a Python entry point, which is how edwh finds this plugin in the first place, so being a
   plugin-of-a-plugin introduces no new concept;
3. an explicit module list in `.toml` under `[restic.plugins] modules`, for anything installed
   without an entry point.

Discovery is deliberately failure-isolating: a plugin that raises on import is reported and
skipped. A broken notification plugin must never prevent a backup.
"""

import heapq
import importlib
import importlib.metadata
import importlib.util
import sys
import typing
from collections import OrderedDict
from pathlib import Path

from termcolor import cprint
from typing_extensions import NotRequired

from .config import read_config


#: Bumped when a change breaks a plugin at runtime: a removed or renamed field, a changed
#: method signature, a member dropped from the Event union. Adding an event operation does not
#: bump it -- see docs/plugins-architecture.md section 7.2.
CONTRACT_VERSION = 1

T = typing.TypeVar("T")


class Registration(typing.TypedDict):
    short_name: str
    aliases: NotRequired[tuple[str, ...]]
    priority: NotRequired[int]


class Registry(typing.Generic[T]):
    """A priority-ordered plugin registry with lazy, scoped discovery.

    Subclasses declare where to look; the mechanics of pushing, aliasing and ordering are shared.
    """

    #: Entry point group external packages declare, e.g. "edwh_restic_plugin.repositories".
    entry_point_group: str
    #: Dotted package whose *.py modules are imported, or None to skip in-package discovery.
    in_package: str | None = None
    #: Key under [restic.plugins] holding an explicit module list for this registry.
    config_key: str

    def __init__(self) -> None:
        # _queue is for internal use by heapq only! external api should use .queue !!!
        self._queue: list[tuple[int, int, type[T], Registration]] = []
        # aliases stores a reference for each name to the plugin class
        self._aliases: dict[str, type[T]] = {}
        self._discovered = False
        # Tie-breaker, so heapq never has to compare the plugin classes themselves. Repository can
        # be compared (SortableMeta defines __lt__/__gt__ for exactly this reason) but nothing else
        # can, and requiring every future plugin base to carry that metaclass would be a trap.
        # A counter also makes equal priorities resolve in registration order rather than
        # arbitrarily.
        self._counter = 0

    def push(self, plugin: type[T], settings: Registration) -> None:
        priority = settings.get("priority", -1)
        if priority < 0:
            priority = sys.maxsize - priority  # very high int

        self._counter += 1
        heapq.heappush(self._queue, (priority, self._counter, plugin, settings))
        self._aliases[settings["short_name"]] = plugin
        for alias in settings.get("aliases", ()):
            self._aliases[alias] = plugin

    @property
    def queue(self) -> list[tuple[int, int, type[T], Registration]]:
        if not self._discovered:
            self.discover()

        return self._queue

    def clear(self) -> None:
        self._queue = []
        self._aliases = {}
        self._discovered = False

    def get(self, name: str) -> type[T] | None:
        # NB: discover first. _aliases is only filled by push(), which only runs during
        # discovery, so reading _aliases directly returns None for a correctly registered plugin
        # unless some earlier call happened to trigger discovery.
        if not self._discovered:
            self.discover()
        return self._aliases.get(name)

    def to_sorted_list(self) -> list[type[T]]:
        # No need for sorting here; heapq maintains the heap property
        return list(self)

    def to_ordered_dict(self) -> "OrderedDict[str, type[T]]":
        ordered_dict: OrderedDict[str, type[T]] = OrderedDict()
        for _, _, item, settings in self.queue:
            ordered_dict[settings["short_name"]] = item
        return ordered_dict

    def __iter__(self) -> typing.Generator[type[T], None, None]:
        return (entry[2] for entry in self.queue)

    def __bool__(self) -> bool:
        return bool(self.queue)

    def discover(
        self,
        *,
        in_package: bool = True,
        entry_points: bool = True,
        config: bool = True,
    ) -> None:
        """Import plugin modules so their @register decorators run.

        Scoped so tests can ask for in-package plugins only. Without that, installing any
        third-party repository plugin in a dev environment would change what the suite sees.
        """
        self._discovered = True

        if in_package and self.in_package:
            self._discover_in_package()
        if entry_points:
            self._discover_entry_points()
        if config:
            self._discover_from_config()

    def _discover_in_package(self) -> None:
        package = typing.cast(str, self.in_package)
        spec = importlib.util.find_spec(package)
        if not spec or not spec.origin:
            return

        for file_path in Path(spec.origin).resolve().parent.glob("*.py"):
            if not file_path.stem.startswith("__"):
                self._import(f"{package}.{file_path.stem}")

    def _discover_entry_points(self) -> None:
        for entry_point in importlib.metadata.entry_points(group=self.entry_point_group):
            # The target is imported for its side effects -- @register does the work -- so the
            # value may be a module or a class; load() covers both.
            try:
                entry_point.load()
            except Exception as e:
                self._report(f"entry point '{entry_point.name}' ({entry_point.value})", e)

    def _discover_from_config(self) -> None:
        modules = read_config("plugins").get(self.config_key, [])
        if isinstance(modules, str):
            modules = [modules]

        for module in modules:
            self._import(module)

    def _import(self, module: str) -> None:
        try:
            if existing := sys.modules.get(module):
                # A plain import_module is a no-op for an already-imported module, so @register
                # would not run and a clear() + discover() cycle would silently register nothing.
                # Reloading re-executes the decorators, which is what makes rediscovery idempotent.
                importlib.reload(existing)
            else:
                importlib.import_module(module)
        except Exception as e:
            self._report(f"plugin module '{module}'", e)

    @staticmethod
    def _report(what: str, error: Exception) -> None:
        cprint(
            f"warning: {what} could not be loaded and was skipped: {type(error).__name__}: {error}",
            color="yellow",
            file=sys.stderr,
        )
