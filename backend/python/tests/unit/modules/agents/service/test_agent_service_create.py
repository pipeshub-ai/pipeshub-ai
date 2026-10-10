import logging
from typing import Any

import pytest
from fastapi import HTTPException

from app.modules.agents.service.agent_service import AgentService
from app.modules.agents.service.errors import HandleTakenError
from app.modules.agents.service.models import (
    AgentActor,
    AgentPatch,
    AgentSpec,
    ChatProvenance,
)
from app.services.graph_db.errors import UniqueConstraintViolation
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    user_key,
)

ALICE = AgentActor(user_key=user_key("alice"), user_id="u-alice", org_id="org-1")
MALLORY = AgentActor(user_key=user_key("mallory"), user_id="u-mallory", org_id="org-2")


def _service(graph: InMemoryGraph) -> AgentService:
    return AgentService(graph, FakeConfigService(), logging.getLogger("test.agent_service"))  # type: ignore[arg-type]


def _agents(graph: InMemoryGraph) -> dict[str, dict[str, Any]]:
    return graph.nodes.get(AGENTS, {})


def _detail(exc: HTTPException) -> dict[str, str]:
    assert isinstance(exc.detail, dict)
    return exc.detail


class LosesRaceOnce(InMemoryGraph):
    """A competing create commits the very handle we are writing, `times` times in a row."""

    def __init__(self, times: int = 1) -> None:
        super().__init__()
        self.remaining = times
        self.racers: list[tuple[str, str]] = []

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        if collection == AGENTS and self.remaining > 0:
            self.remaining -= 1
            key = f"racer-{len(self.racers)}"
            self.racers.append((key, nodes[0]["handle"]))
            self.add_agent(key, "bob", orgId="org-1", handle=nodes[0]["handle"])
        return await super().batch_upsert_nodes(nodes, collection, transaction)

    async def rollback_transaction(self, transaction: str) -> None:
        await super().rollback_transaction(transaction)
        # The racer committed on its own; our rollback must not undo it.
        for key, handle in self.racers:
            if key not in self.nodes.get(AGENTS, {}):
                self.add_agent(key, "bob", orgId="org-1", handle=handle)


class TestCreateHandle:
    @pytest.mark.asyncio
    async def test_new_agent_carries_org_handle_and_origin(self) -> None:
        graph = InMemoryGraph()

        created = await _service(graph).create(ALICE, AgentSpec(name="Offer drafter"))

        stored = _agents(graph)[created.agent_key]
        assert created.handle == "offer-drafter"
        assert (stored["orgId"], stored["handle"], stored["createdVia"]) == ("org-1", "offer-drafter", "ui")
        assert created.agent["handle"] == "offer-drafter"
        assert "sourceConversationId" not in stored

    @pytest.mark.asyncio
    async def test_chat_origin_records_provenance(self) -> None:
        graph = InMemoryGraph()

        created = await _service(graph).create(
            ALICE, AgentSpec(name="Drafter"), origin="chat",
            provenance=ChatProvenance(conversation_id="c-1", message_id="m-1"),
        )

        stored = _agents(graph)[created.agent_key]
        assert (stored["createdVia"], stored["sourceConversationId"], stored["sourceMessageId"]) == ("chat", "c-1", "m-1")

    @pytest.mark.asyncio
    async def test_creating_twice_from_one_draft_returns_the_first_agent(self) -> None:
        graph = InMemoryGraph()
        service = _service(graph)
        draft = ChatProvenance(conversation_id="c-1", message_id="m-1")

        first = await service.create(ALICE, AgentSpec(name="Drafter"), origin="chat", provenance=draft)
        again = await service.create(ALICE, AgentSpec(name="Drafter", handle="other"), origin="chat", provenance=draft)

        assert (again.agent_key, again.handle) == (first.agent_key, "drafter")
        assert again.agent["createdBy"] == "u-alice"
        assert len(_agents(graph)) == 1
        assert len(graph.calls_to("begin_transaction")) == 1

    @pytest.mark.asyncio
    async def test_a_deleted_agent_or_another_draft_creates_anew(self) -> None:
        graph = InMemoryGraph()
        service = _service(graph)
        draft = ChatProvenance(conversation_id="c-1", message_id="m-1")
        first = await service.create(ALICE, AgentSpec(name="Drafter"), origin="chat", provenance=draft)
        _agents(graph)[first.agent_key]["isDeleted"] = True

        recreated = await service.create(ALICE, AgentSpec(name="Drafter"), origin="chat", provenance=draft)
        other = await service.create(
            ALICE, AgentSpec(name="Drafter"), origin="chat",
            provenance=ChatProvenance(conversation_id="c-1", message_id="m-2"),
        )

        assert len({first.agent_key, recreated.agent_key, other.agent_key}) == 3

    @pytest.mark.asyncio
    async def test_same_name_gets_a_numbered_handle(self) -> None:
        graph = InMemoryGraph()
        service = _service(graph)

        handles = [(await service.create(ALICE, AgentSpec(name="Sales Bot"))).handle for _ in range(3)]

        assert handles == ["sales-bot", "sales-bot-2", "sales-bot-3"]
        assert len(_agents(graph)) == 3

    @pytest.mark.asyncio
    async def test_the_first_attempt_does_not_read_the_directory(self) -> None:
        graph = InMemoryGraph()

        await _service(graph).create(ALICE, AgentSpec(name="Sales Bot"))

        assert graph.calls_to("search_agent_handles") == [] and graph.calls_to("get_agent_by_handle") == []

    @pytest.mark.asyncio
    async def test_reserved_name_is_suffixed(self) -> None:
        created = await _service(InMemoryGraph()).create(ALICE, AgentSpec(name="Assistant"))

        assert created.handle == "assistant-agent"

    @pytest.mark.asyncio
    async def test_other_org_may_reuse_a_handle(self) -> None:
        graph = InMemoryGraph()
        service = _service(graph)
        await service.create(ALICE, AgentSpec(name="Sales Bot"))

        other = await service.create(MALLORY, AgentSpec(name="Sales Bot"))

        assert other.handle == "sales-bot"

    @pytest.mark.asyncio
    async def test_lost_race_retries_with_the_next_suffix(self) -> None:
        graph = LosesRaceOnce()

        created = await _service(graph).create(ALICE, AgentSpec(name="Solo"))

        assert created.handle == "solo-2"
        mine = [a for a in _agents(graph).values() if a.get("name") == "Solo"]
        assert len(mine) == 1 and mine[0]["_key"] == created.agent_key
        assert sorted(a["handle"] for a in _agents(graph).values()) == ["solo", "solo-2"]

    @pytest.mark.asyncio
    async def test_a_rolled_back_attempt_leaves_no_partial_agent(self) -> None:
        graph = LosesRaceOnce()

        await _service(graph).create(ALICE, AgentSpec(name="Solo"))

        assert len(graph.rolled_back) == 1 and len(graph.committed) == 1
        assert len(graph.edges["permission"]) == 2  # the racer's owner edge + ours; nothing from the failed attempt

    @pytest.mark.asyncio
    async def test_gives_up_after_five_lost_races(self) -> None:
        graph = LosesRaceOnce(times=9)

        with pytest.raises(HTTPException) as exc:
            await _service(graph).create(ALICE, AgentSpec(name="Solo"))

        assert exc.value.status_code == 500
        assert len(graph.calls_to("begin_transaction")) == 5

    @pytest.mark.asyncio
    async def test_other_write_failures_are_not_retried(self) -> None:
        graph = InMemoryGraph()
        graph.fail("batch_upsert_nodes", RuntimeError("boom"))

        with pytest.raises(HTTPException) as exc:
            await _service(graph).create(ALICE, AgentSpec(name="Solo"))

        assert exc.value.status_code == 500
        assert len(graph.calls_to("begin_transaction")) == 1


class TestRequestedHandle:
    @pytest.mark.asyncio
    async def test_free_handle_is_used_as_given(self) -> None:
        created = await _service(InMemoryGraph()).create(ALICE, AgentSpec(name="Anything", handle="@my-bot"))

        assert created.handle == "my-bot"

    @pytest.mark.asyncio
    async def test_taken_handle_is_a_409_with_a_suggestion(self) -> None:
        graph = InMemoryGraph()
        service = _service(graph)
        await service.create(ALICE, AgentSpec(name="Sales Bot"))
        await service.create(ALICE, AgentSpec(name="Sales Bot"))

        with pytest.raises(HandleTakenError) as exc:
            await service.create(ALICE, AgentSpec(name="Other", handle="sales-bot"))

        assert exc.value.status_code == 409
        assert _detail(exc.value) == {
            "code": "HANDLE_TAKEN", "message": "The handle @sales-bot is already taken.", "suggestion": "sales-bot-3",
        }
        assert len(_agents(graph)) == 2

    @pytest.mark.asyncio
    async def test_explicit_handle_is_never_silently_renumbered(self) -> None:
        graph = LosesRaceOnce()

        with pytest.raises(HandleTakenError) as exc:
            await _service(graph).create(ALICE, AgentSpec(name="Mine", handle="mine"))

        assert _detail(exc.value)["suggestion"] == "mine-2"
        assert len(graph.calls_to("begin_transaction")) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handle", ["assistant", "pipeshub", "ai", "bot", "agent", "everyone", "here", "all"])
    async def test_reserved_handle_is_a_400(self, handle: str) -> None:
        graph = InMemoryGraph()

        with pytest.raises(HTTPException) as exc:
            await _service(graph).create(ALICE, AgentSpec(name="Anything", handle=handle))

        assert exc.value.status_code == 400 and _detail(exc.value)["code"] == "HANDLE_RESERVED"
        assert graph.calls_to("begin_transaction") == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handle", ["Bad Handle", "UPPER", "a", "x" * 41, "a_b", "é-bot"])
    async def test_malformed_handle_is_a_400(self, handle: str) -> None:
        with pytest.raises(HTTPException) as exc:
            await _service(InMemoryGraph()).create(ALICE, AgentSpec(name="Anything", handle=handle))

        assert exc.value.status_code == 400 and _detail(exc.value)["code"] == "HANDLE_INVALID"

    @pytest.mark.asyncio
    async def test_blank_handle_means_derive_from_the_name(self) -> None:
        created = await _service(InMemoryGraph()).create(ALICE, AgentSpec(name="Derived Name", handle="  "))

        assert created.handle == "derived-name"


class TestUpdateHandle:
    async def _owned(self, graph: InMemoryGraph) -> str:
        return (await _service(graph).create(ALICE, AgentSpec(name="First"))).agent_key

    @pytest.mark.asyncio
    async def test_owner_changes_the_handle(self) -> None:
        graph = InMemoryGraph()
        key = await self._owned(graph)

        await _service(graph).update(ALICE, key, AgentPatch.model_validate({"handle": "renamed", "tags": ["x"]}))

        assert _agents(graph)[key]["handle"] == "renamed"
        assert _agents(graph)[key]["tags"] == ["x"]

    @pytest.mark.asyncio
    async def test_taken_handle_is_a_409_and_saves_nothing_else(self) -> None:
        graph = InMemoryGraph()
        key = await self._owned(graph)
        await _service(graph).create(ALICE, AgentSpec(name="Second"))

        with pytest.raises(HandleTakenError) as exc:
            await _service(graph).update(ALICE, key, AgentPatch.model_validate({"handle": "second", "tags": ["x"]}))

        assert _detail(exc.value)["suggestion"] == "second-2"
        assert _agents(graph)[key]["handle"] == "first" and _agents(graph)[key].get("tags") != ["x"]
        assert graph.calls_to("update_agent") == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("handle", "code"), [("assistant", "HANDLE_RESERVED"), ("Bad Handle", "HANDLE_INVALID")])
    async def test_reserved_and_malformed_are_400(self, handle: str, code: str) -> None:
        graph = InMemoryGraph()
        key = await self._owned(graph)

        with pytest.raises(HTTPException) as exc:
            await _service(graph).update(ALICE, key, AgentPatch.model_validate({"handle": handle}))

        assert exc.value.status_code == 400 and _detail(exc.value)["code"] == code
        assert _agents(graph)[key]["handle"] == "first"

    @pytest.mark.asyncio
    async def test_unchanged_handle_writes_nothing(self) -> None:
        graph = InMemoryGraph()
        key = await self._owned(graph)

        await _service(graph).update(ALICE, key, AgentPatch.model_validate({"handle": "first"}))

        assert graph.calls_to("update_node") == []

    @pytest.mark.asyncio
    async def test_a_legacy_agent_gets_its_org_with_the_first_handle(self) -> None:
        graph = InMemoryGraph()
        graph.add_agent("legacy", "alice", name="Legacy")

        await _service(graph).update(ALICE, "legacy", AgentPatch.model_validate({"handle": "legacy-bot"}))

        assert (_agents(graph)["legacy"]["orgId"], _agents(graph)["legacy"]["handle"]) == ("org-1", "legacy-bot")

    @pytest.mark.asyncio
    async def test_a_non_owner_cannot_change_it(self) -> None:
        graph = InMemoryGraph()
        key = await self._owned(graph)

        with pytest.raises(HTTPException) as exc:
            await _service(graph).update(
                AgentActor(user_key=user_key("bob"), user_id="u-bob", org_id="org-1"), key,
                AgentPatch.model_validate({"handle": "stolen"}),
            )

        assert exc.value.status_code == 404
        assert _agents(graph)[key]["handle"] == "first"


def test_unique_violation_is_not_an_http_error() -> None:
    assert not issubclass(UniqueConstraintViolation, HTTPException)
