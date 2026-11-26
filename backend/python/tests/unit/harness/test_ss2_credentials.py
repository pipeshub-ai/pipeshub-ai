"""SS2 CR-1..CR-3, MA-2: a run started by Bob never resolves Alice's key or connector token.

With real handles: connector records go through the P2.4a credential service
(``credential_service_for`` over the platform's records), model keys through
the P2.4b vault and ``ConfiguredModelAccess`` behind the A1 guard. Alice's
secrets are canaries: they must not reach Bob's run, a log line or a refusal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.harness.credentials.service import credential_service_for
from app.harness.credentials.tool_credentials import (
    ToolCredentials,
    scope_of,
    toolset_reference,
)
from harness_core.access.errors import AccountNotOwned
from harness_core.access.guard import GuardedModelAccess
from harness_core.access.memory import InMemoryOrgAccessPolicies
from harness_core.access.types import ModelApi, ModelNeed, PinnedRoute, user_account_ref
from harness_core.credentials import CredentialRefused
from harness_core.credentials.errors import CredentialOwnerMismatch
from harness_engines.vendor_rules import release_vendor_rules
from tests.unit.harness.access.accounts_support import TENANT, Accounts
from tests.unit.harness.credentials.support import DictConfig, owned_toolsets

ALICE, BOB, AGENT = "user-alice", "user-bob", "agent-svc"
ALICE_TOKEN = "ghp_" + "A1iceA1iceA1iceA1iceA1iceA1iceA1ice"
BOB_TOKEN = "ghp_" + "B0bB0bB0bB0bB0bB0bB0bB0bB0bB0bB0bB0b"
AGENT_TOKEN = "ghp_" + "AgentAgentAgentAgentAgentAgentAgent0"
ALICE_KEY = "sk-" + "alice" * 10
BOB_KEY = "sk-" + "bobby" * 10


@dataclass
class ReadRecordingConfig(DictConfig):
    reads: list[str] = field(default_factory=list[str])

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **kwargs: Any) -> Any:  # noqa: ANN401
        self.reads.append(key)
        return await super().get_config(key, default, use_cache)


def _connections() -> ReadRecordingConfig:
    record = lambda token: {"isAuthenticated": True, "credentials": {"access_token": token}}  # noqa: E731
    return ReadRecordingConfig(
        owned_toolsets(
            {
                f"/services/toolsets/inst-1/{ALICE}": record(ALICE_TOKEN),
                f"/services/toolsets/inst-1/{BOB}": record(BOB_TOKEN),
                f"/services/toolsets/inst-1/{AGENT}": record(AGENT_TOKEN),
            },
            TENANT,
        )
    )


def _bobs_credentials(config: ReadRecordingConfig, **extra: Any) -> ToolCredentials:  # noqa: ANN401
    return ToolCredentials(
        credential_service_for(config),  # type: ignore[arg-type]
        tenant=TENANT,
        run_id="run-bob",
        principal=BOB,
        **extra,
    )


async def test_cr1_bob_s_run_resolves_his_own_connection_and_never_alice_s(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    config = _connections()
    credentials = _bobs_credentials(config)
    own = await credentials.toolset_config(toolset_reference("inst-1", BOB))
    assert own["credentials"]["access_token"] == BOB_TOKEN
    assert len(credentials.handles()) == 1

    with pytest.raises(CredentialOwnerMismatch) as refused:
        await credentials.toolset_config(toolset_reference("inst-1", ALICE))
    assert refused.value.reason_code == "credential_owner_mismatch"
    # Refused before any handle or read: Alice's record was never touched.
    assert f"/services/toolsets/inst-1/{ALICE}" not in config.reads
    assert len(credentials.handles()) == 1
    # CR-3: no canary in the refusal or any log line.
    assert ALICE_TOKEN not in str(refused.value)
    assert ALICE_TOKEN not in caplog.text and BOB_TOKEN not in caplog.text
    assert await credentials.close() == 1


async def test_cr1_the_mcp_path_is_bound_the_same_way() -> None:
    credentials = _bobs_credentials(_connections())
    scope = scope_of({"credentialRef": {"kind": "mcp", "name": f"mcp-1/{ALICE}"}})
    assert scope is not None
    with pytest.raises(CredentialOwnerMismatch):
        await credentials.material(scope)


async def test_cr2_a_service_account_agent_uses_its_own_connection_and_nobody_else_s() -> None:
    credentials = _bobs_credentials(_connections(), credential_owner=AGENT)
    agent = await credentials.toolset_config(toolset_reference("inst-1", AGENT))
    assert agent["credentials"]["access_token"] == AGENT_TOKEN
    with pytest.raises(CredentialRefused):
        await credentials.toolset_config(toolset_reference("inst-1", ALICE))


def _guard(accounts: Accounts) -> GuardedModelAccess:
    return GuardedModelAccess(
        accounts.access,
        rules=release_vendor_rules,
        policies=InMemoryOrgAccessPolicies(),
        offering="managed",
        engine_apis={"opencode": frozenset({ModelApi.CHAT_COMPLETIONS})},
    )


async def test_ma2_bob_s_run_never_resolves_alice_s_model_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    accounts = Accounts()
    alice_ref, bob_ref = user_account_ref(ALICE, "openAI"), user_account_ref(BOB, "openAI")
    await accounts.put(alice_ref, "openAI", ALICE_KEY)
    await accounts.put(bob_ref, "openAI", BOB_KEY)
    guard = _guard(accounts)
    bob = ModelNeed(actor=BOB, engine="opencode", api=ModelApi.CHAT_COMPLETIONS)
    written = len(accounts.config.vault_reads)

    with pytest.raises(AccountNotOwned):
        await guard.resolve(TENANT, PinnedRoute("openai", "gpt-x", account=alice_ref), run_id="run-bob", need=bob)
    # Refused before the vault was read: no handle was issued for Alice's key.
    assert accounts.builds.entries == []
    assert not [read for read in accounts.config.vault_reads[written:] if ALICE in read[0]]

    own = await guard.resolve(TENANT, PinnedRoute("openai", "gpt-x", account=bob_ref), run_id="run-bob", need=bob)
    assert own.access.pays == "user"
    # Bob's transport is built from Bob's key, by handle; Alice's never reaches a transport.
    assert len(accounts.builds.entries) == 1
    assert BOB_KEY in repr(accounts.builds.entries) and ALICE_KEY not in repr(accounts.builds.entries)
    assert ALICE_KEY not in caplog.text and BOB_KEY not in caplog.text
