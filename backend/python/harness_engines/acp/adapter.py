"""``EngineAdapter`` for an ACP agent profile: one adapter per profile, its kind ``acp-<agent>``.

``open`` checks everything before the agent may act: restored state belongs to this profile and version
(``EngineStateIncompatible`` before anything runs), the binary hashes to the pin (EH1), the agent answers
``initialize`` with protocol version 1 and, when it names itself, the pinned version, and it can take the tool
gateway as an HTTP MCP server. The agent never authenticates: its only model credential is the gateway's
placeholder, swapped by the egress proxy.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, ClassVar

from harness_core.engines.binary import check_reported_version, verify_binary
from harness_core.engines.errors import EngineBinaryMismatch, EngineUnhealthy
from harness_core.engines.jsonview import as_object, as_str
from harness_core.engines.stopping import stop_after_failed_open, stop_process
from harness_core.engines.types import EngineCapabilities
from harness_core.logs import HarnessLog
from harness_core.sandbox.tar import put_config, put_files
from harness_core.workspace.sync.archive import Owner
from harness_engines.acp.launch import SESSION_POLICY_PATH, render_launch
from harness_engines.acp.permissions import SessionPolicy
from harness_engines.acp.profiles import CONFIG_ROOT
from harness_engines.acp.protocol import INITIALIZE, METHOD_NOT_FOUND, PROTOCOL_VERSION
from harness_engines.acp.rpc import AcpRpc, Incoming
from harness_engines.acp.sealed import seal, sealed_files
from harness_engines.acp.session import AcpSession
from harness_engines.acp.state import decode_state

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineState
    from harness_core.engines.types import (
        EngineKind,
        EngineLaunch,
        EngineProfile,
        GatewayEndpoints,
        JsonValue,
    )
    from harness_core.sandbox import ProcessHandle, SandboxHandle
    from harness_engines.acp.profiles import AcpAgentProfile

_LOG = logging.getLogger(__name__)
INITIALIZE_TIMEOUT_S = 60.0
CLIENT_NAME = "pipeshub-harness"
CLIENT_VERSION = "1"


class AcpAdapter:
    """Drives ``profile``'s agent. ``kind`` is per instance: one adapter per profile (``acp_adapters``)."""

    kind: ClassVar[EngineKind]

    def __init__(self, profile: AcpAgentProfile, *, log: HarnessLog | None = None, **session: float) -> None:
        self.profile = profile
        self._log = log or HarnessLog(_LOG)
        self._session_options = session

    @property
    def version(self) -> str:
        return self.profile.agent_version

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            can_steer=False,
            native_skills="skillsDir" in self.profile.gateway_env,
            sub_agents=False,
            model_apis=frozenset({self.profile.model_api}),
            max_tools=self.profile.max_tools,
            drive="stdio",
            can_compact=False,
            reserved_tool_names=self.profile.reserved_tool_names,
        )

    def render_launch(self, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str) -> EngineLaunch:
        return render_launch(self.profile, profile, endpoints, skills_dir)

    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> AcpSession:
        agent = self.profile
        if launch.engine != agent.engine:
            raise ValueError(f"profile {agent.id} cannot open {launch.engine.ref}")
        log = self._log.bind(**dict(correlation or {}), sandboxId=sandbox.id, engineKind=agent.kind, engineVersion=agent.agent_version)
        restored = decode_state(restore, launch.engine, agent=agent.id, roots=agent.state_paths) if restore is not None else None
        await verify_binary(sandbox, agent.binary, launch.engine, log)
        if restored is not None and restored.files:
            # The agent keeps adding to its sessions: they are the sandbox user's (P5.5 Owner).
            await put_files(sandbox, restored.files, owner=Owner())
        # What the agent obeys is root's and read-only, in folders it shares with root (SEC1), and no folder above
        # it is the user's, not even one restored state made.
        skills_dir = launch.env.get(agent.gateway_env.get("skillsDir", ""), "")
        links = {agent.skills_link: skills_dir} if agent.skills_link and skills_dir else None
        sealed = sealed_files(launch.files)
        own = {path: content for path, content in launch.files.items() if path not in sealed}
        await put_config(sandbox, own, homes=(CONFIG_ROOT, *agent.homes), links=links, own_parents=True)
        await seal(sandbox, agent, sealed, log)
        policy = SessionPolicy.from_json(launch.files[SESSION_POLICY_PATH])
        process = await sandbox.exec(list(launch.argv), env=dict(launch.env), stdin=True)
        rpc = AcpRpc(process)
        try:
            holder: list[AcpSession] = []

            async def handle(message: Incoming) -> None:
                if holder:
                    await holder[0].handle(message)
                elif message.is_request:
                    # Nothing is served before the session exists: an early ask is answered, never left waiting.
                    await rpc.reply_error(message.id, METHOD_NOT_FOUND, "method not found")

            def ended(error: BaseException | None) -> None:
                if holder:
                    holder[0].ended(error)

            rpc.start(handle, ended)
            try:
                init = as_object(
                    await rpc.request(
                        INITIALIZE,
                        {
                            "protocolVersion": PROTOCOL_VERSION,
                            "clientCapabilities": {
                                "fs": {"readTextFile": policy.client_fs_read, "writeTextFile": policy.client_fs_write},
                                "terminal": False,
                            },
                            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                        },
                        timeout_s=INITIALIZE_TIMEOUT_S,
                    )
                )
            except TimeoutError as exc:
                raise EngineUnhealthy(f"{agent.name} did not answer initialize") from exc
            if init.get("protocolVersion") != PROTOCOL_VERSION:
                log.warning("engine.acp.protocol_mismatch", reasonCode=EngineBinaryMismatch.reason_code)
                raise EngineBinaryMismatch(f"{agent.name} does not speak ACP protocol version {PROTOCOL_VERSION}")
            info = as_object(init.get("agentInfo"))
            if info:
                check_reported_version(launch.engine, as_str(info.get("version")), log, expected=agent.reported_version)
            capabilities = as_object(init.get("agentCapabilities"))
            if as_object(capabilities.get("mcpCapabilities")).get("http") is not True:
                log.warning("engine.acp.no_http_mcp", reasonCode="acp_no_http_mcp")
                raise EngineUnhealthy(f"{agent.name} cannot take the tool gateway as an HTTP MCP server")
            session = AcpSession(
                agent=agent,
                engine=launch.engine,
                sandbox=sandbox,
                process=process,
                rpc=rpc,
                policy=policy,
                load_session=_continues(agent, capabilities),
                restored=restored,
                log=log,
                **self._session_options,  # pyright: ignore[reportArgumentType]
            )
            holder.append(session)
        except BaseException:
            await stop_after_failed_open(self._stop(process, rpc), log)
            raise
        log.info("engine.session.opened", restored=restored is not None)
        return session

    async def _stop(self, process: ProcessHandle, rpc: AcpRpc) -> None:
        await rpc.close()
        with contextlib.suppress(Exception):
            await stop_process(process)


def _continues(agent: AcpAgentProfile, capabilities: Mapping[str, JsonValue]) -> bool:
    """The agent takes back a saved session as the profile says it does: ``session/load`` where it advertises
    ``loadSession``, ``session/resume`` where it advertises ``sessionCapabilities.resume``."""
    if agent.resume == "load":
        return capabilities.get("loadSession") is True
    if agent.resume == "resume":
        return isinstance(as_object(capabilities.get("sessionCapabilities")).get("resume"), dict)
    return False


def acp_adapter_class(profile: AcpAgentProfile) -> type[AcpAdapter]:
    """``AcpAdapter`` with ``kind`` set to the profile's (the registry keys adapters by their class's kind)."""
    return type(f"AcpAdapter_{profile.id}", (AcpAdapter,), {"kind": profile.kind})


def acp_adapter(profile: AcpAgentProfile, *, log: HarnessLog | None = None, **session: float) -> AcpAdapter:
    return acp_adapter_class(profile)(profile, log=log, **session)


__all__ = ["CLIENT_NAME", "AcpAdapter", "acp_adapter", "acp_adapter_class"]
