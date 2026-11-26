"""P2.4b AR (accounts through ``ConfiguredModelAccess``), RV/RO (revocation and rotation during an active run
through the real ``ModelGateway``)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from harness_core.access.errors import (
    AccountRevoked,
    AccountUnavailable,
    ProviderUnsupported,
    RouteNotConfigured,
)
from harness_core.access.types import (
    ENGINE_MODEL_ID,
    RouteKind,
    org_account_ref,
    user_account_ref,
)
from harness_core.access.types import PinnedRoute as Route
from harness_core.gateway.model_gateway import ChatCompletion, refusal_status
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import body_of
from tests.unit.harness.access.accounts_support import TENANT, Accounts

KEY = "sk-proj-userkeyone" + "A" * 30
ROTATED = "sk-proj-userkeytwo" + "B" * 30
AZURE = "azurekey" + "C" * 32
ALICE = user_account_ref("user-a", "openAI")
AZURE_ACCOUNT = org_account_ref("acct-azure")
ROUTE = Route("openai", "gpt-4o", config_model="gpt-4o", account=ALICE)
WINDOW_S = 60


def _api_key(entry: dict[str, object]) -> object:
    return entry["configuration"]["apiKey"]  # type: ignore[index]


async def test_ar1_a_users_key_by_handle_paid_by_the_user() -> None:
    a = Accounts()
    await a.put(ALICE, "openAI", KEY)
    resolved = await a.access.resolve(TENANT, ROUTE, run_id="run-1")
    assert resolved.access.kind is RouteKind.GATEWAY and resolved.access.account == ALICE
    assert (resolved.access.provider, resolved.access.model, resolved.access.pays) == ("openai", "gpt-4o", "user")
    ((provider, entry, model, name),) = a.builds.entries
    assert (provider, model, name, _api_key(entry)) == ("openAI", "gpt-4o", ALICE.account_id, KEY)
    assert KEY not in repr(resolved.access) and KEY not in repr(a.access._cache)  # pyright: ignore[reportPrivateUsage]
    issued = list(a.credentials._issued.values())  # pyright: ignore[reportPrivateUsage]
    assert [(i.credential.purpose.tool, i.credential.purpose.run_id, i.principal.subject) for i in issued] == [
        (f"model:{ALICE.account_id}", "run-1", "user-a")
    ]


async def test_ar2_an_org_azure_account_builds_its_deployment() -> None:
    a = Accounts()
    await a.put(AZURE_ACCOUNT, "azureOpenAI", AZURE, endpoint="https://x.openai.azure.com", deploymentName="dep-1")
    resolved = await a.access.resolve(TENANT, Route("azureopenai", "gpt-5", account=AZURE_ACCOUNT), run_id="run-1")
    assert resolved.access.pays == "org" and resolved.access.account == AZURE_ACCOUNT
    ((_, entry, _, _),) = a.builds.entries
    assert entry["configuration"] == {
        "apiKey": AZURE,
        "endpoint": "https://x.openai.azure.com",
        "deploymentName": "dep-1",
        "model": "gpt-5",
    }


CLOUD_FIELDS = {
    "bedrock": {"region": "us-east-1", "accessKeyId": "AKIA" + "E3CTESTKEYID0000", "secretAccessKey": "s" * 40},
    "vertexAI": {"projectId": "proj-e3c-test", "location": "us-east5", "serviceAccountJson": '{"type": "service_account"}'},
}


@pytest.mark.parametrize(
    ("provider", "model", "pricing"),
    [
        ("bedrock", "us.anthropic.claude-sonnet-4-6-v1:0", "claude-sonnet-4-6"),
        ("vertexAI", "claude-sonnet-4-6@20250929", "claude-sonnet-4-6"),
    ],
)
async def test_ar3_bv4_cloud_accounts_reach_anthropic_through_their_cloud(provider: str, model: str, pricing: str) -> None:
    # E3c: P2.4b stored these accounts with no transport (provider_unsupported); the cloud transports now exist.
    a = Accounts()
    ref = org_account_ref(f"acct-{provider}")
    await a.put(ref, provider, "unused", **CLOUD_FIELDS[provider])
    resolved = await a.access.resolve(TENANT, Route(provider.lower(), model, account=ref), run_id="run-1")
    assert (resolved.access.pays, resolved.access.account, resolved.pricing_model) == ("org", ref, pricing)
    ((built, entry, built_model, name),) = a.builds.entries
    assert (built, built_model, name) == (provider, model, ref.account_id)
    assert entry["configuration"] == {**CLOUD_FIELDS[provider], "model": model}


@pytest.mark.parametrize(("provider", "model"), [("bedrock", "meta.llama3-70b-instruct-v1:0"), ("vertexAI", "gemini-2.5-pro")])
async def test_bv4_another_vendors_model_on_a_cloud_account_is_unsupported(provider: str, model: str) -> None:
    a = Accounts()
    ref = org_account_ref(f"acct-{provider}")
    await a.put(ref, provider, "unused", **CLOUD_FIELDS[provider])
    with pytest.raises(ProviderUnsupported):
        _ = await a.access.resolve(TENANT, Route(provider.lower(), model, account=ref), run_id="run-1")
    assert a.builds.entries == []


async def test_bv4_the_default_factory_builds_the_sdk_cloud_client() -> None:
    from app.harness.access.configured import build_configured_transport
    from harness_core.models.transports.anthropic import AnthropicTransport
    from harness_core.testing.cloud_providers import service_account

    entry: dict[str, object] = {"configuration": CLOUD_FIELDS["bedrock"]}
    bedrock = await build_configured_transport("bedrock", entry, "anthropic.claude-sonnet-4-6", "acct-1")
    assert isinstance(bedrock, AnthropicTransport)
    assert type(bedrock._client).__name__ == "AsyncAnthropicBedrock"  # pyright: ignore[reportPrivateUsage]
    vertex_fields = {**CLOUD_FIELDS["vertexAI"], "serviceAccountJson": service_account("https://elsewhere.example/token").json}
    vertex = await build_configured_transport("vertexAI", {"configuration": vertex_fields}, "claude-sonnet-4-6", "acct-2")
    assert isinstance(vertex, AnthropicTransport)
    client = vertex._client  # pyright: ignore[reportPrivateUsage]
    assert type(client).__name__ == "AsyncAnthropicVertex"
    assert str(client.base_url).startswith("https://us-east5-aiplatform.googleapis.com/v1")
    # The account file's token endpoint is replaced by Google's.
    assert client.credentials._token_uri == "https://oauth2.googleapis.com/token"  # pyright: ignore[reportAttributeAccessIssue]


async def test_ar4_an_account_of_another_provider_is_not_the_pinned_route() -> None:
    a = Accounts()
    await a.put(ALICE, "openAI", KEY)
    with pytest.raises(RouteNotConfigured):
        _ = await a.access.resolve(TENANT, Route("anthropic", "claude-x", account=ALICE), run_id="run-1")
    assert a.builds.entries == []


async def test_ar5_missing_removed_and_unreadable_accounts_are_typed_refusals() -> None:
    a = Accounts()
    with pytest.raises(RouteNotConfigured):
        _ = await a.access.resolve(TENANT, ROUTE, run_id="run-1")
    await a.put(ALICE, "openAI", KEY)
    _ = await a.store.remove(TENANT, ALICE, actor="user-a")
    with pytest.raises(AccountRevoked) as revoked:
        _ = await a.access.resolve(TENANT, ROUTE, run_id="run-2")
    assert (revoked.value.reason_code, refusal_status(revoked.value)) == ("account_revoked", 403)
    b = Accounts()
    await b.put(ALICE, "openAI", KEY)
    b.config.down = True
    with pytest.raises(AccountUnavailable) as down:
        _ = await b.access.resolve(TENANT, ROUTE, run_id="run-1")
    assert (down.value.reason_code, refusal_status(down.value)) == ("account_unavailable", 503)


async def _call(a: Accounts, rig: Any, binding: Any) -> ChatCompletion:  # noqa: ANN401
    outcome = await rig.gateway.chat(
        rig.token(binding), {}, body_of({"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}]})
    )
    assert isinstance(outcome, ChatCompletion)
    return outcome


async def _refused(rig: Any, binding: Any) -> HarnessRefusal:  # noqa: ANN401
    with pytest.raises(HarnessRefusal) as refused:
        _ = await rig.gateway.chat(
            rig.token(binding), {}, body_of({"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}]})
        )
    return refused.value


@pytest.mark.parametrize("after_s", [1, 30, WINDOW_S])
async def test_rv1_a_removed_key_is_refused_at_the_next_call_of_an_active_run(
    after_s: int, caplog: pytest.LogCaptureFixture
) -> None:
    a = Accounts()
    await a.put(ALICE, "openAI", KEY)
    rig = a.rig()
    binding = rig.pin(routes=(ROUTE,), pays="user")
    _ = await _call(a, rig, binding)
    calls = len(rig.transport.calls)
    assert calls == 1

    _ = await a.store.remove(TENANT, ALICE, actor="user-a")
    _ = a.clock.advance(after_s)
    caplog.set_level(logging.WARNING, logger="harness_core.gateway.model")
    for _attempt in range(2):
        refused = await _refused(rig, binding)
        assert (refused.reason_code, refusal_status(refused)) == ("account_revoked", 403)
    assert len(rig.transport.calls) == calls
    assert not [k for k in a.access._cache if k[0] == ALICE.account_id]  # pyright: ignore[reportPrivateUsage]
    gateway = [r for r in caplog.records if getattr(r, "harness_event", None) == "model_gateway.refused"]
    assert [r.harness["reason"] for r in gateway] == ["account_revoked", "account_revoked"]  # type: ignore[attr-defined]


async def test_rv2_a_store_outage_at_the_next_call_fails_closed() -> None:
    a = Accounts()
    await a.put(ALICE, "openAI", KEY)
    rig = a.rig()
    binding = rig.pin(routes=(ROUTE,), pays="user")
    _ = await _call(a, rig, binding)
    a.config.down = True
    refused = await _refused(rig, binding)
    assert (refused.reason_code, refusal_status(refused)) == ("account_unavailable", 503)
    assert len(rig.transport.calls) == 1


async def test_rv3_every_call_reads_the_account_uncached_and_failing_closed() -> None:
    a = Accounts()
    await a.put(ALICE, "openAI", KEY)
    rig = a.rig()
    binding = rig.pin(routes=(ROUTE,), pays="user")
    a.config.vault_reads.clear()
    for _call_number in range(3):
        _ = await _call(a, rig, binding)
    document = [r for r in a.config.vault_reads if r[0].endswith(ALICE.account_id)]
    assert len(document) >= 3
    assert all((use_cache, raise_on_error) == (False, True) for _, use_cache, raise_on_error in a.config.vault_reads)


async def test_ro1_a_replaced_key_keeps_the_active_run_working_and_reaches_it_by_its_handle_ttl() -> None:
    a = Accounts()
    await a.put(ALICE, "openAI", KEY)
    rig = a.rig()
    binding = rig.pin(routes=(ROUTE,), pays="user")
    _ = await _call(a, rig, binding)
    _ = a.clock.advance(10)
    await a.put(ALICE, "openAI", ROTATED)
    for step in (10, 200, 300):
        _ = a.clock.advance(step)
        _ = await _call(a, rig, binding)
    assert [_api_key(e) for _, e, _, _ in a.builds.entries] == [KEY]

    _ = a.clock.advance(30)
    _ = await _call(a, rig, binding)
    assert [_api_key(e) for _, e, _, _ in a.builds.entries] == [KEY, ROTATED]

    other = rig.pin("run-2", routes=(ROUTE,), pays="user")
    _ = await _call(a, rig, other)
    assert len(rig.transport.calls) == 6
    issued = [i.credential for i in a.credentials._issued.values()]  # pyright: ignore[reportPrivateUsage]
    assert [(c.purpose.run_id, c.version) for c in issued] == [("run-1", "1"), ("run-1", "2"), ("run-2", "2")]
