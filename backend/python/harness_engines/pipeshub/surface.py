"""The ``/pipeshub`` contract's surface, and the additive-only rule over it (OWD-6, WS-20).

A skill, a prompt or an agent-written script may hard-code anything a contract version exposes: a path under
``/pipeshub`` (its kind, where a link points, a read-only mode), a fixed name in the session folder, a ``ph`` command
with its arguments and flags, an exit code's ``--json`` name, and the shape of a ``<tool>.schema.json``. Only what
such a caller could depend on is in the surface; the content of ``AGENTS.md`` and ``INDEX.md`` and the generated SDK's
internals stay free (OWD-6 "keep flexible").

``non_additive`` compares a pre-registered surface with the built one. Within a version the built surface may hold
more; everything the golden names must be there unchanged.
"""

from __future__ import annotations

import os
import re
import stat
from typing import TYPE_CHECKING, Any, cast

from harness_engines.pipeshub import (
    CONTRACT_VERSION,
    FOLDER,
    session_files,
    session_links,
    tool_schema,
)
from harness_engines.pipeshub import ph as ph_cli

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from harness_core.engines.types import GatewayEndpoints

# A digest in a session folder name (``tools-<16 hex>``) varies with the grant; the name around it does not.
_DIGEST = re.compile(r"-[0-9a-f]{16,64}(?=/|$)")
_ACTIONS = {
    "_StoreAction": "store",
    "_StoreTrueAction": "store_true",
    "_AppendAction": "append",
    "_HelpAction": "help",
}


def contract_surface(root: Path, endpoints: GatewayEndpoints | None = None) -> dict[str, Any]:
    """The surface of the contract an image root holds after ``install.sh`` (``root`` + ``/pipeshub``), with the
    session folder the controller seeds for a turn with no tools."""
    return {
        "contract": CONTRACT_VERSION,
        "layout": _layout(root / FOLDER.lstrip("/")),
        "session": _session(endpoints),
        "ph": _commands(ph_cli.parser()),
        "exitCodes": {str(code): name for code, name in sorted(ph_cli.ERROR_CODES.items())},
        "schemaFile": _schema_shape(),
    }


def _layout(folder: Path) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in sorted((*dirnames, *filenames)):
            path = os.path.join(dirpath, name)
            relative = os.path.relpath(path, folder)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                found[relative] = {"kind": "link", "target": os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                found[relative] = {"kind": "dir"}
            else:
                found[relative] = {"kind": "file", "mode": f"{stat.S_IMODE(info.st_mode):04o}"}
    return found


def _session(endpoints: GatewayEndpoints | None) -> dict[str, dict[str, str]]:
    from harness_core.engines.controller import EngineControllerConfig

    endpoints = endpoints or EngineControllerConfig(images={}).endpoints("pxh-model-surface", "pxh-tools-surface")
    files = session_files((), endpoints)
    found: dict[str, dict[str, str]] = {_DIGEST.sub("-<digest>", path): {"kind": "file"} for path in files}
    for name, target in session_links(files).items():
        found[name] = {"kind": "link", "target": _DIGEST.sub("-<digest>", target)}
    return dict(sorted(found.items()))


def _commands(root: argparse.ArgumentParser) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def walk(parser: argparse.ArgumentParser, name: str) -> None:
        positionals: dict[str, dict[str, str]] = {}
        options: dict[str, dict[str, str]] = {}
        required = 0
        for action in parser._actions:  # argparse keeps its actions nowhere public
            choices: object = getattr(action, "choices", None)
            # A noun's verbs: argparse's subparsers action is the one whose choices are the verbs' parsers.
            if not action.option_strings and isinstance(choices, dict):
                verbs = cast("dict[str, argparse.ArgumentParser]", choices)
                for verb, child in verbs.items():
                    walk(child, verb if name == "ph" else f"{name} {verb}")
                continue
            if action.option_strings:
                spec = {"action": _ACTIONS.get(type(action).__name__, type(action).__name__)}
                if action.type is not None:
                    spec["type"] = getattr(action.type, "__name__", str(action.type))
                for flag in action.option_strings:
                    options[flag] = spec
                required += 1 if action.required else 0
                continue
            nargs = "1" if action.nargs is None else str(action.nargs)
            positionals[action.dest] = {"nargs": nargs}
            required += 0 if nargs in ("?", "*") else 1
        found[name] = {"positionals": positionals, "required": required, "options": options}

    walk(root, "ph")
    return found


def _schema_shape() -> dict[str, str]:
    """What every ``<tool>.schema.json`` holds whatever the tool: the 2020-12 dialect, an object, titled with the
    name ``ph tools call`` takes and described (each by its JSON type); the tool's own keywords vary."""
    from harness_core.gateway.types import McpTool

    schema = cast("dict[str, object]", tool_schema(McpTool("toolset__tool", "Described.", {"type": "object"})))
    return {key: str(value) if key in ("$schema", "type") else type(value).__name__ for key, value in schema.items()}


def non_additive(golden: object, current: object, where: str = "") -> list[str]:
    """Every name ``golden`` holds that ``current`` removed or changed, by path (``/ph/tools search/options``)."""
    if isinstance(golden, dict):
        if not isinstance(current, dict):
            return [f"{where or '/'}: changed from a mapping to {type(current).__name__}"]
        golden_map, current_map = cast("dict[str, object]", golden), cast("dict[str, object]", current)
        violations: list[str] = []
        for key, value in golden_map.items():
            path = f"{where}/{key}"
            if key not in current_map:
                violations.append(f"{path}: removed")
            else:
                violations.extend(non_additive(value, current_map[key], path))
        return violations
    if golden != current:
        return [f"{where}: changed from {golden!r} to {current!r}"]
    return []


__all__ = ["contract_surface", "non_additive"]
