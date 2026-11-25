"""ACP agent profiles: each long-tail coding agent is data, not code (``acp_agents.json``, signed release data).

A profile names the agent's pinned binary (path and sha256), the image it runs in, how to start it, how its model
traffic reaches the model gateway (env and config templates), the names its native tools carry, how instructions
reach it, and how a session resumes. ``AcpAdapter`` drives any profile the same way; adding an agent is a reviewed
change to the document and, for a release, its signature.

The document is signed with the release key that signs the vendor rules (``RELEASE_KEYS``,
``scripts/engines/sign_vendor_rules.py``) by the release build, never in the tree (ADR-SEC-01 D7); a test stack signs
it with its own test root. Unsigned, or signed by a key the install does not trust, no profile is installed and the
sandbox broker names the engine untrusted, except on a dev install with ``PIPESHUB_ACP_PROFILES_UNSIGNED``, which
reads the unsigned document (``harness_engines.profile_trust``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, TypeGuard, cast

from harness_core.access.errors import VendorRulesInvalid
from harness_core.access.types import ModelApi
from harness_core.access.vendor_rules import verify_release_document
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import (
    ACP_KIND_PREFIX,
    SEALED_DIR,
    EngineVersion,
    is_acp_kind,
)
from harness_engines.acp.protocol import PROTOCOL_VERSION

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

DOCUMENT_SCHEMA: Final = "pipeshub.acp-agents/1"
WORKDIR: Final = "/workspace"
# Where a profile's rendered config files and the run's instructions live: root's and read-only (``put_config``).
CONFIG_ROOT: Final = "/state/acp"
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
# ``@``: an npm scope folder (DeepSeek Harness's entry point is node_modules/@deepseek-ai/dsh/lib/bin.js).
_ABSOLUTE = re.compile(r"^/[A-Za-z0-9._/@-]{1,255}$")
_GATEWAY_ENV_KEYS = frozenset({"modelToken", "toolsToken", "modelBaseUrl", "model", "skillsDir"})
# How a session continues in a fresh sandbox: ``load`` (``session/load``, the agent replays it), ``resume``
# (``session/resume``, advertised as ``sessionCapabilities.resume``: the agent restores it without a replay, DeepSeek
# Harness, WP-105), either from its files under ``resume.paths``; or ``recap`` (a new session told the earlier turns).
ResumeMode = Literal["load", "resume", "recap"]
# How a config template carries the run's native tool rules: an object of name -> allow/deny value, or the list of
# the names the mode denies (an agent whose config only excludes tools, Gemini CLI's ``tools.exclude``), or the list
# of the names it allows (an agent whose config lists the tools it keeps, Goose's ``available_tools``).
ToolRulesForm = Literal["map", "denied", "allowed"]
# How the driver asks whether an agent has the tool gateway, where ACP itself cannot (E2E3-F6): ``opencode`` asks the
# agent's own server; ``gateway`` asks the gateway, for an agent with no status API that connects its MCP servers
# only while it opens a session and never retries one that refused (Gemini CLI).
McpStatusApi = Literal["opencode", "gateway"]
McpDeclare = Literal["session", "config"]
# When an agent asked by the gateway connects its MCP servers: inside ``session/new``/``session/load`` (Gemini CLI,
# checked once), or after answering it (Qwen Code's background discovery, waited for before the first prompt).
McpConnects = Literal["session", "background"]
# What the gateway counts as the agent's own connect: a tool list (Gemini CLI, Qwen Code list the tools as they
# connect), or the MCP handshake (Goose connects inside ``session/new`` but lists the tools only once a prompt arrives,
# R30ENG-FU-3).
McpWitness = Literal["list", "handshake"]
# How an agent writes a finished command's result: the output alone (``text``), Gemini CLI's labelled envelope
# (``Command:``, ``Directory:``, ``Output:``, ``Error:``, ``Exit Code:``; Qwen Code), whose ``Output:`` section is the
# output and ``(empty)`` its absence, or the output with an exit-status marker as its last line (``marked``: DeepSeek
# Harness's ``[exit code: N]``, which is the only place it reports a failed command's code).
CommandResult = Literal["text", "labelled", "marked"]
# Where the driver learns of the agent's own compaction: opencode's loopback API, or Qwen Code's session record.
CompactionNotice = Literal["opencode", "qwen"]
# An agent's own session log the driver reads at each turn's end, when ACP carries neither the turn's usage nor a
# compaction notice: ``dsh``, DeepSeek Harness's (each model call's usage and each compaction it committed; WP-105).
SessionRecord = Literal["dsh"]


class AcpProfileInvalid(ValueError):
    """The ACP agents document is not this release's schema; ``detail`` names what is wrong (never secrets)."""

    reason_code = "acp_profile_invalid"


@dataclass(frozen=True, slots=True)
class ConfigTemplate:
    """A config the agent reads: an env variable's value or a file under ``CONFIG_ROOT``, rendered from a JSON
    template (``harness_engines.acp.launch``); ``tool_rules`` names the object inside it that gets one entry per
    native tool, allowed or denied by the run's mode."""

    template: JsonValue
    env: str | None = None
    path: str | None = None
    tool_rules: tuple[str, ...] | None = None
    tool_rules_form: ToolRulesForm = "map"
    # A file the image holds whose settings this one keeps, the rendered ones added beside them (never replacing
    # one): only for a config under ``SEALED_DIR``, the agent's system settings (Gemini CLI, WP-59).
    base: str | None = None

    @property
    def sealed(self) -> bool:
        return self.path is not None and self.path.startswith(SEALED_DIR + "/")


@dataclass(frozen=True, slots=True)
class McpStatus:
    """Where the driver checks the tool gateway before the first prompt; ACP has no such request, and an agent may
    open a session without a gateway that refused it.

    ``opencode``: the agent's own HTTP API, on ``port`` inside the sandbox, reports and reconnects its MCP servers,
    asked once the session is open. ``gateway``: the agent reports nothing, so the gateway's tools are listed from
    inside the sandbox over the agent's own route with ``runtime`` (an interpreter in the agent's image), before the
    session is opened, which is where the agent connects. Either way the gateway's own count of tool lists then
    witnesses the agent's connect: once the session is open (``connects: session``), or waited for, bounded by the
    gateway timeout, when the agent connects after answering ``session/new`` (``connects: background``). ``witness``
    says which count: tool lists (``list``), or MCP handshakes for an agent that lists only when prompted
    (``handshake``)."""

    api: McpStatusApi
    port: int | None = None
    runtime: str | None = None
    connects: McpConnects = "session"
    witness: McpWitness = "list"

    @property
    def before_session(self) -> bool:
        return self.api == "gateway"

    @property
    def waits_for_witness(self) -> bool:
        return self.api == "gateway" and self.connects == "background"


@dataclass(frozen=True, slots=True)
class AcpAgentProfile:
    id: str
    name: str
    agent_name: str
    agent_version: str
    binary: str
    binary_digest: str
    image: str
    argv: tuple[str, ...]
    model_api: ModelApi
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    gateway_env: Mapping[str, str] = field(default_factory=dict[str, str])
    configs: tuple[ConfigTemplate, ...] = ()
    native_tools: Mapping[NativeTool, tuple[str, ...]] = field(default_factory=dict[NativeTool, tuple[str, ...]])
    plan_tool: str | None = None
    tool_rule_values: tuple[str, str] = ("allow", "deny")
    # The names the agent's permission config uses per native tool, where they differ from the tools' own.
    tool_rule_names: Mapping[NativeTool, tuple[str, ...]] = field(default_factory=dict[NativeTool, tuple[str, ...]])
    # ``session/set_config_option`` the driver sends after opening a session: config id -> value.
    session_config: Mapping[str, str | bool] = field(default_factory=dict[str, "str | bool"])
    mcp_server: str = "tools"
    # How the model sees a gateway tool, ``{server}`` and ``{tool}`` filled in.
    mcp_tool_name: str = "{server}_{tool}"
    # Where the driver checks the gateway is connected before the first prompt; None: the agent's word is taken.
    mcp_status: McpStatus | None = None
    # Where the gateway's MCP server is declared: ``session`` sends it in ``session/new`` and ``session/load``;
    # ``config`` leaves it to the rendered config alone and sends no server, for an agent whose ACP mapping drops
    # what the config sets (Gemini CLI keeps only the URL and headers, so the run's tool timeout was lost).
    mcp_declare: McpDeclare = "session"
    # ``_meta`` of the gateway's ``mcpServers`` entry, a template rendered per run (Goose reads the tool timeout there).
    mcp_meta: JsonValue = None
    # Where the agent's own compaction is learnt, when ACP carries no notice (MG-10, C3.67); None: not asked.
    compaction_notice: CompactionNotice | None = None
    instructions_file: str | None = None
    # Folders under ``CONFIG_ROOT`` the agent also writes its own state in (root's, sticky, as ``CONFIG_ROOT``).
    homes: tuple[str, ...] = ()
    # Where the agent discovers its user skills: a root-owned link to the run's skills folder.
    skills_link: str | None = None
    # An agent that names a tool only in its call id (Gemini CLI: ``<tool>__<id>``): the separator after the name.
    call_id_name_separator: str | None = None
    # Where the agent reports a turn's usage when it is not ACP's ``usage`` (a key path in the prompt's answer,
    # holding ``input_tokens`` and ``output_tokens``).
    usage_path: tuple[str, ...] | None = None
    # Where the agent reports each model call's usage when its prompt's answer carries none (a key path in a
    # ``agent_message_chunk`` holding ACP's ``inputTokens`` and ``outputTokens``; Qwen Code's ``_meta.usage``): the
    # turn's usage is their sum.
    call_usage_path: tuple[str, ...] | None = None
    # Where a tool call names its tool when ACP's ``name`` is absent and the title is a display text (a key path in
    # the call; Goose's ``_meta.goose.toolCall.toolName``).
    tool_name_path: tuple[str, ...] | None = None
    # The agent's own reason for a failed prompt (its error's ``data.reason``) -> the engine error code it is, for an
    # agent that drops the model gateway's ``[gateway:<code>]`` (Goose's ``credits_exhausted`` for a 402).
    error_reasons: Mapping[str, str] = field(default_factory=dict[str, str])
    command_result: CommandResult = "text"
    # The agent sometimes reports a command that printed something as having printed nothing (Qwen Code 0.26.5's own
    # race, R32ENGFU-FU-1): an empty result is unverified.
    output_may_be_lost: bool = False
    resume: ResumeMode = "recap"
    state_paths: tuple[str, ...] = ()
    client_fs: bool = False
    max_tools: int | None = None
    # Bare tool names the agent drops from every MCP server's list (``EngineCapabilities.reserved_tool_names``).
    reserved_tool_names: frozenset[str] = frozenset()
    protocol_version: int = PROTOCOL_VERSION
    # What ``initialize``'s ``agentInfo.version`` says, where the agent reports a fixed protocol-surface version
    # rather than its release's (DeepSeek Harness's ACP server says ``0.0.1`` whatever the release); None: the
    # release's version. The pin is the version and the binary's digest either way.
    agent_reported_version: str | None = None
    # The agent's own session log, read at each turn's end for its usage and compactions; None: not read.
    session_record: SessionRecord | None = None

    @property
    def reported_version(self) -> str:
        return self.agent_reported_version or self.agent_version

    @property
    def resumes_from_files(self) -> bool:
        """The agent continues a session from its own files under ``state_paths`` (``load`` or ``resume``)."""
        return self.resume in ("load", "resume")

    @property
    def kind(self) -> str:
        return ACP_KIND_PREFIX + self.id

    @property
    def engine(self) -> EngineVersion:
        return EngineVersion(kind=self.kind, version=self.agent_version, binary_digest=self.binary_digest)

    def exposed_tool_name(self, granted: str) -> str:
        return self.mcp_tool_name.format(server=self.mcp_server, tool=granted)

    @property
    def gateway_tool_prefix(self) -> str:
        """What every gateway tool's exposed name starts with (the pattern up to ``{tool}``)."""
        return self.mcp_tool_name.split("{tool}", 1)[0].format(server=self.mcp_server)


def _mcp_status(raw: object, where: str) -> McpStatus | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _invalid(f"{where} is not an object")
    status = cast("dict[str, object]", raw)
    api = status.get("api")
    if api == "gateway":
        runtime = status.get("runtime")
        connects = status.get("connects", "session")
        witness = status.get("witness", "list")
        if not set(status) <= {"api", "runtime", "connects", "witness"} or not isinstance(runtime, str):
            raise _invalid(
                f"{where} for the gateway names a runtime, how the agent connects, what witnesses it and nothing else"
            )
        if not _ABSOLUTE.match(runtime) or ".." in runtime:
            raise _invalid(f"{where}.runtime is not an absolute path")
        if connects not in ("session", "background"):
            raise _invalid(f"{where}.connects is neither session nor background")
        if witness not in ("list", "handshake"):
            raise _invalid(f"{where}.witness is neither list nor handshake")
        return McpStatus(api="gateway", runtime=runtime, connects=connects, witness=witness)
    if api != "opencode":
        raise _invalid(f"{where}.api is neither opencode nor gateway")
    port = status.get("port")
    if set(status) != {"api", "port"} or not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise _invalid(f"{where}.port is not a port")
    return McpStatus(api="opencode", port=port)


def _invalid(detail: str) -> AcpProfileInvalid:
    return AcpProfileInvalid(f"ACP agents document: {detail}")


def _under_config(path: object) -> TypeGuard[str]:
    return (
        isinstance(path, str) and bool(_ABSOLUTE.match(path)) and path.startswith(CONFIG_ROOT + "/") and ".." not in path
    )


def _under_sealed(path: object) -> TypeGuard[str]:
    return (
        isinstance(path, str) and bool(_ABSOLUTE.match(path)) and path.startswith(SEALED_DIR + "/") and ".." not in path
    )


def _str(raw: Mapping[str, object], key: str, where: str, *, pattern: re.Pattern[str] | None = None) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value or (pattern is not None and not pattern.match(value)):
        raise _invalid(f"{where}.{key} is not a valid string")
    return value


def _key_path(entry: Mapping[str, object], key: str, where: str) -> tuple[str, ...] | None:
    """``entry[key]`` as a key path (a non-empty list of non-empty strings), or None when absent."""
    raw = entry.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw or not all(isinstance(k, str) and k for k in cast("list[object]", raw)):
        raise _invalid(f"{where}.{key} is not a key path")
    return tuple(cast("list[str]", raw))


def _str_map(raw: object, where: str, *, keys: re.Pattern[str] | None = None) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _invalid(f"{where} is not an object")
    out: dict[str, str] = {}
    for key, value in cast("dict[str, object]", raw).items():
        if key.startswith("_"):
            continue
        if not isinstance(value, str) or (keys is not None and not keys.match(key)):
            raise _invalid(f"{where}.{key} is not a string entry")
        out[key] = value
    return out


def _configs(raw: object, where: str) -> tuple[ConfigTemplate, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _invalid(f"{where} is not a list")
    out: list[ConfigTemplate] = []
    for index, item in enumerate(cast("list[object]", raw)):
        if not isinstance(item, dict) or "template" not in item:
            raise _invalid(f"{where}[{index}] has no template")
        entry = cast("dict[str, object]", item)
        env, path = entry.get("env"), entry.get("path")
        if (env is None) == (path is None):
            raise _invalid(f"{where}[{index}] needs exactly one of env and path")
        if env is not None and (not isinstance(env, str) or not _ENV_NAME.match(env)):
            raise _invalid(f"{where}[{index}].env is not an env name")
        if path is not None and not (_under_config(path) or _under_sealed(path)):
            raise _invalid(f"{where}[{index}].path is not under {CONFIG_ROOT} or {SEALED_DIR}")
        base = entry.get("base")
        if base is not None and (
            not _under_sealed(path)
            or not isinstance(base, str)
            or not _ABSOLUTE.match(base)
            or ".." in base
            or not isinstance(entry["template"], dict)
        ):
            raise _invalid(f"{where}[{index}].base is an absolute path, for an object template under {SEALED_DIR}")
        rules = entry.get("toolRules")
        if rules is not None and (not isinstance(rules, list) or not all(isinstance(k, str) for k in cast("list[object]", rules))):
            raise _invalid(f"{where}[{index}].toolRules is not a key path")
        form = entry.get("toolRulesForm", "map")
        if form not in ("map", "denied", "allowed") or (form != "map" and not rules):
            raise _invalid(f"{where}[{index}].toolRulesForm is not map, denied or allowed (with toolRules)")
        out.append(
            ConfigTemplate(
                template=cast("JsonValue", entry["template"]),
                env=env,
                path=path,
                tool_rules=tuple(cast("list[str]", rules)) if rules is not None else None,
                tool_rules_form=form,
                base=cast("str | None", base),
            )
        )
    return tuple(out)


def _native(raw: object, where: str) -> dict[NativeTool, tuple[str, ...]]:
    if not isinstance(raw, dict):
        raise _invalid(f"{where} is not an object")
    out: dict[NativeTool, tuple[str, ...]] = {}
    for key, names in cast("dict[str, object]", raw).items():
        if key.startswith("_"):
            continue
        try:
            tool = NativeTool(key)
        except ValueError:
            raise _invalid(f"{where}.{key} is not a native tool") from None
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in cast("list[object]", names)):
            raise _invalid(f"{where}.{key} is not a list of tool names")
        out[tool] = tuple(cast("list[str]", names))
    return out


def parse_profile(raw: object) -> AcpAgentProfile:
    if not isinstance(raw, dict):
        raise _invalid("an agent is not an object")
    entry = cast("dict[str, object]", raw)
    agent_id = _str(entry, "id", "agent", pattern=_ID)
    where = f"agents.{agent_id}"
    if not is_acp_kind(ACP_KIND_PREFIX + agent_id):
        raise _invalid(f"{where}.id does not make an engine kind")
    agent = entry.get("agent")
    binary = entry.get("binary")
    if not isinstance(agent, dict) or not isinstance(binary, dict):
        raise _invalid(f"{where} needs agent and binary objects")
    agent_obj, binary_obj = cast("dict[str, object]", agent), cast("dict[str, object]", binary)
    argv = entry.get("argv")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in cast("list[object]", argv)):
        raise _invalid(f"{where}.argv is not a list of strings")
    try:
        model_api = ModelApi(_str(entry, "modelApi", where))
    except ValueError:
        raise _invalid(f"{where}.modelApi is not a model API") from None
    gateway_env = _str_map(entry.get("gatewayEnv"), f"{where}.gatewayEnv")
    if not set(gateway_env) <= _GATEWAY_ENV_KEYS or "modelToken" not in gateway_env:
        raise _invalid(f"{where}.gatewayEnv names unknown keys or no modelToken")
    if not all(_ENV_NAME.match(name) for name in gateway_env.values()):
        raise _invalid(f"{where}.gatewayEnv values are not env names")
    mcp: object = entry.get("mcp") or {}
    if not isinstance(mcp, dict):
        raise _invalid(f"{where}.mcp is not an object")
    mcp_obj = cast("dict[str, object]", mcp)
    tool_name = mcp_obj.get("toolName", "{server}_{tool}")
    if not isinstance(tool_name, str) or tool_name.count("{tool}") != 1 or not tool_name.endswith("{tool}"):
        raise _invalid(f"{where}.mcp.toolName must end with {{tool}}")
    status = _mcp_status(mcp_obj.get("status"), f"{where}.mcp.status")
    declare = mcp_obj.get("declare", "session")
    if declare not in ("session", "config"):
        raise _invalid(f"{where}.mcp.declare is neither session nor config")
    if declare == "config" and not any(
        "${mcp_server}" in json.dumps(c.get("template")) and "${tools_mcp_url}" in json.dumps(c.get("template"))
        for c in cast("list[dict[str, object]]", entry.get("configs") or [])
        if isinstance(c, dict)
    ):
        # The gateway would then be declared nowhere: the agent would open every session without it.
        raise _invalid(f"{where}.mcp.declare config needs a config that names the gateway server and its URL")
    mcp_meta = mcp_obj.get("meta")
    if mcp_meta is not None and (not isinstance(mcp_meta, dict) or declare != "session"):
        raise _invalid(f"{where}.mcp.meta is an object, for a gateway declared in the session")
    resume: object = entry.get("resume") or {}
    if not isinstance(resume, dict):
        raise _invalid(f"{where}.resume is not an object")
    resume_obj = cast("dict[str, object]", resume)
    mode = resume_obj.get("mode", "recap")
    if mode not in ("load", "resume", "recap"):
        raise _invalid(f"{where}.resume.mode is not load, resume or recap")
    state_paths = resume_obj.get("paths", [])
    if not isinstance(state_paths, list) or not all(
        isinstance(p, str) and _ABSOLUTE.match(p) and p.startswith("/state/") and ".." not in p
        for p in cast("list[object]", state_paths)
    ):
        raise _invalid(f"{where}.resume.paths are not folders under /state")
    if mode in ("load", "resume") and not state_paths:
        raise _invalid(f"{where}.resume.mode {mode} needs the paths the agent keeps its sessions in")
    notice = entry.get("compactionNotice")
    if notice == "opencode" and (status is None or status.api != "opencode"):
        raise _invalid(f"{where}.compactionNotice is opencode, for an agent whose mcp.status is opencode's own API")
    if notice == "qwen" and (status is None or status.runtime is None or not state_paths):
        raise _invalid(f"{where}.compactionNotice qwen reads the session record with mcp.status.runtime under resume.paths")
    if notice not in (None, "opencode", "qwen"):
        raise _invalid(f"{where}.compactionNotice is neither opencode nor qwen")
    record = entry.get("sessionRecord")
    if record not in (None, "dsh"):
        raise _invalid(f"{where}.sessionRecord is not dsh")
    if record is not None and (notice is not None or status is None or status.runtime is None or not state_paths):
        # The record already carries the compactions; it is read with the agent's runtime under resume.paths.
        raise _invalid(f"{where}.sessionRecord is read with mcp.status.runtime under resume.paths, without compactionNotice")
    instructions = entry.get("instructionsFile")
    if instructions is not None and not _under_config(instructions):
        raise _invalid(f"{where}.instructionsFile is not under {CONFIG_ROOT}")
    homes = entry.get("homes", [])
    if not isinstance(homes, list) or not all(_under_config(h) for h in cast("list[object]", homes)):
        raise _invalid(f"{where}.homes are not folders under {CONFIG_ROOT}")
    skills_link = entry.get("skillsLink")
    if skills_link is not None and (not _under_config(skills_link) or "skillsDir" not in gateway_env):
        raise _invalid(f"{where}.skillsLink is not under {CONFIG_ROOT} or gatewayEnv has no skillsDir")
    separator = entry.get("callIdNameSeparator")
    if separator is not None and (not isinstance(separator, str) or not separator):
        raise _invalid(f"{where}.callIdNameSeparator is not a string")
    usage_path = _key_path(entry, "usagePath", where)
    call_usage_path = _key_path(entry, "callUsagePath", where)
    name_path = _key_path(entry, "toolNamePath", where)
    error_reasons = _str_map(entry.get("errorReasons"), f"{where}.errorReasons", keys=_ID)
    if not all(_CODE.match(code) for code in error_reasons.values()):
        raise _invalid(f"{where}.errorReasons values are not engine error codes")
    command_result = entry.get("commandResult", "text")
    if command_result not in ("text", "labelled", "marked"):
        raise _invalid(f"{where}.commandResult is not text, labelled or marked")
    output_may_be_lost = entry.get("outputMayBeLost", False)
    if not isinstance(output_may_be_lost, bool):
        raise _invalid(f"{where}.outputMayBeLost is not a boolean")
    rule_values = entry.get("toolRuleValues", ["allow", "deny"])
    if not isinstance(rule_values, list) or len(cast("list[object]", rule_values)) != 2:
        raise _invalid(f"{where}.toolRuleValues is not [allow, deny]")
    max_tools = entry.get("maxTools")
    if max_tools is not None and (not isinstance(max_tools, int) or isinstance(max_tools, bool) or max_tools < 1):
        raise _invalid(f"{where}.maxTools is not a positive integer")
    reserved_names = entry.get("reservedToolNames", [])
    if not isinstance(reserved_names, list) or not all(
        isinstance(n, str) and n for n in cast("list[object]", reserved_names)
    ):
        raise _invalid(f"{where}.reservedToolNames is not a list of tool names")
    protocol = entry.get("protocolVersion", PROTOCOL_VERSION)
    if protocol != PROTOCOL_VERSION:
        raise _invalid(f"{where}.protocolVersion is not {PROTOCOL_VERSION}")
    plan_tool = entry.get("planTool")
    if plan_tool is not None and not isinstance(plan_tool, str):
        raise _invalid(f"{where}.planTool is not a string")
    session_config = entry.get("sessionConfig") or {}
    if not isinstance(session_config, dict) or not all(
        isinstance(k, str) and isinstance(v, str | bool) for k, v in cast("dict[object, object]", session_config).items()
    ):
        raise _invalid(f"{where}.sessionConfig is not an object of strings and booleans")
    profile = AcpAgentProfile(
        id=agent_id,
        name=_str(entry, "name", where),
        agent_name=_str(agent_obj, "name", f"{where}.agent"),
        agent_version=_str(agent_obj, "version", f"{where}.agent"),
        agent_reported_version=_str(agent_obj, "reportedVersion", f"{where}.agent") if "reportedVersion" in agent_obj else None,
        binary=_str(binary_obj, "path", f"{where}.binary", pattern=_ABSOLUTE),
        binary_digest=_str(binary_obj, "digest", f"{where}.binary"),
        image=_str(entry, "image", where),
        argv=tuple(cast("list[str]", argv)),
        model_api=model_api,
        env=_str_map(entry.get("env"), f"{where}.env", keys=_ENV_NAME),
        gateway_env=gateway_env,
        configs=_configs(entry.get("configs"), f"{where}.configs"),
        native_tools=_native(entry.get("nativeTools", {}), f"{where}.nativeTools"),
        tool_rule_names=_native(entry.get("toolRuleNames", {}), f"{where}.toolRuleNames"),
        session_config=cast("dict[str, str | bool]", session_config),
        plan_tool=plan_tool,
        tool_rule_values=(str(cast("list[object]", rule_values)[0]), str(cast("list[object]", rule_values)[1])),
        mcp_server=_str(mcp_obj, "server", f"{where}.mcp", pattern=_ID) if "server" in mcp_obj else "tools",
        mcp_tool_name=tool_name,
        mcp_status=status,
        mcp_declare=cast("McpDeclare", declare),
        mcp_meta=cast("JsonValue", mcp_meta),
        compaction_notice=notice,
        instructions_file=instructions,
        homes=tuple(cast("list[str]", homes)),
        skills_link=skills_link,
        call_id_name_separator=separator,
        usage_path=usage_path,
        call_usage_path=call_usage_path,
        tool_name_path=name_path,
        error_reasons=error_reasons,
        command_result=cast("CommandResult", command_result),
        output_may_be_lost=output_may_be_lost,
        resume=mode,
        state_paths=tuple(cast("list[str]", state_paths)),
        client_fs=entry.get("clientFs") is True,
        max_tools=max_tools,
        reserved_tool_names=frozenset(cast("list[str]", reserved_names)),
        session_record=cast("SessionRecord | None", record),
    )
    try:
        _ = profile.engine
    except ValueError as exc:
        raise _invalid(f"{where}: {exc}") from None
    reserved = {*profile.gateway_env.values(), *(c.env for c in profile.configs if c.env)}
    if reserved & set(profile.env):
        raise _invalid(f"{where}.env sets a variable the launch renders")
    return profile


def parse_acp_agents(document: bytes) -> tuple[AcpAgentProfile, ...]:
    try:
        raw: object = json.loads(document)
    except ValueError:
        raise _invalid("not JSON") from None
    if not isinstance(raw, dict):
        raise _invalid("not an object")
    doc = cast("dict[str, object]", raw)
    agents = doc.get("agents")
    if doc.get("schema") != DOCUMENT_SCHEMA or not isinstance(agents, list):
        raise _invalid("not this release's schema")
    profiles = tuple(parse_profile(agent) for agent in cast("list[object]", agents))
    ids = [p.id for p in profiles]
    if len(set(ids)) != len(ids):
        raise _invalid("two agents share an id")
    return profiles


def load_acp_agents(
    document: bytes | None, signature: bytes | None, keys: Mapping[str, bytes], *, unsigned_ok: bool = False
) -> tuple[AcpAgentProfile, ...]:
    """The profiles, verified against ``keys``; ``unsigned_ok`` (dev only) reads an unsigned document.

    Raises ``VendorRulesInvalid`` (signature) or ``AcpProfileInvalid`` (content)."""
    if unsigned_ok and document is not None and signature is None:
        return parse_acp_agents(document)
    return parse_acp_agents(verify_release_document(document, signature, keys))


__all__ = [
    "CONFIG_ROOT",
    "DOCUMENT_SCHEMA",
    "WORKDIR",
    "AcpAgentProfile",
    "AcpProfileInvalid",
    "CompactionNotice",
    "ConfigTemplate",
    "McpConnects",
    "McpWitness",
    "McpStatus",
    "ResumeMode",
    "SessionRecord",
    "VendorRulesInvalid",
    "load_acp_agents",
    "parse_acp_agents",
    "parse_profile",
]
