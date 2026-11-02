"""Who may inline a stored document: the graph's ``get_ref_access`` and the service's uniform denial."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.localKB.push.api import error_body
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.interface.graph_db_provider import (
    REF_LINKS_PER_CALL,
    REF_LINKS_PER_DOC,
    AccessCheck,
    IGraphDBProvider,
    RefAccess,
)

from .image_harness import PNG, USER, did, image_body, make_harness, ref

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

UPLOAD = {"kind": "externalRecordId", "origin": "UPLOAD", "connectorName": "KB", "connectorId": "kb1"}


class FakeProvider:
    """Just what ``get_ref_access`` calls; ``check_access`` answers like the real one (``graph_db_provider.py:7156``)."""

    def __init__(self, rows, readable=(), placeholders=()) -> None:
        self.rows, self.readable, self.placeholders = rows, set(readable), set(placeholders)
        self.check_calls: list[tuple] = []
        self.hint_calls = 0
        self.fail = False

    async def get_user_by_user_id(self, user_id, *, raise_on_error=False) -> object:
        return {"id": f"key-of-{user_id}", "userId": user_id}

    async def _ref_link_rows(self, ids) -> object:
        if self.fail:
            raise RuntimeError("down")
        return [r for r in self.rows if r["docId"] in ids]

    async def _ref_access_hint(self, user_key, org_id, connector_ids) -> object:
        self.hint_calls += 1
        return {"probe": sorted(connector_ids)}

    async def check_access(self, user_key, org_id, *, node_ids=(), access=None, **kw) -> object:
        self.check_calls.append((user_key, org_id, list(node_ids), access))
        ok = {n for n in node_ids if n in self.readable}
        return AccessCheck(node_ids=frozenset(ok), placeholder_ids=frozenset(ok & self.placeholders))


async def access(provider, ids) -> object:
    return await IGraphDBProvider.get_ref_access(provider, USER, "org1", ids)


def row(doc, rec, **over) -> object:
    return {"docId": doc, "recordId": rec, **UPLOAD, **over}


class TestGraphAccess:
    async def test_readable_linking_record_grants(self) -> None:
        p = FakeProvider([row("d1", "r1")], readable={"r1"})
        assert await access(p, ["d1"]) == RefAccess(frozenset({"d1"}), frozenset({"d1"}))

    async def test_linked_but_unreadable_is_linked_not_readable(self) -> None:
        p = FakeProvider([row("d1", "r1")])
        assert await access(p, ["d1"]) == RefAccess(frozenset({"d1"}), frozenset())

    async def test_unlinked_is_neither(self) -> None:
        assert await access(FakeProvider([]), ["d1"]) == RefAccess()

    async def test_own_upload_uses_user_id_graph_uses_user_key(self) -> None:
        p = FakeProvider([row("d1", "r1")], readable={"r1"})
        await access(p, ["d1"])
        assert p.check_calls[0][0] == f"key-of-{USER}" and p.check_calls[0][0] != USER

    @pytest.mark.parametrize("over", [{"origin": "CONNECTOR"}, {"connectorName": "SLACK"}, {"kind": "files.path"}], ids=["connector-origin", "other-connector", "files.path"])
    async def test_forged_link_never_grants(self, over) -> None:
        """A readable record that a connector (or a client-reported path) pointed at a victim's document links it, never grants it."""
        p = FakeProvider([row("d1", "r1", **over)], readable={"r1"})
        got = await access(p, ["d1"])
        assert got.linked == {"d1"} and got.readable == frozenset() and p.check_calls == []

    async def test_placeholder_record_does_not_grant(self) -> None:
        p = FakeProvider([row("d1", "r1")], readable={"r1"}, placeholders={"r1"})
        assert (await access(p, ["d1"])).readable == frozenset()

    async def test_linking_record_caps_and_one_check_access_call(self) -> None:
        rows = [row(f"d{i}", f"r{i}-{j:03d}") for i in range(40) for j in range(20)]  # 20 linking records per document
        p = FakeProvider(rows, readable={r["recordId"] for r in rows})
        got = await access(p, [f"d{i}" for i in range(40)])
        assert len(p.check_calls) == 1
        asked = p.check_calls[0][2]
        assert len(asked) == REF_LINKS_PER_CALL and all(sum(1 for a in asked if a.startswith(f"r{i}-")) <= REF_LINKS_PER_DOC for i in range(40))
        assert asked[: REF_LINKS_PER_DOC] == sorted(asked[: REF_LINKS_PER_DOC]) and got.linked  # sorted by id, deterministic

    async def test_a_user_with_1000_grants_costs_one_call_with_the_single_connector_probe(self) -> None:
        p = FakeProvider([row("d1", "r1")], readable={"r1"})
        await access(p, ["d1"])
        assert len(p.check_calls) == 1 and p.hint_calls == 1 and p.check_calls[0][3] == {"probe": ["kb1"]}

    async def test_two_connectors_skip_the_probe(self) -> None:
        p = FakeProvider([row("d1", "r1"), row("d2", "r2", connectorId="kb2")], readable={"r1", "r2"})
        await access(p, ["d1", "d2"])
        assert p.hint_calls == 0 and p.check_calls[0][3] is None

    async def test_store_failure_is_an_outage_not_a_denial(self) -> None:
        p = FakeProvider([row("d1", "r1")])
        p.fail = True
        with pytest.raises(PermissionVerificationUnavailableError):
            await access(p, ["d1"])

    async def test_user_lookup_failure_is_an_outage_not_a_raw_driver_error(self) -> None:
        p = FakeProvider([row("d1", "r1")], readable={"r1"})
        p.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("driver down"))
        with pytest.raises(PermissionVerificationUnavailableError):
            await access(p, ["d1"])

    async def test_access_hint_failure_is_an_outage_not_a_raw_driver_error(self) -> None:
        p = FakeProvider([row("d1", "r1")], readable={"r1"})
        p._ref_access_hint = AsyncMock(side_effect=RuntimeError("driver down"))
        with pytest.raises(PermissionVerificationUnavailableError):
            await access(p, ["d1"])
        assert p.check_calls == []

    async def test_unknown_user_reads_nothing(self) -> None:
        p = FakeProvider([row("d1", "r1")], readable={"r1"})
        p.get_user_by_user_id = AsyncMock(return_value=None)
        assert (await access(p, ["d1"])).readable == frozenset()


@pytest.fixture
async def h(tmp_path) -> AsyncIterator[object]:
    harness = await make_harness(tmp_path)
    yield harness
    await harness.store.stop()


async def refusal(h, body=None) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        await h.push(body or image_body([ref(1)]))
    return e.value


class TestServiceDecision:
    async def test_own_unlinked_upload_accepted(self, h) -> None:
        h.docs.put(1, PNG, initiator=USER)
        assert (await h.push(image_body([ref(1)]))).status == "created"

    async def test_other_users_unlinked_upload_denied(self, h) -> None:
        h.docs.put(1, PNG, initiator="someone-else")
        assert (await refusal(h)).reason == "IMAGE_REF_INVALID"

    async def test_service_token_upload_with_no_initiator_is_denied(self, h) -> None:
        h.docs.put(1, PNG, initiator=None)
        assert (await refusal(h)).reason == "IMAGE_REF_INVALID"

    async def test_linked_document_never_falls_through_to_own_upload(self, h) -> None:
        h.docs.put(1, PNG, initiator=USER)
        h.graph.access = RefAccess(frozenset({did(1)}), frozenset())
        assert (await refusal(h)).reason == "IMAGE_REF_INVALID"

    async def test_linked_to_a_readable_record_is_accepted_whoever_uploaded_it(self, h) -> None:
        h.docs.put(1, PNG, initiator="someone-else")
        h.graph.access = RefAccess(frozenset({did(1)}), frozenset({did(1)}))
        assert (await h.push(image_body([ref(1)]))).status == "created"

    async def test_every_ref_gets_one_info_and_one_graph_batch_in_every_cell(self, h) -> None:
        h.docs.put(1, PNG, initiator=USER)
        h.docs.put(2, PNG, initiator="other")       # foreign unlinked
        h.docs.put(3, PNG, initiator="other")       # linked, unreadable
        h.graph.access = RefAccess(frozenset({did(3)}), frozenset())
        # ref 4 is absent. Every cell is denied; the call pattern is the same for each.
        for refs in ([1], [2], [3], [4], [1, 2, 3, 4]):
            h.docs.info_calls.clear(), h.graph.access_calls.clear()
            body = image_body([ref(n) for n in refs])
            if refs == [1]:
                h.graph.access = RefAccess()
                await h.push(body)
                h.graph.access = RefAccess(frozenset({did(3)}), frozenset())
            else:
                await refusal(h, body)
            ids = [did(n) for n in refs]
            expected = 2 if refs == [1] else 1  # an accepted create reads once more for the re-check before its claim
            assert h.docs.info_calls == [ids] * expected and h.graph.access_calls == [ids] * expected

    async def test_same_id_16_times_is_one_info_one_download(self, h) -> None:
        h.docs.put(1, PNG)
        await h.push(image_body([ref(1)] * 16))
        assert h.docs.info_calls[0] == [did(1)] and h.store.count("download") == 1

    @pytest.mark.parametrize("cause", ["absent", "deleted", "retired", "awaiting", "artifact_set", "non_image", "over_20mib", "foreign", "linked_unreadable"])
    async def test_denials_are_byte_identical(self, h, cause) -> None:
        h.docs.put(1, PNG)
        doc = h.docs.docs[did(1)]
        if cause == "absent":
            del h.docs.docs[did(1)]
        elif cause == "deleted":
            doc.deleted = True
        elif cause == "retired":
            doc.retired = True
        elif cause == "awaiting":
            doc.awaiting = True
        elif cause == "artifact_set":
            doc.artifact_set = True
        elif cause == "non_image":
            doc.ext = ".pdf"
        elif cause == "over_20mib":
            doc.data = b"x" * ((20 << 20) + 1)
        elif cause == "foreign":
            doc.initiator = "other"
        else:
            h.graph.access = RefAccess(frozenset({did(1)}), frozenset())
        first = error_body(await refusal(h))
        assert first["error"]["reason"] == "IMAGE_REF_INVALID" and "location" not in first["error"]
        again = error_body(await refusal(h, image_body([ref(1)], text="other text")))
        assert again == first
        assert first == error_body(await _foreign(h))

    async def test_foreign_awaiting_document_unchanged_after_a_denied_push(self, h) -> None:
        """The verified read may adopt a finished direct upload in storage; a denied push must leave another user's document as it was (F11)."""
        h.docs.put(1, PNG, initiator="other")
        doc = h.docs.docs[did(1)]
        doc.awaiting = True
        before = (doc.data, doc.awaiting, doc.initiator, doc.deleted, doc.retired)
        await refusal(h)
        assert (doc.data, doc.awaiting, doc.initiator, doc.deleted, doc.retired) == before
        assert h.docs.uploads == [] and h.store.count("download") == 0

    async def test_decision_waits_for_both_calls(self, h) -> None:
        """Info answers 'absent' at once; a refusal must still wait for the slow graph call (no early exit, no timing signal)."""
        finished = []
        orig = h.graph.get_ref_access

        async def slow(*a) -> object:
            await asyncio.sleep(0.2)
            finished.append("graph")
            return await orig(*a)

        h.graph.get_ref_access = slow
        await refusal(h, image_body([ref(9)]))  # absent from storage
        assert finished == ["graph"]

    async def test_denied_push_stores_nothing_and_takes_no_download(self, h) -> None:
        h.docs.put(1, PNG, initiator="other")
        await refusal(h)
        assert h.docs.uploads == [] and h.store.count("download") == 0 and h.mat.runs == 0 and h.admission._taken == 0

    async def test_task_payload_ids_are_vetted(self, h) -> None:
        h.docs.put(1, PNG)
        await h.push(image_body([ref(1)]))
        assert all(len(r["id"]) == 24 for r in h.mat.tasks[0]["refs"]) and h.mat.tasks[0]["token"] == "token"


async def _foreign(h) -> PushRefusal:
    h.docs.docs.clear()
    return await refusal(h)
