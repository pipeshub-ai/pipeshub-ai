"""``render_launch`` for an ACP agent: a pure function from its profile, the run's profile and the gateways.

What the agent obeys is rendered from the profile's templates and written root's and read-only under
``CONFIG_ROOT`` (or passed in its env); the model gateway's URL and the token placeholders are the only endpoints it
is told. Templates are JSON with ``${name}`` placeholders (``placeholders``); a string that is exactly one
placeholder takes the value's own type (a number stays a number). A template's ``toolRules`` key path gets one entry
per native tool name: the profile's allow value for what the run's mode permits, its deny value for the rest, so
an agent with a permission config (opencode's) shows the model exactly the tools the mode allows. In the ``denied``
form the path holds a list instead, extended with the names the mode denies (Gemini CLI's ``tools.exclude``); in
the ``allowed`` form, with the names it allows (Goose's ``available_tools``, where an empty list would mean all).
An agent whose config switches whole tools on and off (DeepSeek Harness's plugin rows, WP-105) reads the mode from the
booleans ``${allows_<tool>}`` and ``${denies_<tool>}``, one pair per engine-neutral native tool.

The session's own policy (which native tools the mode allows, how gateway tools are named) travels in the launch
as ``SESSION_POLICY_PATH``: the session decides the agent's ``session/request_permission`` asks from it.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.budget.run_budget import native_steps
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.engines.policy import NativeTool, expected_native_tools
from harness_core.engines.types import EngineLaunch, EngineMode
from harness_engines.acp.profiles import CONFIG_ROOT, WORKDIR

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import EngineProfile, GatewayEndpoints, JsonValue
    from harness_engines.acp.profiles import AcpAgentProfile

SESSION_POLICY_PATH: Final = f"{CONFIG_ROOT}/session.json"
# Sub-agents need ACP's draft "Subagent Sessions"; until it is stable no ACP agent is given its own.
UNSUPPORTED: Final = frozenset({NativeTool.SUBAGENT})
_PLACEHOLDER = re.compile(r"\$\{([a-z_]+)\}")
_RELEASE_STEPS = RELEASE_DEFAULTS.engine_profile.limits.max_steps
DEFAULT_STEPS: Final = _RELEASE_STEPS.root if _RELEASE_STEPS is not None else 100


def allowed_native(profile: EngineProfile) -> frozenset[NativeTool]:
    """The native tools the run's mode permits an ACP agent: the engine-neutral set, less what ACP cannot carry."""
    return expected_native_tools(profile, skills=True) - UNSUPPORTED


def placeholders(
    agent: AcpAgentProfile, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str
) -> dict[str, JsonValue]:
    gateway = agent.gateway_env
    permitted = allowed_native(profile)
    switches: dict[str, JsonValue] = {}
    for tool in NativeTool:
        switches[f"allows_{tool.value}"] = tool in permitted
        switches[f"denies_{tool.value}"] = tool not in permitted
    return {
        **switches,
        "model_base_url": endpoints.model_base_url,
        "model_root_url": endpoints.model_root_url,
        "model": ENGINE_MODEL_ID,
        "tools_mcp_url": endpoints.tools_mcp_url,
        "tool_timeout_s": endpoints.tool_timeout_s,
        "tool_timeout_ms": endpoints.tool_timeout_s * 1000,
        "skills_dir": skills_dir,
        "workdir": WORKDIR,
        "max_steps": native_steps(profile.limits.max_steps, DEFAULT_STEPS),
        "model_token_env": gateway["modelToken"],
        "tools_token_env": gateway.get("toolsToken", gateway["modelToken"]),
        # The proxy's placeholders, for an agent that reads a header only from its config, never from its env.
        "model_token": endpoints.token_placeholder,
        "tools_token": endpoints.tools_placeholder,
        "mcp_server": agent.mcp_server,
        "instructions_file": agent.instructions_file or "",
    }


def render_template(template: JsonValue, values: Mapping[str, JsonValue]) -> JsonValue:
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template)
        if whole is not None:
            return _value(values, whole.group(1))
        return _substitute(template, values)
    if isinstance(template, dict):
        return {_substitute(key, values): render_template(value, values) for key, value in template.items()}
    if isinstance(template, list):
        return [render_template(value, values) for value in template]
    return template


def _substitute(text: str, values: Mapping[str, JsonValue]) -> str:
    return _PLACEHOLDER.sub(lambda m: str(_value(values, m.group(1))), text)


def _value(values: Mapping[str, JsonValue], name: str) -> JsonValue:
    if name not in values:
        raise ValueError(f"unknown template placeholder {name}")
    return values[name]


def tool_rules(agent: AcpAgentProfile, profile: EngineProfile) -> dict[str, str]:
    """One entry per native tool's permission name, in the profile's order: allowed by the mode, or denied."""
    allow, deny = agent.tool_rule_values
    permitted = allowed_native(profile)
    rules: dict[str, str] = {}
    for tool, names in agent.native_tools.items():
        for name in agent.tool_rule_names.get(tool, names):
            rules[name] = allow if tool in permitted else deny
    if agent.plan_tool:
        rules[agent.plan_tool] = allow
    return rules


def _insert_denied(
    document: JsonValue, path: tuple[str, ...], rules: Mapping[str, str], deny: str, *, allowed: bool = False
) -> JsonValue:
    """The names ``rules`` denies (``allowed``: the names it allows), appended once each, in order, to the list at
    ``path`` in ``document``."""
    if not isinstance(document, dict):
        raise ValueError("toolRules needs an object template")
    root: dict[str, JsonValue] = dict(document)
    node = root
    for key in path[:-1]:
        child = node.get(key)
        child = dict(child) if isinstance(child, dict) else {}
        node[key] = child
        node = child
    listed = node.get(path[-1])
    names: list[JsonValue] = list(listed) if isinstance(listed, list) else []
    names.extend(name for name, value in rules.items() if (value != deny) is allowed and name not in names)
    node[path[-1]] = names
    return root


def _insert(document: JsonValue, path: tuple[str, ...], rules: Mapping[str, str], deny: str) -> JsonValue:
    """The rules at ``path`` in ``document``. An allowed name the template already rules keeps the template's
    (finer) entry, such as a list of skills it denies; a denied name is denied whatever the template says."""
    if not isinstance(document, dict):
        raise ValueError("toolRules needs an object template")
    root: dict[str, JsonValue] = dict(document)
    node = root
    for key in path:
        child = node.get(key)
        child = dict(child) if isinstance(child, dict) else {}
        node[key] = child
        node = child
    for name, value in rules.items():
        if name in node and value != deny:
            continue
        node[name] = value
    return root


def session_policy(agent: AcpAgentProfile, profile: EngineProfile, endpoints: GatewayEndpoints) -> dict[str, JsonValue]:
    permitted = allowed_native(profile)
    return {
        "agent": agent.id,
        "mode": profile.mode.value,
        "allowed": sorted(tool.value for tool in permitted),
        "nativeNames": {tool.value: list(names) for tool, names in agent.native_tools.items()},
        "planTool": agent.plan_tool,
        "gatewayPrefix": agent.gateway_tool_prefix,
        "mcpServer": agent.mcp_server,
        "toolsUrl": endpoints.tools_mcp_url,
        "toolsPlaceholder": endpoints.tools_placeholder,
        "callIdNameSeparator": agent.call_id_name_separator,
        "usagePath": list(agent.usage_path) if agent.usage_path is not None else None,
        "callUsagePath": list(agent.call_usage_path) if agent.call_usage_path is not None else None,
        "errorReasons": dict(agent.error_reasons),
        "commandResult": agent.command_result,
        "outputMayBeLost": agent.output_may_be_lost,
        "toolNamePath": list(agent.tool_name_path) if agent.tool_name_path is not None else None,
        "clientFsRead": agent.client_fs,
        "clientFsWrite": agent.client_fs and profile.mode is EngineMode.BUILD,
    }


def render_launch(
    agent: AcpAgentProfile, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str
) -> EngineLaunch:
    if profile.engine != agent.engine:
        raise ValueError(f"profile {agent.id} cannot launch {profile.engine.ref}")
    if endpoints.model_api is not agent.model_api:
        raise ValueError(f"{agent.name} speaks {agent.model_api}, not {endpoints.model_api}")
    values = placeholders(agent, profile, endpoints, skills_dir)
    env: dict[str, str] = {"HOME": "/tmp", **agent.env}
    gateway = agent.gateway_env
    env[gateway["modelToken"]] = endpoints.token_placeholder
    if "toolsToken" in gateway:
        env[gateway["toolsToken"]] = endpoints.tools_placeholder
    if "modelBaseUrl" in gateway:
        env[gateway["modelBaseUrl"]] = endpoints.model_base_url
    if "model" in gateway:
        env[gateway["model"]] = ENGINE_MODEL_ID
    if "skillsDir" in gateway:
        env[gateway["skillsDir"]] = skills_dir
    files: dict[str, bytes] = {}
    for config in agent.configs:
        rendered = render_template(config.template, values)
        if config.tool_rules is not None:
            rules, deny = tool_rules(agent, profile), agent.tool_rule_values[1]
            if config.tool_rules_form == "map":
                rendered = _insert(rendered, config.tool_rules, rules, deny)
            else:
                allowed = config.tool_rules_form == "allowed"
                rendered = _insert_denied(rendered, config.tool_rules, rules, deny, allowed=allowed)
        text = rendered if isinstance(rendered, str) else json.dumps(rendered, separators=(",", ":"))
        if config.env is not None:
            env[config.env] = text
        elif config.path is not None:
            files[config.path] = text.encode()
    policy = session_policy(agent, profile, endpoints)
    if agent.mcp_meta is not None:
        policy["mcpMeta"] = render_template(agent.mcp_meta, values)
    files[SESSION_POLICY_PATH] = json.dumps(policy, sort_keys=True).encode()
    return EngineLaunch(
        engine=profile.engine,
        argv=(agent.binary, *agent.argv),
        env=env,
        files=files,
        workdir=WORKDIR,
        port=None,
    )


__all__ = [
    "SESSION_POLICY_PATH",
    "UNSUPPORTED",
    "allowed_native",
    "placeholders",
    "render_launch",
    "render_template",
    "session_policy",
    "tool_rules",
]
