from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config.constants.service import config_node_constants
from app.services.model_identity import metrics
from app.services.model_identity.boundary import (
    UNATTRIBUTED_SERVICE,
    principal_from_request_user,
)
from app.services.model_identity.call_context import (
    current_call_context,
    narrow_workload,
    peek_call_context,
)
from app.services.model_identity.groups import GroupMembership
from app.services.model_identity.policy_store import IdentityPolicyStore
from app.services.model_identity.principal import PrincipalKind, service_principal
from app.services.model_identity.runtime import (
    IdentityRuntime,
    MissingIdentityError,
    assert_identity_allowed,
    call_scope,
    note_forwarding,
    reset_forwarding_state,
    set_identity_runtime,
)
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

POLICY_KEY = config_node_constants.AI_GATEWAY_IDENTITY_POLICY.value


class _Config:
    def __init__(self, policy=None) -> None:
        self.policy = policy

    async def get_config(self, key, default=None, use_cache=False, **_) -> object:
        if key == POLICY_KEY and self.policy is not None:
            return self.policy
        return default


@pytest.fixture(autouse=True)
def _fresh_runtime() -> Iterator[None]:
    set_identity_runtime(None)
    reset_forwarding_state()
    yield
    set_identity_runtime(None)
    reset_forwarding_state()


class TestPrincipalFromRequestUser:
    def test_a_person(self) -> None:
        p = principal_from_request_user({"userId": "u1", "orgId": "o1", "email": "a@x.com"})
        assert (p.kind, p.subject_id, p.org_id) == (PrincipalKind.USER, "u1", "o1")

    def test_a_service_account_token_is_the_agent(self) -> None:
        p = principal_from_request_user(
            {"userId": "sa-user", "orgId": "o1", "isServiceAccount": True}, agent_id="agent-9"
        )
        assert (p.kind, p.subject_id) == (PrincipalKind.SERVICE_ACCOUNT, "agent-9")

    def test_a_service_account_token_without_an_agent_uses_its_own_id(self) -> None:
        p = principal_from_request_user({"userId": "sa-user", "orgId": "o1", "isServiceAccount": True})
        assert p.subject_id == "sa-user"

    @pytest.mark.parametrize("user", [None, {}, {"userId": "u1"}, {"orgId": "o1"}])
    def test_nothing_usable_is_an_unattributed_service(self, user) -> None:
        p = principal_from_request_user(user)
        assert p.kind is PrincipalKind.SERVICE
        assert p.service_name == UNATTRIBUTED_SERVICE

    def test_agent_id_does_not_turn_a_person_into_a_service_account(self) -> None:
        p = principal_from_request_user({"userId": "u1", "orgId": "o1"}, agent_id="agent-9")
        assert p.kind is PrincipalKind.USER


class TestCallScope:
    async def test_binds_for_the_block_and_restores_after(self, alice) -> None:
        before = peek_call_context()
        async with call_scope(alice, Workload.CHAT, conversation_id="c1") as ctx:
            assert current_call_context() is ctx
            assert ctx.principal == alice
            assert ctx.conversation_id == "c1"
        assert peek_call_context() is before

    async def test_a_failure_preparing_the_context_still_binds_a_default(self, alice) -> None:
        broken = MagicMock()
        async with call_scope(alice, Workload.CHAT, config_service=broken) as ctx:
            assert ctx.principal == alice

    async def test_loads_the_org_policy(self, alice) -> None:
        cfg = _Config({"includeEmail": True})
        async with call_scope(alice, Workload.CHAT, config_service=cfg) as ctx:
            assert ctx.governance.include_email is True

    async def test_children_inherit_the_binding(self, alice) -> None:
        seen = {}

        async def sub_agent() -> None:
            seen["task"] = current_call_context().principal
            await asyncio.sleep(0)
            seen["nested"] = await asyncio.to_thread(lambda: current_call_context().principal)

        async with call_scope(alice, Workload.AGENT):
            await asyncio.gather(asyncio.create_task(sub_agent()))
        assert seen == {"task": alice, "nested": alice}

    async def test_concurrent_requests_do_not_see_each_other(self, alice, bob) -> None:
        results = {}

        async def request(principal) -> None:
            async with call_scope(principal, Workload.CHAT):
                for _ in range(5):
                    await asyncio.sleep(0)
                    assert current_call_context().principal == principal
                results[principal.subject_id] = True

        await asyncio.gather(*(request(p) for p in [alice, bob] * 10))
        assert results == {"u-alice": True, "u-bob": True}

    async def test_request_id_defaults_to_the_trace_root(self, alice) -> None:
        async with call_scope(alice, Workload.CHAT, request_id="r-1") as ctx:
            assert ctx.request_id == "r-1"


class TestStrictMode:
    STRICT = {"onMissingIdentity": "block"}

    async def test_blocks_an_interactive_call_with_no_person_when_forwarding(self) -> None:
        note_forwarding(requires_groups=False)
        with pytest.raises(MissingIdentityError) as exc:
            async with call_scope(
                service_principal("x", "o1"), Workload.CHAT, config_service=_Config(self.STRICT)
            ):
                pytest.fail("must not run")
        assert exc.value.status_code == 403
        assert isinstance(exc.value, HTTPException)
        assert metrics.counter_value(metrics.IDENTITY_MISSING, "blocked_chat") == 1

    async def test_does_nothing_when_no_connection_forwards(self) -> None:
        async with call_scope(
            service_principal("x", "o1"), Workload.CHAT, config_service=_Config(self.STRICT)
        ):
            pass

    @pytest.mark.parametrize(
        "workload", [Workload.INDEXING, Workload.HEALTH_CHECK, Workload.SYSTEM, Workload.EXTRACTION]
    )
    async def test_never_blocks_background_or_admin_work(self, workload) -> None:
        note_forwarding(requires_groups=False)
        async with call_scope(
            service_principal("x", "o1"), workload, config_service=_Config(self.STRICT)
        ):
            pass

    async def test_a_service_account_is_an_identified_principal(self) -> None:
        note_forwarding(requires_groups=False)
        from app.services.model_identity.principal import service_account_principal

        async with call_scope(
            service_account_principal("agent-1", "o1"), Workload.AGENT, config_service=_Config(self.STRICT)
        ):
            pass

    async def test_a_person_passes(self, alice) -> None:
        note_forwarding(requires_groups=False)
        async with call_scope(alice, Workload.CHAT, config_service=_Config(self.STRICT)):
            pass

    async def test_the_precheck_raises_before_a_stream_starts(self) -> None:
        note_forwarding(requires_groups=False)
        with pytest.raises(MissingIdentityError):
            await assert_identity_allowed(
                service_principal("x", "o1"), Workload.CHAT, config_service=_Config(self.STRICT)
            )

    async def test_the_precheck_tolerates_a_policy_outage(self) -> None:
        note_forwarding(requires_groups=False)
        await assert_identity_allowed(
            service_principal("x", "o1"), Workload.CHAT, config_service=MagicMock()
        )


class _Resolver:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, org_id, principal) -> GroupMembership:
        self.calls += 1
        return GroupMembership(ids=("g1",), source="test", synced_at=0.0, complete=True)


class TestGroupPrefetch:
    def _runtime(self, resolver) -> IdentityRuntime:
        return IdentityRuntime(IdentityPolicyStore(_Config({"groupsEnabled": True})), resolver)

    async def test_starts_a_lookup_for_a_person_when_a_forwarder_wants_groups(self, alice) -> None:
        note_forwarding(requires_groups=True)
        resolver = _Resolver()
        ctx = await self._runtime(resolver).context(alice, Workload.CHAT)
        assert ctx.groups is not None
        assert (await ctx.groups.wait(1.0)).ids == ("g1",)
        assert resolver.calls == 1

    async def test_skips_the_lookup_when_nothing_would_read_it(self, alice) -> None:
        note_forwarding(requires_groups=False)
        resolver = _Resolver()
        ctx = await self._runtime(resolver).context(alice, Workload.CHAT)
        assert ctx.groups is None
        assert resolver.calls == 0

    async def test_service_principals_have_no_groups(self) -> None:
        note_forwarding(requires_groups=True)
        resolver = _Resolver()
        ctx = await self._runtime(resolver).context(service_principal("x", "o1"), Workload.INDEXING)
        assert ctx.groups is None

    async def test_the_default_resolver_does_nothing(self, alice) -> None:
        note_forwarding(requires_groups=True)
        ctx = await IdentityRuntime(
            IdentityPolicyStore(_Config({"groupsEnabled": True}))
        ).context(alice, Workload.CHAT)
        assert ctx.groups is None


class TestNarrowWorkload:
    async def test_narrows_within_the_same_class(self, alice) -> None:
        async with call_scope(alice, Workload.CHAT):
            with narrow_workload(Workload.QUERY_EMBEDDING):
                assert current_call_context().workload is Workload.QUERY_EMBEDDING
                assert current_call_context().principal == alice
            assert current_call_context().workload is Workload.CHAT

    async def test_is_a_no_op_across_classes(self) -> None:
        async with call_scope(service_principal("job", "o1"), Workload.INDEXING):
            with narrow_workload(Workload.QUERY_EMBEDDING):
                assert current_call_context().workload is Workload.INDEXING

    def test_unbound_code_is_unaffected(self) -> None:
        with narrow_workload(Workload.QUERY_EMBEDDING):
            assert current_call_context().workload is Workload.SYSTEM


class TestChatBoundary:
    async def test_the_stream_runs_under_the_callers_identity(self, monkeypatch) -> None:
        from app.api.routes import chatbot

        seen = []

        async def inner(**_) -> AsyncIterator[object]:
            seen.append(current_call_context().principal)
            yield "event"

        monkeypatch.setattr(chatbot, "_stream_chat_via_agent_loop", inner)
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1", "email": "a@x.com"}
        query = MagicMock(conversationId="c9")

        events = [
            e
            async for e in chatbot._generate_chat_stream_via_agent_loop(
                request, query, MagicMock(), MagicMock(), MagicMock(), None
            )
        ]
        assert events == ["event"]
        assert seen[0].subject_id == "u1"
        assert peek_call_context() is None

    async def test_closing_the_outer_stream_closes_the_inner_one(self, monkeypatch) -> None:
        from app.api.routes import chatbot

        closed = []

        async def inner(**_) -> AsyncIterator[object]:
            try:
                while True:
                    yield "tick"
            finally:
                closed.append(True)

        monkeypatch.setattr(chatbot, "_stream_chat_via_agent_loop", inner)
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        stream = chatbot._generate_chat_stream_via_agent_loop(
            request, MagicMock(conversationId=None), MagicMock(), MagicMock(), MagicMock(), None
        )
        await stream.__anext__()
        await stream.aclose()
        assert closed == [True]
