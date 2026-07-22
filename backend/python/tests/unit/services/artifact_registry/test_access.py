"""Tests for `app.services.artifact_registry.access.AccessPolicy` — the one
place every registry operation checks authorization."""

from __future__ import annotations

import pytest

from app.services.artifact_registry.access import (
    AccessDeniedError,
    AccessPolicy,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.models import Actor

from .fakes import FakeGraphProvider

ORG = "org-1"
USER = "user-1"


def _seed_record(graph: FakeGraphProvider, artifact_id: str, *, org_id: str = ORG) -> None:
    graph.nodes["records"][artifact_id] = {"_key": artifact_id, "orgId": org_id, "recordName": "report.pdf"}


class TestResolveUserKey:
    async def test_resolves_key_from_user_doc(self) -> None:
        graph = FakeGraphProvider()
        graph.add_user(USER, key="ukey-1")
        policy = AccessPolicy(graph)
        assert await policy.resolve_user_key(Actor(org_id=ORG, user_id=USER)) == "ukey-1"

    async def test_raises_not_found_for_unknown_user(self) -> None:
        graph = FakeGraphProvider()
        policy = AccessPolicy(graph)
        with pytest.raises(ArtifactNotFoundError):
            await policy.resolve_user_key(Actor(org_id=ORG, user_id="ghost"))


class TestAuthorizeReadWrite:
    async def test_authorizes_owner_with_permission_edge(self) -> None:
        graph = FakeGraphProvider()
        graph.add_user(USER, key="ukey-1")
        _seed_record(graph, "art-1")
        graph.edges["permission"].append({"from_id": "ukey-1", "to_id": "art-1", "role": "OWNER"})
        policy = AccessPolicy(graph)
        actor = Actor(org_id=ORG, user_id=USER)

        record = await policy.authorize_read(actor, "art-1")
        assert record["_key"] == "art-1"
        # Write authorization is identical to read today (no share model yet).
        record2 = await policy.authorize_write(actor, "art-1")
        assert record2 == record

    async def test_missing_record_raises_not_found(self) -> None:
        graph = FakeGraphProvider()
        graph.add_user(USER)
        policy = AccessPolicy(graph)
        with pytest.raises(ArtifactNotFoundError):
            await policy.authorize_read(Actor(org_id=ORG, user_id=USER), "does-not-exist")

    async def test_cross_org_record_raises_not_found_not_denied(self) -> None:
        """A cross-org probe must get the SAME error as a missing artifact
        — never a distinguishable "access denied", which would leak
        existence across tenants."""
        graph = FakeGraphProvider()
        graph.add_user(USER)
        _seed_record(graph, "art-1", org_id="other-org")
        policy = AccessPolicy(graph)
        with pytest.raises(ArtifactNotFoundError):
            await policy.authorize_read(Actor(org_id=ORG, user_id=USER), "art-1")

    async def test_no_permission_edge_raises_access_denied(self) -> None:
        graph = FakeGraphProvider()
        graph.add_user(USER, key="ukey-1")
        _seed_record(graph, "art-1")
        policy = AccessPolicy(graph)
        with pytest.raises(AccessDeniedError):
            await policy.authorize_read(Actor(org_id=ORG, user_id=USER), "art-1")

    async def test_permission_edge_for_different_user_does_not_authorize(self) -> None:
        graph = FakeGraphProvider()
        graph.add_user(USER, key="ukey-1")
        graph.add_user("other-user", key="ukey-2")
        _seed_record(graph, "art-1")
        graph.edges["permission"].append({"from_id": "ukey-2", "to_id": "art-1", "role": "OWNER"})
        policy = AccessPolicy(graph)
        with pytest.raises(AccessDeniedError):
            await policy.authorize_read(Actor(org_id=ORG, user_id=USER), "art-1")


class TestGrantOwnerPermission:
    async def test_builds_edge_without_persisting(self) -> None:
        graph = FakeGraphProvider()
        graph.add_user(USER, key="ukey-1")
        policy = AccessPolicy(graph)
        edge = await policy.grant_owner_permission(Actor(org_id=ORG, user_id=USER), "art-1", now=123)

        assert edge == {
            "from_id": "ukey-1", "from_collection": "users", "to_id": "art-1",
            "to_collection": "records", "type": "USER", "role": "OWNER",
            "createdAtTimestamp": 123, "updatedAtTimestamp": 123,
        }
        # Caller (VersionManager) is responsible for persisting — this
        # method must not have written anything itself.
        assert graph.edges["permission"] == []
