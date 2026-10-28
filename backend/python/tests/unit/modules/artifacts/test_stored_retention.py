"""The retirement sweep and the stray pass of the stored-write protocol, against in-memory fakes of the
graph and the storage service."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.modules.artifacts.stored_retention import (
    RETIRE_GRACE_MS,
    STRAY_GRACE_SECONDS,
    StoredRecordRetention,
)
from app.services.messaging.membership import MembershipVerdict

NOW = 10_000_000_000
GR = RETIRE_GRACE_MS
ORG = "org-1"
OK = MembershipVerdict(True)


class FakeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.cas: list[tuple[dict, dict]] = []

    def add(self, vrid: str, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
        node = {
            "id": vrid, "orgId": ORG, "documentId": "d-mapped", "switchDoc": "d-mapped", "switchSeq": 5,
            "updatedAt": 1, "switchUpdatedAt": 1, "lastSwitchAt": NOW - 10 * GR, "retired": [], **fields,
        }
        self.nodes[vrid] = node
        return node

    async def get_switch_pointers(self, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        keys = sorted(k for k in self.nodes if after_key is None or k > after_key)[:limit]
        return [{
            "virtualRecordId": k, "orgId": self.nodes[k]["orgId"], "documentId": self.nodes[k].get("documentId"),
            "switchSeq": self.nodes[k].get("switchSeq"), "lastSwitchAt": self.nodes[k].get("lastSwitchAt"),
            "retired": list(self.nodes[k].get("retired") or []),
        } for k in keys]

    async def get_document(self, key: str, collection: str, **_: Any) -> dict[str, Any] | None:  # noqa: ANN401
        node = self.nodes.get(key)
        return None if node is None else {**node, "retired": list(node.get("retired") or [])}

    async def update_node_fields_if_match(self, key: str, collection: str, updates: dict, expected: dict, *_: Any) -> bool:  # noqa: ANN401
        node = self.nodes.get(key)
        self.cas.append((updates, expected))
        if node is None or any(node.get(k) != v for k, v in expected.items()):
            return False
        node.update(updates)
        return True


class FakeBlob:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.purged: list[str] = []
        self.retire_outcome = "marked"
        self.marks: list[str] = []
        self.listing: list[dict[str, Any]] = []
        self.listed_with: list[int | None] = []

    async def _get_document_row(self, org_id: str, document_id: str) -> dict[str, Any] | None:
        row = self.rows.get(document_id)
        return None if row is None else dict(row)

    async def _retire_document(self, org_id: str, document_id: str) -> str:
        self.marks.append(document_id)
        if self.retire_outcome == "marked":
            self.rows[document_id]["retiredAt"] = NOW
        return self.retire_outcome

    async def purge_document(self, org_id: str, document_id: str) -> int:
        self.purged.append(document_id)
        self.rows.pop(document_id, None)
        return 1

    async def list_record_documents(self, org_id: str, vrid: str, *, older_than_seconds: int | None = None) -> list[dict[str, Any]]:
        self.listed_with.append(older_than_seconds)
        return list(self.listing)


def _sweep(graph: FakeGraph, blob: FakeBlob, verdict: MembershipVerdict = OK, **kw: Any) -> StoredRecordRetention:  # noqa: ANN401
    async def membership() -> MembershipVerdict:
        return verdict

    return StoredRecordRetention(graph, blob, logging.getLogger("t"), membership=membership, now_ms=lambda: NOW, **kw)  # type: ignore[arg-type]


def _retired(doc: str, age: int) -> dict[str, Any]:
    return {"doc": doc, "rev": "r", "sidecar": None, "at": NOW - age}


class TestRetirementSweep:
    @pytest.mark.asyncio
    async def test_a_marked_document_is_purged_only_once_the_mark_and_the_entry_are_a_grace_old(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-old", 2 * GR), _retired("d-young", 2 * GR), _retired("d-new", GR // 2)])
        blob.rows = {
            "d-old": {"retiredAt": NOW - 2 * GR}, "d-young": {"retiredAt": NOW - GR // 2}, "d-new": {"retiredAt": NOW - 2 * GR},
        }

        _, report = await _sweep(graph, blob).run_page(None)

        assert blob.purged == ["d-old"] and report.purged == 1
        assert [e["doc"] for e in graph.nodes["v1"]["retired"]] == ["d-young", "d-new"]

    @pytest.mark.asyncio
    async def test_a_mark_that_failed_at_the_switch_is_applied_by_the_sweep_and_not_purged_on_that_visit(self) -> None:
        """T47. Control: a sweep that purged on the same visit would remove a document an older writer's
        buffer PUT could still reach."""
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-old", 5 * GR)])
        blob.rows = {"d-old": {"retiredAt": None}}

        _, first = await _sweep(graph, blob).run_page(None)

        assert blob.marks == ["d-old"] and blob.purged == [] and first.marked == 1
        assert [e["doc"] for e in graph.nodes["v1"]["retired"]] == ["d-old"]
        blob.rows["d-old"]["retiredAt"] = NOW - 2 * GR  # a grace later, by Node's clock
        _, second = await _sweep(graph, blob).run_page(None)
        assert blob.purged == ["d-old"] and second.purged == 1

    @pytest.mark.asyncio
    async def test_without_a_retire_route_the_entry_is_held_and_nothing_is_purged(self) -> None:
        """T48."""
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-old", 5 * GR)])
        blob.rows = {"d-old": {"retiredAt": None}}
        blob.retire_outcome = "unavailable"

        _, report = await _sweep(graph, blob).run_page(None)

        assert blob.purged == [] and report.held_no_fence == 1
        assert len(graph.nodes["v1"]["retired"]) == 1

    @pytest.mark.asyncio
    async def test_the_mapped_document_is_never_purged(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-mapped", 5 * GR)])
        blob.rows = {"d-mapped": {"retiredAt": NOW - 5 * GR}}

        await _sweep(graph, blob).run_page(None)

        assert blob.purged == [] and graph.nodes["v1"]["retired"] == []

    @pytest.mark.asyncio
    async def test_an_entry_whose_document_is_gone_is_dropped(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-gone", 5 * GR)])

        await _sweep(graph, blob).run_page(None)

        assert graph.nodes["v1"]["retired"] == [] and blob.purged == []

    @pytest.mark.asyncio
    async def test_the_removal_expects_the_seq_and_does_not_bump_it(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-old", 5 * GR)])
        blob.rows = {"d-old": {"retiredAt": NOW - 5 * GR}}

        await _sweep(graph, blob).run_page(None)

        [(updates, expected)] = graph.cas
        assert expected == {"switchSeq": 5} and set(updates) == {"retired"}
        assert graph.nodes["v1"]["switchSeq"] == 5

    @pytest.mark.asyncio
    async def test_a_mapping_an_older_writer_wrote_is_skipped(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-old", 5 * GR)], switchDoc="d-other")
        graph.add("v2", retired=[_retired("d-old2", 5 * GR)], updatedAt=2)
        blob.rows = {"d-old": {"retiredAt": NOW - 5 * GR}, "d-old2": {"retiredAt": NOW - 5 * GR}}

        _, report = await _sweep(graph, blob).run_page(None)

        assert blob.purged == [] and report.fingerprints == 2

    @pytest.mark.asyncio
    async def test_a_switch_back_to_the_document_between_the_read_and_the_purge_saves_it(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-old", 5 * GR)])
        blob.rows = {"d-old": {"retiredAt": NOW - 5 * GR}}
        real = blob._get_document_row

        async def switch_then_row(org_id: str, document_id: str) -> dict[str, Any] | None:
            row = await real(org_id, document_id)
            graph.nodes["v1"].update(documentId="d-old", switchDoc="d-old")
            return row

        blob._get_document_row = switch_then_row  # type: ignore[method-assign]

        await _sweep(graph, blob).run_page(None)

        assert blob.purged == []


class TestPauseOnMembership:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("verdict", [MembershipVerdict(False, ("old-pod",), "untagged"), MembershipVerdict(False, (), "stale")])
    async def test_the_sweep_and_the_stray_pass_pause_and_resume(self, verdict: MembershipVerdict) -> None:
        """T50."""
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-old", 5 * GR)])
        blob.rows = {"d-old": {"retiredAt": NOW - 5 * GR}}
        blob.listing = [{"_id": "d-stray", "documentName": "record_v1", "retiredAt": None}]

        key, report = await _sweep(graph, blob, verdict).run_page(None)
        stray_key, strays = await _sweep(graph, blob, verdict).run_strays(None)

        assert report.paused and key is None and strays.paused and stray_key is None
        assert blob.purged == [] and blob.listed_with == []
        _, resumed = await _sweep(graph, blob, OK).run_page(None)
        _, resumed_strays = await _sweep(graph, blob, OK).run_strays(None)
        assert not resumed.paused and not resumed_strays.paused and set(blob.purged) == {"d-old", "d-stray"}


class TestStrayPass:
    @pytest.mark.asyncio
    async def test_unnamed_documents_older_than_the_grace_are_purged_and_named_ones_kept(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", retired=[_retired("d-retired", GR // 2)])
        blob.rows = {"d-retired": {"retiredAt": NOW}}
        blob.listing = [
            {"_id": "d-mapped", "documentName": "record_v1", "retiredAt": None},
            {"_id": "d-retired", "documentName": "record_v1", "retiredAt": None},
            {"_id": "d-stray", "documentName": "record_v1", "retiredAt": None},
            {"_id": "d-meta", "documentName": "metadata_v1", "retiredAt": None},
        ]

        _, report = await _sweep(graph, blob).run_strays(None)

        assert blob.purged == ["d-stray"] and report.strays_purged == 1
        assert blob.listed_with == [STRAY_GRACE_SECONDS]

    @pytest.mark.asyncio
    async def test_a_listing_from_a_storage_service_without_the_age_filter_purges_nothing(self) -> None:
        """An older storage release ignores olderThanSeconds and omits retiredAt: its listing includes a
        copy a writer has just made and not yet switched to."""
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1")
        blob.listing = [{"_id": "d-young", "documentName": "record_v1"}]

        _, report = await _sweep(graph, blob).run_strays(None)

        assert blob.purged == [] and report.strays_purged == 0

    @pytest.mark.asyncio
    async def test_a_mapping_switched_within_the_grace_is_left_alone(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", lastSwitchAt=NOW - GR // 2)
        blob.listing = [{"_id": "d-stray", "documentName": "record_v1", "retiredAt": None}]

        await _sweep(graph, blob).run_strays(None)

        assert blob.purged == [] and blob.listed_with == []

    @pytest.mark.asyncio
    async def test_a_vrid_with_no_document_is_left_to_the_vrid_purge(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1", documentId=None, switchDoc=None)
        blob.listing = [{"_id": "d-stray", "documentName": "record_v1", "retiredAt": None}]

        await _sweep(graph, blob).run_strays(None)

        assert blob.purged == []

    @pytest.mark.asyncio
    async def test_a_switch_between_the_listing_and_the_purge_saves_the_document(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1")
        blob.listing = [{"_id": "d-new", "documentName": "record_v1", "retiredAt": None}]
        real = graph.get_document
        reads = {"n": 0}

        async def switching(key: str, collection: str, **kw: Any) -> dict[str, Any] | None:  # noqa: ANN401
            reads["n"] += 1
            if reads["n"] == 2:
                graph.nodes["v1"]["documentId"] = "d-new"
            return await real(key, collection, **kw)

        graph.get_document = switching  # type: ignore[method-assign]

        await _sweep(graph, blob).run_strays(None)

        assert blob.purged == []

    @pytest.mark.asyncio
    async def test_only_a_bounded_number_of_mappings_are_examined_per_tick(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        for i in range(5):
            graph.add(f"v{i}")
        blob.listing = []

        await _sweep(graph, blob, strays_per_tick=2).run_strays(None)

        assert len(blob.listed_with) == 2

    @pytest.mark.asyncio
    async def test_the_cursor_reaches_every_mapping_once_per_cycle_then_starts_over(self) -> None:
        """Control: driven from the retirement pages, only the first strays_per_tick mappings of each page
        were ever listed, and the same ones were listed again on every tick."""
        graph, blob = FakeGraph(), FakeBlob()
        for i in range(5):
            graph.add(f"v{i}")
        listed: list[str] = []

        async def listing(org_id: str, vrid: str, *, older_than_seconds: int | None = None) -> list[dict[str, Any]]:
            listed.append(vrid)
            return []

        blob.list_record_documents = listing  # type: ignore[method-assign]
        sweep = _sweep(graph, blob, strays_per_tick=2)

        key, _ = await sweep.run_strays(None)
        assert key == "v1"
        key, _ = await sweep.run_strays(key)
        assert key == "v3"
        key, _ = await sweep.run_strays(key)
        assert key is None
        await sweep.run_strays(key)

        assert listed == ["v0", "v1", "v2", "v3", "v4", "v0", "v1"]

    @pytest.mark.asyncio
    async def test_the_retirement_pages_list_no_documents(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        graph.add("v1")
        blob.listing = [{"_id": "d-stray", "documentName": "record_v1", "retiredAt": None}]

        await _sweep(graph, blob).run_page(None)

        assert blob.listed_with == [] and blob.purged == []

    @pytest.mark.asyncio
    async def test_a_pause_keeps_the_cursor_on_the_mapping_not_yet_examined(self) -> None:
        graph, blob = FakeGraph(), FakeBlob()
        for i in range(3):
            graph.add(f"v{i}")
        verdicts = iter([OK, MembershipVerdict(False, (), "stale")])

        async def membership() -> MembershipVerdict:
            return next(verdicts)

        sweep = StoredRecordRetention(graph, blob, logging.getLogger("t"), membership=membership, now_ms=lambda: NOW)  # type: ignore[arg-type]

        key, report = await sweep.run_strays(None)

        assert report.paused and key == "v0" and len(blob.listed_with) == 1
