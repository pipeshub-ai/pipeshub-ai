"""A read inside the record's graph write that fails must fail the write,
not pass for "nothing there": the reconcile would then delete the record's
existing links as stale, or duplicate them, and a write conflict hidden as
an empty answer is never retried."""
from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames

DEPARTMENTS = CollectionNames.BELONGS_TO_DEPARTMENT.value
TOPICS = CollectionNames.BELONGS_TO_TOPIC.value


def _seed(fake_graph) -> None:
    fake_graph.add_record("r1", "acme")
    fake_graph.add_department("Engineering", key="dept-eng")
    fake_graph.edges[(DEPARTMENTS, "records/r1", "departments/dept-eng")] = {
        "from_id": "r1", "from_collection": "records", "to_id": "dept-eng", "to_collection": "departments",
    }


async def test_a_failed_department_lookup_fails_the_write_and_keeps_the_link(
    make_transformer, fake_graph, metadata_factory,
) -> None:
    _seed(fake_graph)
    fake_graph.fail_filter_reads = [RuntimeError("graph down")]
    with pytest.raises(RuntimeError, match="graph down"):
        await make_transformer().save_metadata_to_db("r1", metadata_factory(departments=["Engineering"]), "vr-1")
    assert [e["to_id"] for e in fake_graph.edges_from("r1", DEPARTMENTS)] == ["dept-eng"]


async def test_a_write_conflict_in_the_department_lookup_is_retried(
    make_transformer, fake_graph, metadata_factory,
) -> None:
    _seed(fake_graph)
    fake_graph.fail_filter_reads = [RuntimeError("write conflict on departments")]
    transformer = make_transformer()
    await transformer.save_metadata_to_db("r1", metadata_factory(departments=["Engineering"]), "vr-1")
    assert [e["to_id"] for e in fake_graph.edges_from("r1", DEPARTMENTS)] == ["dept-eng"]
    # Recovered by the retry, so nothing should page anyone.
    transformer.logger.error.assert_not_called()


async def test_a_failed_edge_read_fails_the_write_and_changes_no_link(
    make_transformer, fake_graph, metadata_factory,
) -> None:
    _seed(fake_graph)
    fake_graph.fail_edge_reads = [RuntimeError("graph down")]
    with pytest.raises(RuntimeError, match="graph down"):
        await make_transformer().save_metadata_to_db(
            "r1", metadata_factory(departments=["Engineering"], topics=["Pricing"]), "vr-1",
        )
    assert [e["to_id"] for e in fake_graph.edges_from("r1", DEPARTMENTS)] == ["dept-eng"]
    assert fake_graph.edges_from("r1", TOPICS) == []
