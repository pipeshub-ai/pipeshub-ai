from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.service import TokenScopes
from app.services.model_identity import metrics
from app.services.model_identity.call_context import (
    current_call_context,
    peek_call_context,
)
from app.services.model_identity.principal import (
    PrincipalKind,
    service_account_principal,
    service_principal,
)
from app.services.model_identity.propagation import (
    PCP_CLAIM,
    decode_pcp,
    encode_pcp,
    scope_from_claims,
)
from app.services.model_identity.runtime import (
    call_scope,
    reset_forwarding_state,
    set_identity_runtime,
)
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    set_identity_runtime(None)
    reset_forwarding_state()
    yield
    set_identity_runtime(None)
    reset_forwarding_state()


async def _claim_for(principal, workload, **meta) -> dict[str, object] | None:
    async with call_scope(principal, workload, **meta):
        return encode_pcp()


class TestEncode:
    def test_nothing_bound_nothing_sent(self) -> None:
        assert encode_pcp() is None

    async def test_a_user_claim_carries_ids_only(self, alice) -> None:
        claim = await _claim_for(alice, Workload.CHAT, conversation_id="c1", request_id="r1")
        assert claim == {
            "v": 1, "kind": "user", "sub": "u-alice", "org": "org-1", "wl": "chat", "rid": "r1",
        }
        flat = repr(claim)
        assert "alice@example.com" not in flat and "Alice" not in flat and "oid-alice" not in flat

    async def test_record_and_connector_ride_along(self) -> None:
        claim = await _claim_for(
            service_principal("indexing", "org-1"), Workload.INDEXING, record_id="rec1", connector_id="con1"
        )
        assert claim["rec"] == "rec1" and claim["con"] == "con1"
        assert claim["kind"] == "service" and claim["sub"] == "indexing"


class TestDecode:
    async def test_round_trips_a_user(self, alice) -> None:
        decoded = decode_pcp({"orgId": "org-1", PCP_CLAIM: await _claim_for(alice, Workload.CHAT)})
        assert decoded.principal.kind is PrincipalKind.USER
        assert (decoded.principal.subject_id, decoded.principal.org_id) == ("u-alice", "org-1")
        assert decoded.origin_workload is Workload.CHAT

    async def test_round_trips_a_service_account_and_a_service(self) -> None:
        sa = await _claim_for(service_account_principal("agent-1", "org-1"), Workload.AGENT, agent_id="agent-1")
        decoded = decode_pcp({"orgId": "org-1", PCP_CLAIM: sa})
        assert (decoded.principal.kind, decoded.principal.subject_id) == (PrincipalKind.SERVICE_ACCOUNT, "agent-1")
        assert decoded.agent_id == "agent-1"
        svc = await _claim_for(service_principal("indexing"), Workload.INDEXING)
        decoded = decode_pcp({PCP_CLAIM: svc})
        assert decoded.principal.service_name == "indexing"

    def test_absent_claim_is_not_an_error(self) -> None:
        assert decode_pcp({}) is None
        assert metrics.counter_value(metrics.IDENTITY_MISSING, "bad_pcp_shape") == 0

    @pytest.mark.parametrize(
        "claim, reason",
        [
            ({"v": 2, "kind": "user", "sub": "u", "org": "o", "wl": "chat"}, "version"),
            ("user", "version"),
            ({"v": 1, "kind": "root", "sub": "u", "org": "o", "wl": "chat"}, "shape"),
            ({"v": 1, "kind": "user", "org": "o", "wl": "chat"}, "shape"),
            ({"v": 1, "kind": "user", "sub": "u", "wl": "chat"}, "shape"),
            ({"v": 1, "kind": "user", "sub": ["u"], "org": "o", "wl": "chat"}, "shape"),
            ({"v": 1, "kind": "user", "sub": "x" * 300, "org": "o", "wl": "chat"}, "shape"),
            ({"v": 1, "kind": "user", "sub": "u", "org": "o", "wl": "nope"}, "workload"),
        ],
    )
    def test_malformed_claims_are_dropped_and_counted(self, claim, reason, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            assert decode_pcp({"orgId": "o", PCP_CLAIM: claim}) is None
        assert metrics.counter_value(metrics.IDENTITY_MISSING, f"bad_pcp_{reason}") == 1

    @pytest.mark.parametrize("kind", ["user", "service_account"])
    def test_a_person_or_agent_is_not_believed_without_the_tokens_org(self, kind) -> None:
        claim = {"v": 1, "kind": kind, "sub": "u", "org": "org-1", "wl": "chat"}
        assert decode_pcp({PCP_CLAIM: claim}) is None
        assert metrics.counter_value(metrics.IDENTITY_MISSING, "bad_pcp_org_mismatch") == 1

    def test_a_claim_naming_another_org_than_the_token_is_dropped(self) -> None:
        claim = {"v": 1, "kind": "user", "sub": "u", "org": "org-evil", "wl": "chat"}
        assert decode_pcp({"orgId": "org-1", PCP_CLAIM: claim}) is None
        assert metrics.counter_value(metrics.IDENTITY_MISSING, "bad_pcp_org_mismatch") == 1


class TestScopeFromClaims:
    async def test_binds_the_propagated_person_under_the_routes_workload(self, alice) -> None:
        claims = {"orgId": "org-1", PCP_CLAIM: await _claim_for(alice, Workload.INDEXING)}
        # indexing is background, extraction is background: route workload wins
        async with scope_from_claims(claims, Workload.EXTRACTION, fallback_service="extraction") as ctx:
            assert ctx.principal.subject_id == "u-alice"
            assert ctx.workload is Workload.EXTRACTION
            assert current_call_context() is ctx

    async def test_an_interactive_origin_stays_interactive(self, alice) -> None:
        claims = {"orgId": "org-1", PCP_CLAIM: await _claim_for(alice, Workload.CHAT)}
        async with scope_from_claims(claims, Workload.PARSING, fallback_service="parsing") as ctx:
            assert ctx.workload is Workload.CHAT

    async def test_without_a_claim_the_callee_is_its_own_service(self) -> None:
        async with scope_from_claims(
            {"orgId": "org-1"}, Workload.PARSING, fallback_service="parsing"
        ) as ctx:
            assert ctx.principal.kind is PrincipalKind.SERVICE
            assert ctx.principal.service_name == "parsing"
            assert ctx.principal.org_id == "org-1"

    async def test_a_bad_claim_falls_back_to_the_service_never_a_guess(self) -> None:
        claims = {"orgId": "org-1", PCP_CLAIM: {"v": 1, "kind": "user", "sub": "u-x", "org": "org-2", "wl": "chat"}}
        async with scope_from_claims(claims, Workload.EXTRACTION, fallback_service="extraction") as ctx:
            assert ctx.principal.kind is PrincipalKind.SERVICE
        assert peek_call_context() is None


class TestServiceToken:
    async def _headers(self, org_id, bound=None) -> dict[str, object]:
        from app.services.base_client import BaseServiceClient

        client = BaseServiceClient.__new__(BaseServiceClient)
        client.service_name = "extraction"
        client._service_scope = TokenScopes.DOCUMENT_CLASSIFY
        client._config_service = MagicMock()
        captured = {}

        async def fake_jwt(_cfg, claims) -> str:
            captured.update(claims)
            return "tok"

        with patch("app.services.base_client.generate_jwt", new=fake_jwt):
            if bound is None:
                await client._auth_headers(org_id)
            else:
                async with call_scope(*bound):
                    await client._auth_headers(org_id)
        return captured

    async def test_the_token_carries_the_bound_identity(self, alice) -> None:
        claims = await self._headers("org-1", (alice, Workload.INDEXING))
        assert claims[PCP_CLAIM]["sub"] == "u-alice"
        assert claims["scopes"] == [TokenScopes.DOCUMENT_CLASSIFY.value]
        assert decode_pcp(claims).principal.subject_id == "u-alice"

    async def test_nothing_bound_means_no_claim(self) -> None:
        assert PCP_CLAIM not in await self._headers("org-1")

    async def test_a_context_of_another_org_is_not_sent(self, alice) -> None:
        assert PCP_CLAIM not in await self._headers("org-2", (alice, Workload.CHAT))

    async def test_a_service_without_an_org_still_sends(self) -> None:
        claims = await self._headers(None, (service_principal("indexing"), Workload.INDEXING))
        assert claims[PCP_CLAIM]["kind"] == "service"
