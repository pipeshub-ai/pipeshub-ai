"""Unit tests for ArtifactSharingService: who may share, what each share writes,
and that a direct share and a chat share never undo each other."""

from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.artifact_registry.access import (
    ARTIFACT_SHARE_GRANT,
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.sharing import (
    MAX_SHARE_TARGETS,
    ArtifactSharingService,
    InvalidShareTargetError,
)
from app.services.notification.types import NotificationType

ARTIFACT_ID = "art-1"


def _user(user_id: str, org_id: str = "org-1", **extra: str) -> dict:
    return {"_key": f"key-{user_id}", "userId": user_id, "orgId": org_id, **extra}


class FakeGraph:
    """Users by auth userId, permission edges by (user_key, record_id)."""

    def __init__(self, edges: dict | None = None, artifact: dict | None = None, users: dict | None = None) -> None:
        self.users = users or {
            "owner": _user("owner", fullName="Ada Owner"),
            "bob": _user("bob"),
            "carol": _user("carol"),
            "mallory": _user("mallory", org_id="org-2"),
        }
        self.edges = {
            ("key-owner", ARTIFACT_ID): {"role": "OWNER", "type": "USER", "createdAtTimestamp": 1},
            **(edges or {}),
        }
        self.artifact = artifact if artifact is not None else {"name": "report.pdf", "artifactType": "DOCUMENT"}
        self.created = []
        self.deleted = []

    async def get_document(self, key, collection, transaction=None, *, raise_on_error=False) -> dict | None:
        if collection == CollectionNames.RECORDS.value and key == ARTIFACT_ID:
            return {"_key": key, "orgId": "org-1", "recordName": "report.pdf"}
        if collection == CollectionNames.ARTIFACTS.value and key == ARTIFACT_ID:
            return self.artifact
        if collection == CollectionNames.USERS.value:
            return next((u for u in self.users.values() if u["_key"] == key), None)
        return None

    async def get_user_by_user_id(self, user_id, *, raise_on_error=False) -> dict | None:
        return self.users.get(user_id)

    async def get_edge(self, from_id, from_collection, to_id, to_collection, collection,
                       transaction=None, *, raise_on_error=False) -> dict | None:
        return self.edges.get((from_id, to_id))

    async def get_edges_to_node(self, node_id, edge_collection, transaction=None, *, raise_on_error=False) -> list[dict]:
        record_id = node_id.split("/", 1)[1]
        return [
            {"_from": f"users/{user_key}", "_to": node_id, **props}
            for (user_key, rid), props in self.edges.items()
            if rid == record_id
        ]

    async def batch_create_edges(self, edges, collection, transaction=None) -> bool:
        self.created.extend(edges)
        for edge in edges:
            self.edges[(edge["from_id"], edge["to_id"])] = {
                k: v for k, v in edge.items() if not k.startswith(("from_", "to_"))
            }
        return True

    async def batch_delete_edges(self, edges, collection, transaction=None) -> int:
        self.deleted.extend(edges)
        for edge in edges:
            self.edges.pop((edge["from_id"], edge["to_id"]), None)
        return len(edges)


def _actor(user_id: str = "owner") -> Actor:
    return Actor(org_id="org-1", user_id=user_id)


def _service(graph: FakeGraph, notifications: AsyncMock | None = None) -> ArtifactSharingService:
    return ArtifactSharingService(graph, notifications)


class TestShare:
    @pytest.mark.asyncio
    async def test_the_owner_shares_and_each_new_reader_is_notified(self) -> None:
        graph = FakeGraph()
        notifications = AsyncMock()

        shared = await _service(graph, notifications).share(_actor(), ARTIFACT_ID, ["bob", "carol"])

        assert shared == 2
        for user_key in ("key-bob", "key-carol"):
            edge = graph.edges[(user_key, ARTIFACT_ID)]
            assert edge["role"] == "READER"
            assert edge["type"] == "USER"
            assert edge["grantedVia"] == ARTIFACT_SHARE_GRANT
        kwargs = notifications.publish_notification.await_args.kwargs
        assert kwargs["recipient_user_ids"] == ["bob", "carol"]
        assert kwargs["type"] is NotificationType.ARTIFACT_SHARED
        assert kwargs["org_id"] == "org-1"
        assert kwargs["title"] == "Ada Owner shared an artifact with you"
        assert kwargs["message"] == "report.pdf"
        assert kwargs["redirect_link"] == "/artifacts/?tab=shared&artifactId=art-1"

    @pytest.mark.asyncio
    async def test_a_reader_cannot_share_onward(self) -> None:
        graph = FakeGraph(edges={("key-bob", ARTIFACT_ID): {"role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT}})
        with pytest.raises(AccessDeniedError):
            await _service(graph).share(_actor("bob"), ARTIFACT_ID, ["carol"])
        assert graph.created == []

    @pytest.mark.asyncio
    async def test_a_user_without_access_cannot_share(self) -> None:
        with pytest.raises(AccessDeniedError):
            await _service(FakeGraph()).share(_actor("bob"), ARTIFACT_ID, ["carol"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["mallory", "nobody"])
    async def test_a_target_outside_the_org_fails_the_whole_request(self, target) -> None:
        graph = FakeGraph()
        notifications = AsyncMock()
        with pytest.raises(InvalidShareTargetError):
            await _service(graph, notifications).share(_actor(), ARTIFACT_ID, ["bob", target])
        assert graph.created == []
        notifications.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_owner_and_existing_direct_readers_are_left_alone(self) -> None:
        graph = FakeGraph(edges={("key-bob", ARTIFACT_ID): {"role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT}})
        notifications = AsyncMock()

        shared = await _service(graph, notifications).share(_actor(), ARTIFACT_ID, ["owner", "bob", "bob"])

        assert shared == 0
        assert graph.created == []
        assert graph.edges[("key-owner", ARTIFACT_ID)]["role"] == "OWNER"
        notifications.publish_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_reader_from_a_shared_chat_is_tagged_so_the_share_outlives_the_chat(self) -> None:
        graph = FakeGraph(edges={("key-bob", ARTIFACT_ID): {"role": "READER", "type": "USER", "createdAtTimestamp": 7}})

        shared = await _service(graph, AsyncMock()).share(_actor(), ARTIFACT_ID, ["bob"])

        assert shared == 1
        edge = graph.edges[("key-bob", ARTIFACT_ID)]
        assert edge["grantedVia"] == ARTIFACT_SHARE_GRANT
        assert edge["chatGranted"] is True
        assert edge["createdAtTimestamp"] == 7

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "artifact",
        [
            {},
            {"name": "x", "artifactType": "TOOL_RESULT"},
            {"name": "x", "artifactType": "DOCUMENT", "isTemporary": True},
            {"name": "x", "artifactType": "DOCUMENT", "visibility": "HIDDEN"},
        ],
    )
    async def test_an_artifact_the_gallery_hides_cannot_be_shared(self, artifact) -> None:
        graph = FakeGraph(artifact=artifact)
        with pytest.raises(ArtifactNotFoundError):
            await _service(graph).share(_actor(), ARTIFACT_ID, ["bob"])
        assert graph.created == []

    @pytest.mark.asyncio
    async def test_too_many_targets_are_refused(self) -> None:
        users = {f"u{i}": _user(f"u{i}") for i in range(MAX_SHARE_TARGETS + 1)}
        graph = FakeGraph(users={"owner": _user("owner"), **users})
        with pytest.raises(InvalidShareTargetError):
            await _service(graph).share(_actor(), ARTIFACT_ID, list(users))

    @pytest.mark.asyncio
    async def test_a_graph_failure_is_raised_not_reported_as_an_unknown_user(self) -> None:
        graph = FakeGraph()
        lookups = graph.get_user_by_user_id

        async def failing_lookup(user_id, *, raise_on_error=False) -> dict | None:
            if user_id == "bob":
                assert raise_on_error
                raise RuntimeError("graph down")
            return await lookups(user_id)

        graph.get_user_by_user_id = failing_lookup
        with pytest.raises(RuntimeError, match="graph down"):
            await _service(graph).share(_actor(), ARTIFACT_ID, ["bob"])


class TestUnshare:
    @pytest.mark.asyncio
    async def test_only_direct_shares_are_removed(self) -> None:
        graph = FakeGraph(
            edges={
                ("key-bob", ARTIFACT_ID): {"role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT},
                ("key-carol", ARTIFACT_ID): {"role": "READER"},
            }
        )

        unshared = await _service(graph).unshare(_actor(), ARTIFACT_ID, ["bob", "carol", "owner", "nobody"])

        assert unshared == 1
        assert [(e["from_id"], e["to_id"]) for e in graph.deleted] == [("key-bob", ARTIFACT_ID)]
        assert ("key-carol", ARTIFACT_ID) in graph.edges
        assert graph.edges[("key-owner", ARTIFACT_ID)]["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_a_reader_cannot_unshare(self) -> None:
        graph = FakeGraph(edges={("key-bob", ARTIFACT_ID): {"role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT}})
        with pytest.raises(AccessDeniedError):
            await _service(graph).unshare(_actor("bob"), ARTIFACT_ID, ["bob"])
        assert graph.deleted == []


class TestMembers:
    @pytest.mark.asyncio
    async def test_lists_the_owner_and_direct_shares_but_not_chat_readers_or_groups(self) -> None:
        graph = FakeGraph(
            edges={
                ("key-bob", ARTIFACT_ID): {"role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT},
                ("key-carol", ARTIFACT_ID): {"role": "READER"},
            }
        )
        edges_for = graph.get_edges_to_node

        async def with_group_edge(node_id, edge_collection, transaction=None, *, raise_on_error=False) -> list[dict]:
            assert raise_on_error
            return [*await edges_for(node_id, edge_collection), {"_from": "groups/g1", "role": "OWNER"}]

        graph.get_edges_to_node = with_group_edge

        members = await _service(graph).list_members(_actor(), ARTIFACT_ID)

        assert members == [{"userId": "owner", "role": "OWNER"}, {"userId": "bob", "role": "READER"}]

    @pytest.mark.asyncio
    async def test_reads_neo4j_shaped_edges(self) -> None:
        graph = FakeGraph()

        async def neo4j_edges(node_id, edge_collection, transaction=None, *, raise_on_error=False) -> list[dict]:
            return [
                {"from_id": "key-owner", "from_collection": "users", "role": "OWNER"},
                {"from_id": "key-bob", "from_collection": "users", "role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT},
                {"from_id": "t1", "from_collection": "teams", "role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT},
            ]

        graph.get_edges_to_node = neo4j_edges

        members = await _service(graph).list_members(_actor(), ARTIFACT_ID)

        assert members == [{"userId": "owner", "role": "OWNER"}, {"userId": "bob", "role": "READER"}]

    @pytest.mark.asyncio
    async def test_only_the_owner_can_see_the_members(self) -> None:
        graph = FakeGraph(edges={("key-bob", ARTIFACT_ID): {"role": "READER", "grantedVia": ARTIFACT_SHARE_GRANT}})
        with pytest.raises(AccessDeniedError):
            await _service(graph).list_members(_actor("bob"), ARTIFACT_ID)


class TestDirectShareAndChatShareTogether:
    """A shared chat and a direct share can both cover the same reader. They share
    one READER edge, so taking either grant away must leave the other in place."""

    @staticmethod
    async def _apply(graph: FakeGraph, kind: str, action: str) -> None:
        from app.api.routes.chatbot import (
            _grant_reader_permissions,
            _revoke_reader_permissions,
        )

        if kind == "chat":
            grant = _grant_reader_permissions if action == "share" else _revoke_reader_permissions
            await grant(graph, "org-1", "owner", ["bob"], [ARTIFACT_ID])
            return
        service = _service(graph, AsyncMock())
        if action == "share":
            await service.share(_actor(), ARTIFACT_ID, ["bob"])
        else:
            await service.unshare(_actor(), ARTIFACT_ID, ["bob"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "steps",
        [
            pytest.param(["chat share", "direct share", "direct unshare", "chat unshare"], id="chat-first-direct-removed-first"),
            pytest.param(["chat share", "direct share", "chat unshare", "direct unshare"], id="chat-first-chat-removed-first"),
            pytest.param(["direct share", "chat share", "direct unshare", "chat unshare"], id="direct-first-direct-removed-first"),
            pytest.param(["direct share", "chat share", "chat unshare", "direct unshare"], id="direct-first-chat-removed-first"),
        ],
    )
    async def test_the_reader_keeps_access_until_both_grants_are_gone(self, steps: list[str]) -> None:
        graph = FakeGraph()
        can_read = []
        for step in steps:
            kind, action = step.split()
            await self._apply(graph, kind, action)
            can_read.append(("key-bob", ARTIFACT_ID) in graph.edges)
        assert can_read == [True, True, True, False]
        assert graph.edges[("key-owner", ARTIFACT_ID)]["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_the_member_list_shows_only_a_live_direct_share(self) -> None:
        graph = FakeGraph()
        await self._apply(graph, "chat", "share")
        await self._apply(graph, "direct", "share")
        members = await _service(graph).list_members(_actor(), ARTIFACT_ID)
        assert {"userId": "bob", "role": "READER"} in members

        await self._apply(graph, "direct", "unshare")
        members = await _service(graph).list_members(_actor(), ARTIFACT_ID)
        assert members == [{"userId": "owner", "role": "OWNER"}]

    @pytest.mark.asyncio
    async def test_opening_a_shared_chat_again_writes_nothing_new(self) -> None:
        graph = FakeGraph()
        await self._apply(graph, "direct", "share")
        await self._apply(graph, "chat", "share")
        writes = len(graph.created)
        await self._apply(graph, "chat", "share")
        assert len(graph.created) == writes
