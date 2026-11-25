"""C20.26 / G-25 / GA-14: the managed, read-only policy hook in each engine that runs hooks, and the policy each
engine without one is held to.

Claude Code and Codex run ``PreToolUse`` hooks: the controller renders one that asks the run's policy through the
Tool Gateway (``ph policy check --hook``) into the configuration file the engine obeys, which is root's and read-only
(SEC1; the docker lane in ``test_ga14_policy_hook_docker`` tries to edit it as the sandbox user). opencode has no hook
PipesHub can manage (its plug-ins run in-process with full privileges, and ``OPENCODE_PURE`` keeps them off): its
permission rules, rendered from the same profile, are what it checks before every tool call, so they must refuse
what the native-call policy refuses. The ACP driver asks the native-call policy itself.
"""

from __future__ import annotations

import json
import tomllib

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.jsonview import as_object
from harness_core.engines.native_policy import (
    HOOK_TIMEOUT_S,
    NativeCall,
    NativeCallPolicy,
    policy_hook_command,
)
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
    RunLimits,
)
from harness_engines.claude_code import launch as claude
from harness_engines.codex import launch as codex
from harness_engines.codex.adapter import loaded_policy_hook
from harness_engines.opencode import launch as opencode
from harness_engines.opencode.permissions import Rule, evaluate, flatten
from harness_engines.pipeshub import POLICY_HOOK_PATH

ACCESS = AccessRouteRef("route-1", "d1")


def _endpoints(api: ModelApi, *, hook: str | None = POLICY_HOOK_PATH) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url="http://model-gateway:8097/v1",
        model_api=api,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder="pxh-model-placeholder-0123",
        tools_token_placeholder="pxh-tools-placeholder-4567",
        policy_hook=hook,
    )


def _profile(kind: str, mode: EngineMode = EngineMode.BUILD) -> EngineProfile:
    return EngineProfile(
        engine=EngineVersion(kind, "1.0.0", "sha256:" + "d" * 64), mode=mode, access=ACCESS, limits=RunLimits(max_steps=30)
    )


def test_ga14_claude_code_obeys_the_managed_hook_from_its_read_only_settings() -> None:
    launch = claude.render_launch(_profile("claude_code"), _endpoints(ModelApi.ANTHROPIC_MESSAGES), "/skills")
    settings = json.loads(launch.files[claude.SETTINGS_PATH])
    ((group,),) = [settings["hooks"]["PreToolUse"]]
    assert set(group["matcher"].split("|")) == set(claude.POLICY_HOOK_TOOLS)
    assert group["hooks"] == [{"type": "command", "command": policy_hook_command(POLICY_HOOK_PATH), "timeout": HOOK_TIMEOUT_S}]
    assert settings["disableAllHooks"] is False
    # Every native tool that writes, runs or reaches out is covered; the gateway checks its own tools.
    for tool in (NativeTool.SHELL, NativeTool.EDIT):
        assert set(claude.NATIVE_TOOLS[tool]) <= set(claude.POLICY_HOOK_TOOLS)
    # Only the settings file and the empty, read-only user scope are read: nothing else can add or switch off a hook.
    assert launch.argv[launch.argv.index("--setting-sources") + 1] == "user"
    assert launch.argv[launch.argv.index("--settings") + 1] == claude.SETTINGS_PATH
    assert launch.files[claude.USER_SETTINGS_PATH] == b"{}"
    assert all(path.startswith(f"{claude.CONFIG_DIR}/") for path in launch.files)


def test_ga14_without_a_hook_program_claude_code_has_no_hook() -> None:
    launch = claude.render_launch(_profile("claude_code"), _endpoints(ModelApi.ANTHROPIC_MESSAGES, hook=None), "/skills")
    assert "hooks" not in json.loads(launch.files[claude.SETTINGS_PATH])


def test_ga14_codex_runs_the_managed_hook_trusted_from_its_read_only_config() -> None:
    launch = codex.render_launch(_profile("codex"), _endpoints(ModelApi.RESPONSES), "/skills")
    config = tomllib.loads(launch.files[codex.CONFIG_PATH].decode())
    assert config["features"]["hooks"] is True
    assert config["hooks"]["PreToolUse"] == [
        {
            "matcher": codex.POLICY_HOOK_MATCHER,
            "hooks": [{"type": "command", "command": policy_hook_command(POLICY_HOOK_PATH), "timeout": HOOK_TIMEOUT_S}],
        }
    ]
    # Codex skips an untrusted hook without a word: the config trusts exactly this one by its pinned hash.
    assert config["hooks"]["state"] == {
        codex.POLICY_HOOK_KEY: {"trusted_hash": codex.TRUSTED_HOOKS[policy_hook_command(POLICY_HOOK_PATH)]}
    }
    assert codex.POLICY_HOOK_KEY.startswith(f"{codex.CONFIG_PATH}:")
    assert codex.renders_policy_hook(launch)


def test_ga14_codex_without_a_hook_program_turns_hooks_off() -> None:
    launch = codex.render_launch(_profile("codex"), _endpoints(ModelApi.RESPONSES, hook=None), "/skills")
    config = tomllib.loads(launch.files[codex.CONFIG_PATH].decode())
    assert config["features"]["hooks"] is False and "hooks" not in config
    assert not codex.renders_policy_hook(launch)


def test_ga14_codex_refuses_to_render_a_hook_it_would_not_trust() -> None:
    with pytest.raises(ValueError, match="no trusted hash"):
        codex.render_launch(_profile("codex"), _endpoints(ModelApi.RESPONSES, hook="/elsewhere/ph.py"), "/skills")


def _listed(**hook: object) -> dict[str, object]:
    found = {"key": codex.POLICY_HOOK_KEY, "enabled": True, "trustStatus": "trusted", **hook}
    return {"data": [{"cwd": "/workspace", "hooks": [found], "warnings": [], "errors": []}]}


@pytest.mark.parametrize(
    ("listed", "loaded"),
    [
        (_listed(), True),
        (_listed(trustStatus="managed"), True),
        # What Codex reports for a hook it skips: the adapter refuses to start it (fail closed).
        (_listed(trustStatus="untrusted"), False),
        (_listed(enabled=False), False),
        (_listed(key="/state/codex/config.toml:pre_tool_use:0:1"), False),
        ({"data": []}, False),
        ({}, False),
    ],
)
def test_ga14_codex_must_report_the_managed_hook_loaded_and_trusted(listed: dict[str, object], loaded: bool) -> None:
    assert loaded_policy_hook(listed) is loaded  # type: ignore[arg-type]


def _opencode_rules(mode: EngineMode) -> list[Rule]:
    launch = opencode.render_launch(_profile("opencode", mode), _endpoints(ModelApi.CHAT_COMPLETIONS, hook=None), "/skills")
    config = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])
    return [Rule("*", "*", "allow"), *flatten(as_object(config.get("permission")))]


def _opencode_allows(rules: list[Rule], call: NativeCall) -> bool:
    keys = {NativeTool.SHELL: "bash", NativeTool.EDIT: "edit", NativeTool.READ: "read", NativeTool.WEB: "webfetch"}
    if evaluate(rules, keys[call.tool]) != "allow":
        return False
    # opencode asks ``external_directory`` for any path outside its working folder, the workspace.
    return all(
        evaluate(rules, "external_directory", path) == "allow"
        for path in call.paths
        if not path.startswith(f"{opencode.WORKDIR}/")
    )


@pytest.mark.parametrize("mode", [EngineMode.BUILD, EngineMode.PLAN, EngineMode.REVIEW])
@pytest.mark.parametrize(
    "call",
    [
        NativeCall(NativeTool.SHELL, (), "bash"),
        NativeCall(NativeTool.EDIT, ("/workspace/src/a.py",), "edit"),
        NativeCall(NativeTool.EDIT, ("/tmp/outside.md",), "write"),
        NativeCall(NativeTool.EDIT, ("/pipeshub/session/ph.py",), "edit"),
        NativeCall(NativeTool.READ, (), "read"),
        NativeCall(NativeTool.WEB, (), "webfetch"),
    ],
)
def test_ga14_opencode_without_a_hook_refuses_what_the_policy_refuses(mode: EngineMode, call: NativeCall) -> None:
    """An engine without a managed hook is held to the same policy by its own rendered rules. Where opencode's rules
    let it reach outside the workspace (the folders it reads: /pipeshub, /skills), the refusal of an edit is the
    sandbox's: those are read-only mounts. (opencode reads no project configuration, OPENCODE_DISABLE_PROJECT_CONFIG,
    so an edit of a workspace .opencode/ is inert there; the workspace sync drops it too.)"""
    policy = NativeCallPolicy.for_profile(_profile("opencode", mode))
    refused_by_mount = [p for p in call.paths if _read_only_mount(p)]
    if refused_by_mount:
        assert not policy.verdict(call).allowed
        return
    assert _opencode_allows(_opencode_rules(mode), call) is policy.verdict(call).allowed


def _read_only_mount(path: str) -> bool:
    from harness_core.skills.materializer import skills_mount
    from harness_engines.pipeshub import pipeshub_tool_folder

    mounts = (pipeshub_tool_folder().mount(), skills_mount("/skills"))
    return any(m.read_only and path.startswith(f"{m.target}/") for m in mounts)


def test_ga14_opencode_keeps_its_plug_ins_and_project_config_off() -> None:
    for name in ("OPENCODE_PURE", "OPENCODE_DISABLE_DEFAULT_PLUGINS", "OPENCODE_DISABLE_PROJECT_CONFIG"):
        assert opencode.LOCKED_ENV[name] == "1"


# R10-CC1-H1: the engine-candidate gate reads the hash a Codex binary gives the managed
# hook; the parse is pinned here, the comparison against TRUSTED_HOOKS on the binary in the Codex gate lane.
@pytest.mark.parametrize(
    ("listed", "found"),
    [
        (_listed(currentHash="sha256:" + "1" * 64), "sha256:" + "1" * 64),
        (_listed(trustStatus="untrusted", currentHash="sha256:" + "2" * 64), "sha256:" + "2" * 64),
        (_listed(), None),
        (_listed(key="/state/codex/config.toml:pre_tool_use:0:1", currentHash="sha256:" + "3" * 64), None),
        ({}, None),
    ],
)
def test_r10_cc1_h1_the_hash_codex_lists_for_the_managed_hook_is_read(listed: dict[str, object], found: str | None) -> None:
    from harness_engines.codex.adapter import listed_policy_hook_hash

    assert listed_policy_hook_hash(listed) == found  # type: ignore[arg-type]
