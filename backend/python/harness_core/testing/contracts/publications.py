"""Contract suite for ``PublicationDirectory`` (P2.13, D21).

Provide a ``directory`` fixture: a fresh directory under test. Org ids are unique per test, so the suite also runs
against a shared real store. A host whose containers must exist (a graph edge needs both ends) overrides
``containers`` with three container ids it has created.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.knowledge.publications import Audience, PublicationDraft

if TYPE_CHECKING:
    from harness_core.knowledge.ports import PublicationDirectory

NOW = 1_800_000_000_000


def _org() -> str:
    return f"org-pd-{uuid.uuid4().hex[:10]}"


def _draft(org: str, container: str, **overrides: object) -> PublicationDraft:
    fields: dict[str, object] = {
        "org_id": org,
        "container_id": container,
        "audience": Audience(user_ids=frozenset({"u-alice"}), group_ids=frozenset({"t-hr"})),
        "mode": "answer_only",
        "created_by": "u-owner",
        "expires_at_ms": NOW + 60_000,
    }
    fields.update(overrides)
    return PublicationDraft(**fields)  # type: ignore[arg-type]


def _id() -> str:
    return uuid.uuid4().hex


class PublicationDirectoryContract:
    @pytest.fixture
    def directory(self) -> PublicationDirectory:
        raise NotImplementedError("provide a directory fixture")

    @pytest.fixture
    def containers(self) -> tuple[str, str, str]:
        return ("kb-1", "kb-2", "kb-3")

    async def test_pd1_a_created_publication_reads_back_as_written(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1 = containers[0]
        org = _org()
        pid = _id()
        created = await directory.create(_draft(org, c1, audience=Audience(user_ids=frozenset({"u-a", "u-b"}), org=True)), publication_id=pid, now_ms=NOW)
        assert (created.id, created.org_id, created.container_id, created.mode) == (pid, org, c1, "answer_only")
        read = await directory.get(org, pid)
        assert read == created
        assert read is not None
        assert read.audience == Audience(user_ids=frozenset({"u-a", "u-b"}), org=True)
        assert (read.created_by, read.created_at_ms, read.expires_at_ms, read.revoked_at_ms) == ("u-owner", NOW, NOW + 60_000, None)

    async def test_pd1b_no_expiry_and_full_mode_round_trip(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1 = containers[0]
        org = _org()
        pid = _id()
        _ = await directory.create(_draft(org, c1, mode="full", expires_at_ms=None), publication_id=pid, now_ms=NOW)
        read = await directory.get(org, pid)
        assert read is not None
        assert (read.mode, read.expires_at_ms) == ("full", None)

    async def test_pd2_list_by_container(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1, c2, c3 = containers
        org = _org()
        a, b, c = sorted(_id() for _ in range(3))
        _ = await directory.create(_draft(org, c1), publication_id=a, now_ms=NOW)
        _ = await directory.create(_draft(org, c1, mode="full"), publication_id=b, now_ms=NOW)
        _ = await directory.create(_draft(org, c2), publication_id=c, now_ms=NOW)
        assert [p.id for p in await directory.list_for_container(org, c1)] == [a, b]
        assert [p.id for p in await directory.list_for_container(org, c3)] == []

    async def test_pd3_list_by_org(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1, c2, _ = containers
        org = _org()
        ids = sorted(_id() for _ in range(2))
        _ = await directory.create(_draft(org, c1), publication_id=ids[0], now_ms=NOW)
        _ = await directory.create(_draft(org, c2), publication_id=ids[1], now_ms=NOW)
        assert [p.id for p in await directory.list_for_org(org)] == ids

    async def test_pd4_revoke_keeps_the_publication_and_the_first_revocation(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1 = containers[0]
        org = _org()
        pid = _id()
        _ = await directory.create(_draft(org, c1), publication_id=pid, now_ms=NOW)
        revoked = await directory.revoke(org, pid, by="u-owner", now_ms=NOW + 5)
        assert revoked is not None
        assert (revoked.revoked_at_ms, revoked.revoked_by) == (NOW + 5, "u-owner")
        again = await directory.revoke(org, pid, by="u-admin", now_ms=NOW + 9)
        assert again is not None
        assert (again.revoked_at_ms, again.revoked_by) == (NOW + 5, "u-owner")
        kept = await directory.get(org, pid)
        assert kept is not None
        assert kept.revoked_at_ms == NOW + 5
        assert await directory.revoke(org, _id(), by="u-owner", now_ms=NOW) is None

    async def test_pd5_erase_container(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1, c2, _ = containers
        org = _org()
        a, b = _id(), _id()
        _ = await directory.create(_draft(org, c1), publication_id=a, now_ms=NOW)
        _ = await directory.create(_draft(org, c2), publication_id=b, now_ms=NOW)
        assert await directory.erase_container(org, c1) == 1
        assert await directory.get(org, a) is None
        assert await directory.get(org, b) is not None
        assert await directory.erase_container(org, c1) == 0

    async def test_pd6_erase_org(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1, c2, _ = containers
        org, other = _org(), _org()
        a, b, c = _id(), _id(), _id()
        _ = await directory.create(_draft(org, c1), publication_id=a, now_ms=NOW)
        _ = await directory.create(_draft(org, c2), publication_id=b, now_ms=NOW)
        _ = await directory.create(_draft(other, c1), publication_id=c, now_ms=NOW)
        assert await directory.erase_org(org) == 2
        assert await directory.list_for_org(org) == []
        assert [p.id for p in await directory.list_for_org(other)] == [c]

    async def test_pd7_another_org_never_reads_revokes_or_erases(self, directory: PublicationDirectory, containers: tuple[str, str, str]) -> None:
        c1 = containers[0]
        org, other = _org(), _org()
        pid = _id()
        _ = await directory.create(_draft(org, c1), publication_id=pid, now_ms=NOW)
        assert await directory.get(other, pid) is None
        assert await directory.revoke(other, pid, by="u-x", now_ms=NOW) is None
        assert await directory.list_for_container(other, c1) == []
        assert await directory.erase_container(other, c1) == 0
        kept = await directory.get(org, pid)
        assert kept is not None
        assert kept.revoked_at_ms is None


__all__ = ["PublicationDirectoryContract"]
