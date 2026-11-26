"""Every awaited call in the record-DELETE branch is either part of ``unindex_record`` or excluded with a reason.

The DELETE branch is what takes a record's index back today. When it gains a new call this test fails until the call is
mapped to a step of ``unindex_record`` (or excluded, with the reason it does not apply to a record that still exists).
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"
HANDLER = APP / "services/messaging/kafka/handlers/record.py"
UNINDEX = APP / "modules/indexing/unindex.py"

# call -> the text in unindex.py that performs the same step
COVERED = {
    "self.event_processor.processor.indexing_pipeline.bulk_delete_embeddings": "_release_skipped_content",
    "delete_unreached_taxonomy_points": "delete_unreached_taxonomy_points",
}
EXCLUDED = {
    "self.event_processor.graph_provider.get_document": "a read",
    "entity_store.delete_entity": "the record's title point stays: the record still exists and is found by name",
    "paid_cache_access.paid_cache_for(self.config_service, self.logger).delete_record":
        "keyed by content: a later index of the same bytes reuses it, and the record still exists",
    "schedule_paid_repurge": "the same stored model outputs; nothing to purge while the record exists",
    "self._request_code_edge_build_for_deleted": "a PDF has no code graph",
    "self._hand_over_shared_copy": "the record is not deleted, and the release keeps what a twin still references",
}


def _delete_branch_awaits() -> set[str]:
    tree = ast.parse(HANDLER.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and ast.unparse(node.test) == "event_type == EventTypes.DELETE_RECORD.value"
        ):
            return {
                ast.unparse(a.value.func)
                for a in ast.walk(node)
                if isinstance(a, ast.Await) and isinstance(a.value, ast.Call)
            }
    raise AssertionError("the record-DELETE branch was not found")


def test_every_awaited_call_of_the_delete_branch_is_mapped() -> None:
    unmapped = _delete_branch_awaits() - COVERED.keys() - EXCLUDED.keys()
    assert not unmapped, f"map these to a step of unindex_record or exclude them with a reason: {sorted(unmapped)}"


def test_the_mapping_names_only_calls_that_still_exist() -> None:
    stale = (COVERED.keys() | EXCLUDED.keys()) - _delete_branch_awaits()
    assert not stale, f"no longer in the DELETE branch: {sorted(stale)}"


def test_each_covered_call_has_its_step_in_unindex_record() -> None:
    source = UNINDEX.read_text()
    missing = {call: step for call, step in COVERED.items() if step not in source}
    assert not missing


def test_the_guard_fails_for_a_new_unmapped_call() -> None:
    assert "brand_new_cleanup" not in COVERED and "brand_new_cleanup" not in EXCLUDED
    assert {"brand_new_cleanup"} - COVERED.keys() - EXCLUDED.keys()
