"""Tests for `app.services.artifact_registry.lineage.LineageTracker` —
auto-captured `DERIVED_FROM` edges, never model-asserted."""

from __future__ import annotations

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.services.artifact_registry.lineage import LineageTracker

from .fakes import FakeGraphProvider

_RELATIONS = CollectionNames.RECORD_RELATIONS.value


class TestRecordDerivation:
    async def test_writes_derived_from_edge_with_versions(self) -> None:
        graph = FakeGraphProvider()
        tracker = LineageTracker(graph)

        await tracker.record_derivation(
            output_artifact_id="out-1", code_artifact_id="code-1", code_version=3, output_version=1,
        )

        edges = graph.edges[_RELATIONS]
        assert len(edges) == 1
        assert edges[0]["from_id"] == "out-1"
        assert edges[0]["to_id"] == "code-1"
        assert edges[0]["relationshipType"] == RecordRelations.DERIVED_FROM.value
        assert edges[0]["sourceVersion"] == 3
        assert edges[0]["derivedVersion"] == 1

    async def test_logs_but_does_not_raise_when_edge_write_fails(self) -> None:
        graph = FakeGraphProvider()
        graph.batch_create_edges = _fail_batch_create_edges  # type: ignore[method-assign]
        tracker = LineageTracker(graph)

        # Must not raise — a lineage-write failure should never break the
        # tool call that produced the artifact.
        await tracker.record_derivation(
            output_artifact_id="out-1", code_artifact_id="code-1", code_version=1, output_version=1,
        )


async def _fail_batch_create_edges(edges: list[dict], collection: str) -> bool:
    return False


class TestGetLineageForOutput:
    async def test_returns_none_when_no_lineage_recorded(self) -> None:
        graph = FakeGraphProvider()
        tracker = LineageTracker(graph)
        assert await tracker.get_lineage_for_output("out-1") is None

    async def test_returns_most_recent_edge_when_derived_multiple_times(self, monkeypatch) -> None:
        graph = FakeGraphProvider()
        tracker = LineageTracker(graph)
        timestamps = iter([100, 200])
        monkeypatch.setattr(
            "app.services.artifact_registry.lineage.get_epoch_timestamp_in_ms",
            lambda: next(timestamps),
        )
        await tracker.record_derivation(
            output_artifact_id="out-1", code_artifact_id="code-1", code_version=1, output_version=1,
        )
        # A later re-run of the same code artifact against the same output.
        await tracker.record_derivation(
            output_artifact_id="out-1", code_artifact_id="code-1", code_version=2, output_version=2,
        )

        lineage = await tracker.get_lineage_for_output("out-1")
        assert lineage is not None
        assert lineage.code_version == 2
        assert lineage.output_version == 2

    async def test_ignores_non_derived_from_edges(self) -> None:
        graph = FakeGraphProvider()
        graph.edges[_RELATIONS].append({
            "from_id": "out-1", "to_id": "other-1", "relationshipType": "SIBLING",
            "createdAtTimestamp": 1,
        })
        tracker = LineageTracker(graph)
        assert await tracker.get_lineage_for_output("out-1") is None


class TestGetOutputsForCode:
    async def test_returns_every_output_derived_from_any_version(self) -> None:
        graph = FakeGraphProvider()
        tracker = LineageTracker(graph)
        await tracker.record_derivation(
            output_artifact_id="chart-1", code_artifact_id="code-1", code_version=1, output_version=1,
        )
        await tracker.record_derivation(
            output_artifact_id="table-1", code_artifact_id="code-1", code_version=1, output_version=1,
        )

        outputs = await tracker.get_outputs_for_code("code-1")
        output_ids = {o.output_artifact_id for o in outputs}
        assert output_ids == {"chart-1", "table-1"}

    async def test_returns_empty_list_when_code_has_no_outputs(self) -> None:
        graph = FakeGraphProvider()
        tracker = LineageTracker(graph)
        assert await tracker.get_outputs_for_code("code-1") == []
