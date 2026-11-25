"""``ToolFolder``: a read-only folder in an engine's sandbox, written at each turn's start from the run's grant.

A shell CLI in the engine image reads it to reach the run's tools through the same tool gateway the engine's MCP
client uses (the product's is ``ph`` under ``/pipeshub``, ``harness_engines.pipeshub``). The folder is rendered
from the grant the run is pinned to, so it names exactly the tools ``search_tools`` and ``call_tool`` reach, and
from the gateway endpoints as the sandbox sees them: the tools URL and the attempt's placeholder, which the egress
proxy swaps for the run's token. No token is ever written. The image carries the CLI and its static docs; nothing
the folder holds is baked into the image, so it is never stale.

The image declares the folder contract it was built for (``ImageContract``: a version file). A turn whose image
declares a version the worker does not write, or none, is refused before the engine starts (WS-12): its CLI and links
would not match what the turn seeds.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineContractUnsupported
from harness_core.engines.git_checkout import run_in
from harness_core.gateway.ports import GrantedTools
from harness_core.sandbox.errors import SandboxPathNotFound
from harness_core.sandbox.tar import config_tar, read_tar_stream
from harness_core.sandbox.types import MountSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.engines.types import GatewayEndpoints
    from harness_core.gateway.types import McpTool
    from harness_core.sandbox.ports import SandboxHandle

    # (the run's grant, the gateways as the sandbox sees them, the run's pinned skill names) -> relative path -> bytes
    type FolderRenderer = Callable[[tuple[McpTool, ...], GatewayEndpoints, tuple[str, ...]], Mapping[str, bytes]]
    # the rendered files -> the links the folder holds beside them (relative path -> link target)
    type FolderLinks = Callable[[Mapping[str, bytes]], Mapping[str, str]]


def granted_tools(endpoint: object) -> tuple[McpTool, ...]:
    """The run's whole grant, when its endpoint can say it (``GrantedTools``); else none."""
    return endpoint.granted_tools() if isinstance(endpoint, GrantedTools) else ()


# The usual PATH, when the image's own cannot be read.
DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# Writes the login profiles (``$1``) in ``$HOME`` and prints the image's own PATH.
_PROFILE_SCRIPT = (
    'for f in "$HOME/.profile" "$HOME/.bash_profile"; do printf "%s\\n" "$1" > "$f" || exit 1; done; printf %s "$PATH"'
)


@dataclass(frozen=True, slots=True)
class ImageContract:
    """The file in the engine image that names its folder contract version, and the versions this worker writes."""

    version_file: str
    supported: frozenset[str]
    # What an operator does about a refusal (the refusal is logged; the person sees the engine as unavailable).
    remedy: str = ""

    async def check(self, sandbox: SandboxHandle) -> str:
        """The image's version when the worker writes it; else ``EngineContractUnsupported``."""
        try:
            read = await read_tar_stream(await sandbox.get_tar(self.version_file))
        except SandboxPathNotFound:
            read = {}
        found = next(iter(read.values()), b"").decode("utf-8", "replace").strip()
        if found in self.supported:
            return found
        declared = f"declares contract {found!r} in {self.version_file}" if found else f"has no {self.version_file}"
        writes = ", ".join(sorted(self.supported))
        raise EngineContractUnsupported(
            f"the engine image {declared}; this worker writes contract {writes}" + (f": {self.remedy}" if self.remedy else "")
        )


@dataclass(frozen=True, slots=True)
class Shelf:
    """Where a tool's over-cap results go in the folder (C3.22): ``folder`` under the folder's target, which the
    engine reads as ``path``; ``holds`` names the tools whose results go there."""

    folder: str
    path: str
    holds: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ToolFolder:
    """Where the folder is mounted (read-only scratch, seeded by the controller), how its files are rendered, and
    the contract the image must declare (unset: any image). ``shelf``: where over-cap results are kept, if anywhere.
    ``policy_hook``: the path of the managed policy hook program the folder holds (G-25), if it holds one."""

    target: str
    render: FolderRenderer
    contract: ImageContract | None = None
    links: FolderLinks | None = None
    shelf: Shelf | None = None
    policy_hook: str | None = None
    # WP-70: the folder's own programs (relative paths, runnable) and the folder of them that goes first on the
    # engine's PATH, so the worker, not the image, owns the CLI's version (an image's copy lags until it is rebuilt).
    executables: tuple[str, ...] = ()
    bin_dir: str | None = None

    def mount(self) -> MountSpec:
        return MountSpec(self.target, read_only=True)

    async def admit(self, sandbox: SandboxHandle) -> str | None:
        """The image's contract version, checked before anything is written; ``None`` when none is required."""
        return None if self.contract is None else await self.contract.check(sandbox)

    async def write(
        self,
        sandbox: SandboxHandle,
        tools: tuple[McpTool, ...],
        endpoints: GatewayEndpoints,
        skills: tuple[str, ...] = (),
    ) -> int:
        """Seeds the folder for this turn; the number of tools it lists."""
        files = self.render(tools, endpoints, skills)
        links = {} if self.links is None else self.links(files)
        await sandbox.seed_tar(self.target, config_tar(files, links=links, executables=self.executables))
        return len(tools)

    def bin_path(self) -> str | None:
        """Where the folder's programs are in the sandbox; ``None`` when it puts none on the PATH."""
        return None if self.bin_dir is None else posixpath.join(self.target, self.bin_dir)

    async def path_env(self, sandbox: SandboxHandle, env: Mapping[str, str]) -> dict[str, str]:
        """The engine's ``PATH`` with the folder's programs first (``{}`` when it has none), read from the image's own,
        and a login profile in the engine's home that puts them first again: Codex runs each command as ``bash -lc``,
        and the images' ``/etc/profile`` resets ``PATH``. The profile is the sandbox user's own file, as anything else
        in its home is: it changes only what that user's shells find first."""
        bin_path = self.bin_path()
        if bin_path is None:
            return {}
        line = f'case ":$PATH:" in *":{bin_path}:"*) ;; *) PATH="{bin_path}:$PATH"; export PATH ;; esac'
        home = env.get("HOME", "/tmp")
        code, out, _ = await run_in(sandbox, ["sh", "-c", _PROFILE_SCRIPT, "pxh-path", line], {"HOME": home})
        image_path = out.strip() if code == 0 and out.strip() else DEFAULT_PATH
        return {"PATH": f"{bin_path}:{image_path}"}


    def shelves(self, tool: str) -> bool:
        return self.shelf is not None and self.shelf.holds(tool)

    async def shelve(self, sandbox: SandboxHandle, name: str, text: str, *, suffix: str = ".md") -> str:
        """Writes ``text`` as ``<name><suffix>`` on the shelf, read-only to the engine like the rest of the folder;
        the path the engine reads it at. A file of the same name is replaced (a replayed call writes the same text)."""
        assert self.shelf is not None
        await sandbox.seed_tar(
            self.target,
            config_tar({f"{self.shelf.folder}/{name}{suffix}": text.encode()}, dirs=(self.shelf.folder,)),
        )
        return f"{self.shelf.path}/{name}{suffix}"


__all__ = ["ImageContract", "Shelf", "ToolFolder", "granted_tools"]
