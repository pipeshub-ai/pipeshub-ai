"""Configuration an agent obeys above anything a repository can set, seeded into the sandbox's ``SEALED_DIR``.

Gemini CLI 0.63.0 merges a trusted workspace's ``.gemini/settings.json`` over its user settings, one MCP server
name at a time, so a repository could redefine the tool gateway's entry (its URL, headers, timeout or trust); only
its system settings sit above the workspace. It reads system settings from ``GEMINI_CLI_SYSTEM_SETTINGS_PATH``,
and only from a file root owns in folders root owns down to ``/``, none writable by group or others (a file that
fails is skipped with a warning, and the image's floor would go with it). So the profile renders that file under
``SEALED_DIR`` (a read-only scratch mount, ``0755`` root's, seeded from outside), keeps everything the image's own
system settings hold (``base``) and adds the run's entry beside them, never over one (WP-59). After seeding, the
driver checks the ownership itself, so a broker that made the folder writable is an engine that cannot start.
"""

from __future__ import annotations

import json
import posixpath
from typing import TYPE_CHECKING, cast

from harness_core.engines.binary import run_check
from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.types import SEALED_DIR
from harness_core.sandbox.errors import SandboxError
from harness_core.sandbox.tar import config_tar, read_tar_stream

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.logs import HarnessLog
    from harness_core.sandbox import SandboxHandle
    from harness_engines.acp.profiles import AcpAgentProfile

_GROUP_OR_OTHER_WRITE = 0o022


class SealedConfigRefused(EngineUnhealthy):
    """A sealed config could not be made: the image's base is missing or unreadable, the rendered settings would
    replace one of its settings, or the folder is not one the agent trusts."""


def merged(base: JsonValue, rendered: JsonValue, where: str = "") -> JsonValue:
    """``base`` with ``rendered`` added: objects merge key by key; any other value already in ``base`` is kept and
    refused, never replaced."""
    if not isinstance(base, dict) or not isinstance(rendered, dict):
        raise SealedConfigRefused(f"the sealed config would replace the image's {where or 'settings'}")
    out = dict(cast("dict[str, JsonValue]", base))
    for key, value in cast("dict[str, JsonValue]", rendered).items():
        out[key] = merged(out[key], value, f"{where}.{key}" if where else key) if key in out else value
    return out


def sealed_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    return {path: content for path, content in files.items() if path.startswith(SEALED_DIR + "/")}


async def seal(sandbox: SandboxHandle, agent: AcpAgentProfile, files: Mapping[str, bytes], log: HarnessLog) -> None:
    """Seeds the agent's sealed configs (each merged over its image ``base``) and checks the agent will trust them."""
    sealed = sealed_files(files)
    if not sealed:
        return
    bases = {c.path: c.base for c in agent.configs if c.sealed and c.base is not None}
    out: dict[str, bytes] = {}
    for path, content in sealed.items():
        base_path = bases.get(path)
        if base_path is None:
            out[path] = content
            continue
        try:
            read = await read_tar_stream(await sandbox.get_tar(base_path))
        except SandboxError as exc:
            log.warning("engine.sealed_config.no_base", reasonCode="sealed_config")
            raise SealedConfigRefused(f"{agent.name}'s image holds no {base_path}") from exc
        printed = read.get(posixpath.basename(base_path), b"")
        try:
            base = cast("JsonValue", json.loads(printed))
        except json.JSONDecodeError as exc:
            raise SealedConfigRefused(f"{agent.name}'s {base_path} is not JSON") from exc
        out[path] = json.dumps(merged(base, cast("JsonValue", json.loads(content))), sort_keys=True).encode()
    await sandbox.seed_tar(
        SEALED_DIR, config_tar({p.removeprefix(SEALED_DIR + "/"): c for p, c in out.items()}, own_parents=True)
    )
    await _check_trusted(sandbox, sorted(out), log)


def _chain(path: str) -> list[str]:
    folders: list[str] = []
    folder = posixpath.dirname(path)
    while True:
        folders.append(folder)
        if folder == "/":
            return folders
        folder = posixpath.dirname(folder)


async def _check_trusted(sandbox: SandboxHandle, paths: list[str], log: HarnessLog) -> None:
    """Each sealed file and every folder above it is root's and writable by nobody else, as the agent requires."""
    checked = sorted({*paths, *(f for p in paths for f in _chain(p))})
    code, printed = await run_check(sandbox, ["stat", "-c", "%u %a %n", *checked])
    seen: dict[str, tuple[int, int]] = {}
    for line in printed.splitlines():
        uid, mode, name = [*line.split(" ", 2), "", ""][:3]
        if uid.isdigit() and mode and all(c in "01234567" for c in mode):
            seen[name] = (int(uid), int(mode, 8))
    loose = [p for p in checked if seen.get(p, (-1, 0o777))[0] != 0 or seen[p][1] & _GROUP_OR_OTHER_WRITE]
    if code != 0 or loose:
        log.warning("engine.sealed_config.untrusted", reasonCode="sealed_config", paths=len(loose))
        raise SealedConfigRefused(f"the sealed config's folder is not root's alone ({', '.join(loose) or 'unreadable'})")


__all__ = ["SealedConfigRefused", "merged", "seal", "sealed_files"]
