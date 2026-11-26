"""ACP-R*: ACP agent profiles as signed release data, their launch, and how the release installs them."""

from __future__ import annotations

import base64
import dataclasses
import json
from importlib import resources
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from harness_core.access.errors import VendorRulesInvalid
from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.budget.run_budget import NATIVE_STEP_HEADROOM
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    GatewayEndpoints,
    is_acp_kind,
)
from harness_core.testing.engines.contract import ENDPOINTS
from harness_engines import release
from harness_engines.acp.adapter import AcpAdapter
from harness_engines.acp.launch import (
    DEFAULT_STEPS,
    SESSION_POLICY_PATH,
    render_launch,
    render_template,
)
from harness_engines.acp.profiles import (
    AcpProfileInvalid,
    McpStatus,
    load_acp_agents,
    parse_acp_agents,
)
from harness_engines.acp.registry import (
    AGENTS_RESOURCE,
    AGENTS_SIGNATURE_RESOURCE,
    packaged_acp_agents,
)
from harness_engines.acp.testing.profile import fake_document, fake_entry

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue
    from harness_engines.acp.profiles import AcpAgentProfile

ACCESS = AccessRouteRef(route_id="route-1", digest="d1")
SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()
TOKEN = "pxh-model-placeholder"
TOOLS = "pxh-tools-placeholder"


def _opencode() -> AcpAgentProfile:
    return next(p for p in parse_acp_agents(SHIPPED) if p.id == "opencode")


def _signed(document: bytes) -> tuple[bytes, dict[str, bytes]]:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    signature = json.dumps(
        {"alg": "Ed25519", "keyId": "test-key", "sig": base64.b64encode(key.sign(document)).decode()}
    ).encode()
    return signature, {"test-key": public}


def _profile(agent: AcpAgentProfile, mode: EngineMode = EngineMode.BUILD) -> EngineProfile:
    return EngineProfile(engine=agent.engine, mode=mode, access=ACCESS)


def _endpoints(api: ModelApi = ModelApi.CHAT_COMPLETIONS) -> GatewayEndpoints:
    return dataclasses.replace(ENDPOINTS, model_api=api, token_placeholder=TOKEN, tools_token_placeholder=TOOLS)


class TestTheShippedProfiles:
    def test_every_acp_engine_in_the_allowlist_has_its_profile_and_back(self) -> None:
        profiles = {p.engine: p for p in parse_acp_agents(SHIPPED)}
        acp = [r for r in release.release_allowlist() if is_acp_kind(r.version.kind)]
        assert {r.version for r in acp} == set(profiles)
        for entry in acp:
            assert profiles[entry.version].image == entry.image
            assert entry.name == profiles[entry.version].name

    def test_the_opencode_profile_is_the_native_opencode_binary_and_image(self) -> None:
        agent = _opencode()
        native = next(r for r in release.release_allowlist() if r.version.kind == "opencode")
        assert (agent.kind, agent.agent_version, agent.binary_digest, agent.image) == (
            "acp-opencode",
            native.version.version,
            native.version.binary_digest,
            native.image,
        )
        assert agent.binary == release.BINARY_PATHS["opencode"]
        assert agent.model_api is ModelApi.CHAT_COMPLETIONS
        assert agent.resume == "load" and agent.state_paths == ("/state/opencode",)

    def test_the_document_ships_unsigned_so_nothing_is_installed_without_the_dev_override(self) -> None:
        assert resources.files("harness_engines").joinpath(AGENTS_SIGNATURE_RESOURCE).is_file() is False
        assert packaged_acp_agents(unsigned_ok=False) == ()
        assert [p.id for p in packaged_acp_agents(unsigned_ok=True)] == ["opencode", "gemini", "goose", "qwen", "dsh"]


class TestSignature:
    def test_a_signed_document_loads(self) -> None:
        signature, keys = _signed(SHIPPED)
        assert [p.id for p in load_acp_agents(SHIPPED, signature, keys)] == ["opencode", "gemini", "goose", "qwen", "dsh"]

    @pytest.mark.parametrize("change", ["unsigned", "tampered", "unknown_key", "missing"])
    def test_anything_but_a_valid_signature_installs_nothing(self, change: str) -> None:
        signature, keys = _signed(SHIPPED)
        document: bytes | None = SHIPPED
        if change == "unsigned":
            signature = None  # type: ignore[assignment]
        elif change == "tampered":
            document = SHIPPED.replace(b'"opencode (ACP)"', b'"opencode (evil)"')
        elif change == "unknown_key":
            keys = {"other": keys["test-key"]}
        else:
            document = None
        with pytest.raises(VendorRulesInvalid):
            load_acp_agents(document, signature, keys)

    def test_the_dev_override_reads_an_unsigned_document_but_never_a_bad_signature(self) -> None:
        assert load_acp_agents(SHIPPED, None, {}, unsigned_ok=True)
        signature, _ = _signed(SHIPPED)
        with pytest.raises(VendorRulesInvalid):
            load_acp_agents(SHIPPED, signature, {}, unsigned_ok=True)


class TestParse:
    @pytest.mark.parametrize(
        "change",
        [
            {"id": "Bad Id"},
            {"id": "x" * 40},
            {"modelApi": "gemini"},
            {"binary": {"path": "relative/bin", "digest": "sha256:" + "a" * 64}},
            {"binary": {"path": "/bin/x", "digest": "md5:1"}},
            {"gatewayEnv": {"modelToken": "T", "secret": "S"}},
            {"gatewayEnv": {"toolsToken": "T"}},
            {"configs": [{"path": "/etc/x.json", "template": {}}]},
            {"configs": [{"path": "/state/acp/../x.json", "template": {}}]},
            {"configs": [{"env": "X", "path": "/state/acp/x.json", "template": {}}]},
            {"configs": [{"env": "lower", "template": {}}]},
            {"nativeTools": {"teleport": ["x"]}},
            {"mcp": {"server": "tools", "toolName": "{tool}_{server}"}},
            {"mcp": {"status": {"api": "codex", "port": 4096}}},
            {"mcp": {"status": {"api": "opencode", "port": 0}}},
            {"mcp": {"status": {"api": "opencode", "port": True}}},
            {"mcp": {"status": "opencode"}},
            {"resume": {"mode": "load", "paths": []}},
            {"resume": {"mode": "load", "paths": ["/workspace/x"]}},
            {"resume": {"mode": "magic"}},
            {"instructionsFile": "/workspace/AGENTS.md"},
            {"protocolVersion": 2},
            {"env": {"FAKE_ACP_TOKEN": "a real token"}},
            {"env": {"FAKE_ACP_CONFIG": "{}"}},
            {"sessionConfig": {"mode": 3}},
            {"reservedToolNames": "ask_user"},
            {"reservedToolNames": [""]},
        ],
    )
    def test_a_profile_outside_the_schema_is_refused(self, change: dict[str, JsonValue]) -> None:
        with pytest.raises(AcpProfileInvalid):
            parse_acp_agents(fake_document(fake_entry(**change)))

    def test_two_agents_with_one_id_are_refused(self) -> None:
        with pytest.raises(AcpProfileInvalid):
            parse_acp_agents(fake_document(fake_entry(), fake_entry()))

    @pytest.mark.parametrize("document", [b"not json", b"[]", b'{"schema": "other", "agents": []}'])
    def test_a_document_that_is_not_this_schema_is_refused(self, document: bytes) -> None:
        with pytest.raises(AcpProfileInvalid):
            parse_acp_agents(document)


class TestLaunch:
    def test_the_opencode_launch_reaches_only_the_gateways_by_their_placeholders(self) -> None:
        agent = _opencode()
        launch = render_launch(agent, _profile(agent), _endpoints(), "/skills")
        # Its own server on a known loopback port: the driver asks it whether the gateway is connected (E2E3-F6).
        assert launch.argv == ("/usr/local/bin/opencode", "acp", "--pure", "--cwd", "/workspace", "--port", "4096")
        assert agent.mcp_status == McpStatus(api="opencode", port=4096)
        assert launch.env["HARNESS_GATEWAY_TOKEN"] == TOKEN
        assert launch.env["HARNESS_TOOLS_TOKEN"] == TOOLS
        assert launch.env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
        config = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])
        assert config["provider"]["gateway"]["options"] == {
            "baseURL": ENDPOINTS.model_base_url,
            "apiKey": "{env:HARNESS_GATEWAY_TOKEN}",
        }
        assert list(config["provider"]["gateway"]["models"]) == ["engine-model"]
        assert config["model"] == "gateway/engine-model"
        assert config["agent"]["harness"]["steps"] == DEFAULT_STEPS + NATIVE_STEP_HEADROOM
        assert config["instructions"] == ["/usr/share/pxh-engine/RUNTIME.md", "/state/acp/INSTRUCTIONS.md"]
        assert config["permission"]["external_directory"]["/skills/*"] == "allow"
        assert "mcp" not in config
        everything = json.dumps({**launch.env, **{k: v.decode() for k, v in launch.files.items()}})
        assert "${" not in everything
        assert TOKEN not in launch.env["OPENCODE_CONFIG_CONTENT"]

    @pytest.mark.parametrize(
        ("mode", "allowed", "denied"),
        [
            (EngineMode.BUILD, {"read", "glob", "grep", "edit", "bash", "todowrite"}, {"task"}),
            (EngineMode.PLAN, {"read", "glob", "grep", "todowrite"}, {"edit", "bash", "task"}),
            (EngineMode.REVIEW, {"read", "glob", "grep", "todowrite"}, {"edit", "bash", "task"}),
        ],
    )
    def test_the_agents_permission_rules_follow_the_runs_mode(
        self, mode: EngineMode, allowed: set[str], denied: set[str]
    ) -> None:
        agent = _opencode()
        rules = json.loads(render_launch(agent, _profile(agent, mode), _endpoints(), "/skills").env["OPENCODE_CONFIG_CONTENT"])[
            "permission"
        ]
        assert {name for name in allowed if rules[name] == "allow"} == allowed
        assert {name for name in denied if rules[name] == "deny"} == denied
        # The template's finer rule for an allowed tool stays: opencode's own customising skill is never offered.
        assert rules["skill"] == {"*": "allow", "customize-opencode": "deny"}
        assert list(rules)[:2] == ["*", "tools_*"]

    def test_the_session_policy_travels_in_the_launch(self) -> None:
        agent = _opencode()
        policy = json.loads(render_launch(agent, _profile(agent, EngineMode.PLAN), _endpoints(), "/skills").files[SESSION_POLICY_PATH])
        assert policy["allowed"] == ["read", "search", "skill"]
        assert policy["gatewayPrefix"] == "tools_"
        assert policy["toolsPlaceholder"] == TOOLS
        assert policy["clientFsWrite"] is False

    def test_another_engine_or_model_api_is_refused(self) -> None:
        agent = _opencode()
        with pytest.raises(ValueError):
            render_launch(agent, _profile(agent), _endpoints(ModelApi.RESPONSES), "/skills")
        other = dataclasses.replace(_profile(agent), engine=dataclasses.replace(agent.engine, version="9"))
        with pytest.raises(ValueError):
            render_launch(agent, other, _endpoints(), "/skills")

    def test_an_unknown_placeholder_is_refused(self) -> None:
        with pytest.raises(ValueError):
            render_template({"x": "${password}"}, {})
        assert render_template({"${a}": ["${b}", "n=${b}"]}, {"a": "k", "b": 2}) == {"k": [2, "n=2"]}

    def test_capabilities_say_what_acp_cannot_carry(self) -> None:
        capabilities = AcpAdapter(_opencode()).capabilities()
        assert (capabilities.can_steer, capabilities.can_compact, capabilities.sub_agents) == (False, False, False)
        assert capabilities.model_apis == frozenset({ModelApi.CHAT_COMPLETIONS})


class TestRelease:
    def test_uninstalled_acp_engines_are_named_and_have_no_binary_pin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(release, "acp_profiles", lambda env=None: ())
        assert release.uninstalled_engines() == ("acp-dsh@0.2.2-rc.1", "acp-gemini@0.63.0", "acp-goose@1.57.0", "acp-opencode@1.18.34", "acp-qwen@0.26.5")
        assert release.installed_acp() == {}
        pins = release.release_binary_pins()
        assert all(not is_acp_kind(p.engine.kind) for pinned in pins.values() for p in pinned)
        kinds = {a.kind for a in release.release_adapters()}
        assert kinds == {"opencode", "codex", "claude_code"}

    def test_an_installed_profile_brings_its_adapter_pin_and_model_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = _opencode()
        monkeypatch.setattr(release, "acp_profiles", lambda env=None: (agent,))
        assert release.uninstalled_engines() == ("acp-dsh@0.2.2-rc.1", "acp-gemini@0.63.0", "acp-goose@1.57.0", "acp-qwen@0.26.5")
        assert release.installed_acp() == {agent.engine: agent}
        registry = release.release_registry()
        adapter = registry.adapter_for(agent.engine)
        assert isinstance(adapter, AcpAdapter) and adapter.kind == "acp-opencode"
        pins = release.release_binary_pins()[agent.image]
        assert any(p.engine == agent.engine and p.path == agent.binary for p in pins)
        assert release.release_model_apis()["acp-opencode"] == frozenset({ModelApi.CHAT_COMPLETIONS})

    def test_a_profile_pinned_to_another_image_is_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = dataclasses.replace(_opencode(), image="pxh-elsewhere:1")
        monkeypatch.setattr(release, "acp_profiles", lambda env=None: (agent,))
        assert release.installed_acp() == {}
        assert release.uninstalled_engines() == ("acp-dsh@0.2.2-rc.1", "acp-gemini@0.63.0", "acp-goose@1.57.0", "acp-opencode@1.18.34", "acp-qwen@0.26.5")
