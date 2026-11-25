"""GA-23 (G-43, C20.44; OWD-17 as amended 2026-10-26): vendor subscription logins are refused, but one.

A person's own Claude account inside unmodified Claude Code, in their own sandbox, is the single exception: it is
admin-gated and off by default. Every other vendor login (ChatGPT/Codex, Gemini, any other engine or vendor) is
refused at every layer a login would need:

1. the signed vendor rules decide it refused, even for an org that opted into everything;
2. no engine but Claude Code can run a login in its sandbox (the controller signs in only a ``SignsIn`` adapter);
3. the signed sign-in host list names Claude's hosts only;
4. an engine sandbox's egress reaches the two gateways and nothing else, so a vendor's login and token hosts are
   refused (plain HTTP and ``CONNECT``); a signed-in Claude sandbox reaches only Claude's signed hosts;
5. OWD-8: no row lets a key go straight to a vendor (``vendor_injected`` decides prohibited).
"""

from __future__ import annotations

import itertools

import pytest

from harness_core.access.errors import (
    OfferingNotPermitted,
    OrgPolicyRefused,
    VendorPending,
    VendorProhibited,
    VendorRuleMissing,
)
from harness_core.access.types import AccountKind, RouteKind
from harness_core.access.vendor_rules import (
    DEFAULT_ORG_POLICY,
    GATEWAY_BYPASS_ROUTES,
    AccessCandidate,
    OrgAccessPolicy,
    decide,
)
from harness_core.contracts.generated.enums import OFFERINGS
from harness_core.engines.controller import EngineControllerConfig, engine_sandbox_spec
from harness_core.engines.signin import SignsIn
from harness_core.sandbox.egress import (
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.admission import binding_of
from harness_core.sandbox.types import MountSpec, RuntimeMode
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_engines.acp.adapter import AcpAdapter
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.vendor_hosts import CLAUDE_PLAN_ROW, release_sign_in_hosts
from harness_engines.vendor_rules import release_vendor_rules

ENGINES = ("claude_code", "codex", "opencode", "gemini_cli", "loop", "acp-opencode", "acp-gemini")
PROVIDERS = ("anthropic", "openai", "gemini", "azureopenai", "openaicompatible", "bedrock", "vertexai", "foundry")
LOGIN_KINDS = (AccountKind.USER_PLAN_SANDBOX_LOGIN, AccountKind.USER_PLAN_INJECTED)
LOGIN_ROUTES = (RouteKind.VENDOR_SANDBOX_LOGIN, RouteKind.VENDOR_INJECTED)
EVERYTHING_ALLOWED = OrgAccessPolicy(consumer_plans=True)
CLAUDE_OWN_LOGIN = AccessCandidate("anthropic", AccountKind.USER_PLAN_SANDBOX_LOGIN, RouteKind.VENDOR_SANDBOX_LOGIN, "claude_code")
REFUSED = (VendorProhibited, VendorPending, VendorRuleMissing, OfferingNotPermitted, OrgPolicyRefused)
# Where each vendor's subscription login, token exchange or plan-backed API lives.
VENDOR_LOGIN_HOSTS = (
    "auth.openai.com",
    "chatgpt.com",
    "api.openai.com",
    "accounts.google.com",
    "oauth2.googleapis.com",
    "cloudcode-pa.googleapis.com",
    "generativelanguage.googleapis.com",
    "claude.ai",
    "console.anthropic.com",
    "platform.claude.com",
    "api.anthropic.com",
)


def _candidates() -> list[AccessCandidate]:
    return [
        AccessCandidate(provider, kind, route, engine)
        for provider, kind, route, engine in itertools.product(PROVIDERS, LOGIN_KINDS, LOGIN_ROUTES, ENGINES)
    ]


class TestGA23VendorRules:
    @pytest.mark.parametrize("offering", sorted(OFFERINGS))
    def test_every_subscription_login_but_claude_codes_own_is_refused_even_fully_opted_in(self, offering: str) -> None:
        rules = release_vendor_rules()
        allowed: list[AccessCandidate] = []
        for candidate in _candidates():
            try:
                _ = decide(rules, candidate, offering, EVERYTHING_ALLOWED)  # pyright: ignore[reportArgumentType]
            except REFUSED:
                continue
            allowed.append(candidate)
        assert set(allowed) <= {CLAUDE_OWN_LOGIN}, allowed

    @pytest.mark.parametrize(
        "candidate",
        [
            AccessCandidate("openai", AccountKind.USER_PLAN_SANDBOX_LOGIN, RouteKind.VENDOR_SANDBOX_LOGIN, "codex"),
            AccessCandidate("openai", AccountKind.USER_PLAN_INJECTED, RouteKind.VENDOR_SANDBOX_LOGIN, "codex"),
            AccessCandidate("openai", AccountKind.USER_PLAN_INJECTED, RouteKind.VENDOR_INJECTED, "codex"),
            AccessCandidate("gemini", AccountKind.USER_PLAN_SANDBOX_LOGIN, RouteKind.VENDOR_SANDBOX_LOGIN, "gemini_cli"),
            AccessCandidate("gemini", AccountKind.USER_PLAN_SANDBOX_LOGIN, RouteKind.VENDOR_SANDBOX_LOGIN, "opencode"),
            AccessCandidate("anthropic", AccountKind.USER_PLAN_SANDBOX_LOGIN, RouteKind.VENDOR_SANDBOX_LOGIN, "opencode"),
            AccessCandidate("anthropic", AccountKind.USER_PLAN_INJECTED, RouteKind.VENDOR_INJECTED, "claude_code"),
        ],
        ids=["codex-login", "codex-chatgpt-tokens", "chatgpt-injected", "gemini-cli", "gemini-other", "claude-in-opencode", "claude-injected"],
    )
    def test_the_named_logins_are_refused(self, candidate: AccessCandidate) -> None:
        with pytest.raises(REFUSED):
            _ = decide(release_vendor_rules(), candidate, "oss", EVERYTHING_ALLOWED)

    def test_claude_codes_own_login_is_off_until_the_admin_opts_in(self) -> None:
        with pytest.raises(OrgPolicyRefused):
            _ = decide(release_vendor_rules(), CLAUDE_OWN_LOGIN, "oss", DEFAULT_ORG_POLICY)
        assert decide(release_vendor_rules(), CLAUDE_OWN_LOGIN, "oss", EVERYTHING_ALLOWED).id == CLAUDE_PLAN_ROW
        switched_off = OrgAccessPolicy(consumer_plans=True, disabled_rules=frozenset({CLAUDE_PLAN_ROW}))
        with pytest.raises(OrgPolicyRefused):
            _ = decide(release_vendor_rules(), CLAUDE_OWN_LOGIN, "oss", switched_off)

    def test_owd8_no_row_sends_a_key_straight_to_a_vendor(self) -> None:
        bypassing = [rule for rule in release_vendor_rules().rules if rule.route in GATEWAY_BYPASS_ROUTES]
        assert bypassing and all(rule.status == "prohibited" for rule in bypassing)


def test_ga23_only_claude_code_runs_a_login_in_its_sandbox() -> None:
    assert issubclass(ClaudeCodeAdapter, SignsIn)
    for adapter in (CodexAdapter, OpencodeAdapter, AcpAdapter):
        assert not issubclass(adapter, SignsIn), adapter.__name__


def test_ga23_the_signed_sign_in_hosts_are_claudes_only() -> None:
    hosts = release_sign_in_hosts()
    assert hosts is not None and set(hosts.sandbox) == {"api.anthropic.com", "platform.claude.com"}
    for row in ("openai-codex-app-server-login", "gemini-login-hosted-gemini-cli", "anthropic-claude-plan-other-harness"):
        assert release_sign_in_hosts(row) is None


CONFIG = EngineControllerConfig(images={})


def _proxy() -> EgressProxy:
    clock = ManualClock()
    public = dict.fromkeys(VENDOR_LOGIN_HOSTS, ("93.184.216.34",))
    return EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(b"ga23", clock),
        resolver=StaticResolver(public),
        upstreams={"model-gateway": "http://127.0.0.1:1", "tool-gateway": "http://127.0.0.1:1"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(connect_tunnels=True),
    )


async def _bound(proxy: EgressProxy, tunnels: tuple[str, ...]):  # noqa: ANN202 - EgressBinding
    spec = engine_sandbox_spec(
        CONFIG,
        image="pxh-codex-tools:0.160.0-ph7",
        mode=RuntimeMode.DOCKER_HARDENED,
        workspace=MountSpec(CONFIG.workspace_root),
        labels={"runId": "run-ga23", "attemptId": "a1"},
        placeholders=("pxh-model-ga23-0123456789", "pxh-tools-ga23-0123456789"),
        tools_upstream="http://worker:8098/x",
        tunnels=tunnels,
    )
    binding = binding_of("sbx-ga23", SubnetPool().allocate(set()), spec)  # pyright: ignore[reportArgumentType]
    await proxy.bindings.register(binding)
    return binding


async def _reasons(proxy: EgressProxy, binding: object, host: str) -> tuple[str, str]:
    local, peer = binding.proxy_address, binding.sandbox_address  # pyright: ignore[reportAttributeAccessIssue]
    outcomes: list[str] = []
    for request, call in (
        (ProxyRequest("GET", f"http://{host}/oauth/authorize", (("Host", host),)), proxy.authorize),
        (ProxyRequest("CONNECT", f"{host}:443", (("Host", f"{host}:443"),)), proxy.authorize_tunnel),
    ):
        try:
            _ = await call(request, local=local, peer=peer)
        except EgressRefused as refused:
            outcomes.append(refused.reason.value)
        else:
            outcomes.append("allowed")
    return outcomes[0], outcomes[1]


@pytest.mark.parametrize("host", VENDOR_LOGIN_HOSTS)
async def test_ga23_an_engine_sandbox_reaches_no_vendor_login_host(host: str) -> None:
    proxy = _proxy()
    binding = await _bound(proxy, tunnels=())
    assert await _reasons(proxy, binding, host) == (
        EgressRefusalReason.DESTINATION_NOT_ALLOWED.value,
        EgressRefusalReason.DESTINATION_NOT_ALLOWED.value,
    )


async def test_ga23_a_signed_in_claude_sandbox_reaches_claudes_signed_hosts_only() -> None:
    hosts = release_sign_in_hosts()
    assert hosts is not None
    proxy = _proxy()
    binding = await _bound(proxy, tunnels=hosts.sandbox)
    for host in VENDOR_LOGIN_HOSTS:
        _, tunnel = await _reasons(proxy, binding, host)
        assert (tunnel == "allowed") is (host in hosts.sandbox), host
