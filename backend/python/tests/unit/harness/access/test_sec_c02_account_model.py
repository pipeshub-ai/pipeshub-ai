"""SEC-C-02 (ADR-MA-01): the model gateway re-checks an account route's model against the configured models.

Node's pin is the authority; this is the second layer. An account only changes who pays, so the model a user key
or an org cloud account reaches is still one the org configured for that provider: the entry the pin keys
(``config_key``), or, for a pin made before Node keyed account routes, any entry of that provider listing it.
"""

from __future__ import annotations

import pytest

from harness_core.access.errors import RouteNotConfigured
from harness_core.access.types import ENGINE_MODEL_ID, org_account_ref, user_account_ref
from harness_core.access.types import PinnedRoute as Route
from harness_core.gateway.model_gateway import refusal_status
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import body_of
from tests.unit.harness.access.accounts_support import TENANT, Accounts

KEY = "sk-proj-userkeyone" + "A" * 30
AZURE = "azurekey" + "C" * 32
ALICE = user_account_ref("user-a", "openAI")
CLOUD = org_account_ref("acct-azure")


async def _alice() -> Accounts:
    a = Accounts()
    await a.put(ALICE, "openAI", KEY)
    await a.put(CLOUD, "azureOpenAI", AZURE, endpoint="https://x.openai.azure.com", deploymentName="dep-1")
    return a


@pytest.mark.parametrize(
    "route",
    [
        Route("openai", "gpt-4o", config_key="m-openai", config_model="gpt-4o", account=ALICE),
        Route("openai", "gpt-x", config_key="m-openai", config_model="gpt-x", account=ALICE),
        Route("azureopenai", "gpt-5", config_key="m-azure", config_model="gpt-5", account=CLOUD),
        # A pin made before Node keyed account routes: found by provider and name.
        Route("openai", "gpt-4o", config_model="gpt-4o", account=ALICE),
        Route("azureopenai", "gpt-5", account=CLOUD),
    ],
    ids=["own-key", "own-key-second-name", "cloud", "old-pin-own-key", "old-pin-cloud"],
)
async def test_c02_py1_a_configured_model_on_an_account_resolves(route: Route) -> None:
    a = await _alice()
    resolved = await a.access.resolve(TENANT, route, run_id="run-1")
    assert (resolved.access.model, resolved.access.account) == (route.config_model or route.model, route.account)


@pytest.mark.parametrize(
    "route",
    [
        # A name nobody configured, keyed and unkeyed (the SEC-C-02 attack: the org's cloud account, any model).
        Route("azureopenai", "o1-pro-most-expensive", config_key="m-azure", config_model="o1-pro-most-expensive", account=CLOUD),
        Route("azureopenai", "o1-pro-most-expensive", account=CLOUD),
        Route("openai", "o1-pro", config_key="m-openai", config_model="o1-pro", account=ALICE),
        # The key names another provider's entry.
        Route("openai", "gpt-4o", config_key="m-anthropic", config_model="gpt-4o", account=ALICE),
        # Key and name disagree: the name is another entry's.
        Route("openai", "gpt-5", config_key="m-openai", config_model="gpt-5", account=ALICE),
        # A key nobody configured.
        Route("openai", "gpt-4o", config_key="m-gone", config_model="gpt-4o", account=ALICE),
        # Configured, but for another provider than the account's (an old pin, found by name only).
        Route("azureopenai", "gpt-4o", account=CLOUD),
    ],
    ids=["unconfigured-keyed", "unconfigured-old-pin", "unconfigured-own-key", "wrong-provider-key", "key-name-mismatch", "unknown-key", "other-providers-name"],
)
async def test_c02_py2_a_model_the_org_did_not_configure_for_the_account_is_refused(route: Route) -> None:
    a = await _alice()
    with pytest.raises(RouteNotConfigured) as refused:
        _ = await a.access.resolve(TENANT, route, run_id="run-1")
    assert (refused.value.reason_code, refusal_status(refused.value)) == ("route_not_configured", 403)
    assert a.builds.entries == []


@pytest.mark.parametrize("name", ["GPT-4o", " gpt-4o", "gpt-4o ", "gpt-4o​", "gpt‑4o", "gрt-4o", "gpt-4o,o1-pro"])
async def test_c02_py3_case_whitespace_and_look_alike_names_are_refused_keyed_or_not(name: str) -> None:
    a = await _alice()
    for route in (
        Route("openai", name, config_key="m-openai", config_model=name, account=ALICE),
        Route("openai", name, config_model=name, account=ALICE),
    ):
        with pytest.raises(RouteNotConfigured):
            _ = await a.access.resolve(TENANT, route, run_id="run-1")
    assert a.builds.entries == []


async def test_c02_py4_the_gateway_refuses_the_call_and_reaches_no_provider() -> None:
    a = await _alice()
    rig = a.rig()
    binding = rig.pin(
        routes=(Route("azureopenai", "o1-pro-most-expensive", config_model="o1-pro-most-expensive", account=CLOUD),),
        pays="org",
    )
    with pytest.raises(HarnessRefusal) as refused:
        _ = await rig.gateway.chat(
            rig.token(binding), {}, body_of({"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}]})
        )
    assert (refused.value.reason_code, refusal_status(refused.value)) == ("route_not_configured", 403)
    assert rig.transport.calls == []
