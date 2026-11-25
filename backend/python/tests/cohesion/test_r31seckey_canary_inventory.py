"""R31-SECKEY cohesion: every kind of credential the code knows has a canary in the engine-sandbox lane's seed.

deployment/e2e/seckey.py plants a canary for each kind in deployment/e2e/seckey_inventory.py and looks for all of them
inside every engine's sandbox. A kind the inventory does not name would be planted nowhere, so a leak of it could
never be seen. These tests read the code's own enumerations, never a copy of them, so a new model provider, MCP sign-in
mode, web search provider, toolset sign-in or provider credential field fails here until the inventory names it:

* SKC-1 the LLM and embedding providers (``LLMProvider``, ``EmbeddingProvider``)
* SKC-2 every credential field a provider's configuration is read for (``app/utils/aimodels.py``)
* SKC-3 the MCP sign-in modes (``MCPAuthMode``)
* SKC-4 the web search providers (``PROVIDERS``); a keyless one is one of the free providers
* SKC-5 the toolset sign-ins (``AuthType`` and every kind a registered toolset offers); a kind no toolset offers is
  ``unused`` and one a toolset offers is not
* SKC-6 each kind that holds a credential gets a canary for each of its fields
* SKC-7 (R31SECKEY-FU-2) the platform's secrets the config store keeps: every credential and signing keyring of Node's
  ``configPaths.agentHarness`` and the secretKeys document, read from the store and one line each
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from app.agents.constants.toolset_constants import AuthType
from app.agents.mcp.models import MCPAuthMode
from app.utils.aimodels import EmbeddingProvider, LLMProvider
from app.web.search import FREE_PROVIDERS, PROVIDERS

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "deployment" / "e2e"))
import seckey_inventory as inv  # noqa: E402

AIMODELS = ROOT / "backend" / "python" / "app" / "utils" / "aimodels.py"
CREDENTIAL = re.compile(r"(key|secret|token|password|credential|serviceaccount)", re.IGNORECASE)
# A token count (``maxTokens``) names no credential.
COUNT = re.compile(r"Tokens$")
READ_FIELD = re.compile(r"""configuration(?:\.get\(|\[)\s*["']([A-Za-z_]+)["']""")


def test_skc_1_every_model_provider_has_a_canary() -> None:
    assert set(inv.LLM_KINDS) == {p.value for p in LLMProvider}
    assert set(inv.EMBEDDING_KINDS) == {p.value for p in EmbeddingProvider}


def test_skc_2_every_credential_field_a_provider_reads_is_planted() -> None:
    read = {name for name in READ_FIELD.findall(AIMODELS.read_text()) if CREDENTIAL.search(name) and not COUNT.search(name)}
    planted = {field for kind in (*inv.LLM_KINDS, *inv.EMBEDDING_KINDS) for field in inv.model_fields(kind)}
    assert read, "the reader of aimodels.py found no credential field: the pattern no longer matches the code"
    assert read <= planted, f"credential fields with no canary: {sorted(read - planted)}"


def test_skc_3_every_mcp_sign_in_mode_has_a_canary() -> None:
    assert set(inv.MCP_MODES) == {m.value for m in MCPAuthMode}


def test_skc_4_every_web_search_provider_has_a_canary() -> None:
    assert set(inv.SEARCH_PROVIDERS) == set(PROVIDERS)
    keyless = {kind for kind, (mode, _) in inv.SEARCH_PROVIDERS.items() if mode == inv.NONE}
    assert keyless <= set(FREE_PROVIDERS), f"named keyless but not a free provider: {sorted(keyless - set(FREE_PROVIDERS))}"


@pytest.fixture(scope="module")
def offered() -> set[str]:
    from app.agents.registry.toolset_registry import get_toolset_registry

    registry = get_toolset_registry()
    if not registry.get_all_toolsets():
        registry.auto_discover_toolsets()
    kinds = {str(a).upper() for name in registry.get_all_toolsets()
             for a in (registry.get_toolset_metadata(name) or {}).get("supported_auth_types") or []}
    assert kinds, "the toolset registry discovered no toolset"
    return kinds


def test_skc_5_every_toolset_sign_in_has_a_canary(offered: set[str]) -> None:
    assert set(inv.TOOLSET_AUTH) == {a.value for a in AuthType} | offered
    for kind, (mode, _) in inv.TOOLSET_AUTH.items():
        if mode == inv.UNUSED:
            assert kind not in offered, f"{kind} is offered by a toolset now: give it a canary"
        elif mode in (inv.LIVE, inv.STORED):
            assert kind in offered, f"{kind} is planted but no toolset offers it"


def test_skc_6_each_kind_with_a_credential_gets_a_canary_per_field() -> None:
    names = {c.name for c in inv.plan()} | set(inv.STACK_LIVE)
    for family, table in (("mcp", inv.MCP_MODES), ("search", inv.SEARCH_PROVIDERS), ("toolset", inv.TOOLSET_AUTH)):
        for kind, (mode, fields) in table.items():
            assert bool(fields) == (mode in (inv.LIVE, inv.STORED)), (family, kind, mode, fields)
            for field in fields:
                assert f"{family}.{kind}.{field}" in names, (family, kind, field)
    for family, kinds in (("llm", inv.LLM_KINDS), ("embedding", inv.EMBEDDING_KINDS)):
        for kind in kinds:
            assert all(f"{family}.{kind}.{f}" in names for f in inv.model_fields(kind)), (family, kind)


NODE_PATHS = ROOT / "backend" / "nodejs" / "apps" / "src" / "modules" / "configuration_manager" / "paths" / "paths.ts"
# Any credential- or keyring-named harness path, wherever the word falls in the name (R38-IDENT: the role credentials
# are ``roleCredentialScrape`` and the like, which a name ending in the word would miss).
_HARNESS_SECRET_PATH = re.compile(r"""(\w*(?:Credential|SigningKeys)\w*)\s*:\s*'(/services/agentHarness/[^']+)'""")


def test_skc_7_every_config_store_credential_and_signing_keyring_is_a_canary() -> None:
    node = NODE_PATHS.read_text()
    held = dict(_HARNESS_SECRET_PATH.findall(node))
    assert {"workerCredential", "attemptSigningKeys", "gatewaySigningKeys", "roleCredentialScrape",
            "roleCredentialEgressProxy", "roleCredentialSandboxBroker"} <= set(held), held
    assert {name: inv.CONFIG_SECRETS.get(name) for name in held} == held, "a config-store secret with no canary"
    assert inv.CONFIG_SECRETS["secretKeys"] == re.search(r"secretKeys:\s*'([^']+)'", node).group(1)  # type: ignore[union-attr]


def test_skc_7_the_values_are_read_one_line_each_and_named_without_them() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIPxhCanaryKeyBodyOnlyOneLine0123456789abcd\n-----END PRIVATE KEY-----\n"
    keyring = {"activeKid": "k1", "keys": [{"kid": "k1", "privateKey": pem, "publicKey": "pub"}]}
    assert inv.config_secret_values("gatewaySigningKeys", keyring) == {
        "config.gatewaySigningKeys.k1.privateKey": "MC4CAQAwBQYDK2VwBCIEIPxhCanaryKeyBodyOnlyOneLine0123456789abcd"
    }
    worker = {"current": "phw_" + "a" * 43, "previous": None, "previousValidUntil": None, "rotateId": None}
    assert inv.config_secret_values("workerCredential", worker) == {"config.workerCredential.current": "phw_" + "a" * 43}
    stored = inv.config_secret_values("secretKeys", {"jwtSecret": "j" * 32, "short": "x", "n": 7})
    assert stored == {"config.secretKeys.jwtSecret": "j" * 32}
    assert inv.config_secret_values("workerCredential", None) == {}
    proxy = {"current": "phw_" + "b" * 43, "previous": "phw_" + "c" * 43, "previousValidUntil": 1}
    assert inv.config_secret_values("roleCredentialEgressProxy", proxy) == {
        "config.roleCredentialEgressProxy.current": "phw_" + "b" * 43,
        "config.roleCredentialEgressProxy.previous": "phw_" + "c" * 43,
    }
    assert all("\n" not in v and v not in k for k, v in {**stored}.items())
