"""Every edge collection an indexing stage writes from a record is cleared by ``unindex_record`` or excluded with a reason.

The record-DELETE path removes record edges implicitly, by deleting the node, so ``test_unindex_covers_delete_path`` cannot
see them. This scans the stage writers' calls instead. A stage writer that writes edges to a new collection (or EXTRACTED
relations to a new target) fails here until the new edge is cleared by a step of ``unindex_record`` or excluded.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"
ROOTS = [APP / "modules/transformers", APP / "modules/named_entities", APP / "modules/pipeline"]
UNINDEX = APP / "modules/indexing/unindex.py"
CLEARER = APP / "modules/transformers/graphdb.py"

# signature -> (text that must appear in the clearing code, or None when excluded) and the reason
COVERED = {
    "reconcile:CollectionNames.BELONGS_TO_DEPARTMENT.value": "BELONGS_TO_DEPARTMENT",
    "reconcile:CollectionNames.BELONGS_TO_CATEGORY.value": "BELONGS_TO_CATEGORY",
    "reconcile:CollectionNames.BELONGS_TO_LANGUAGE.value": "BELONGS_TO_LANGUAGE",
    "reconcile:CollectionNames.BELONGS_TO_TOPIC.value": "BELONGS_TO_TOPIC",
    "reconcile:_EDGE": "clear_for_record",
    "entity_relations:CollectionNames.ORGS.value:EntityOrigin.EXTRACTED.value": "CollectionNames.ORGS.value, EntityOrigin.EXTRACTED.value",
}
EXCLUDED = {
    "edges:CollectionNames.INTER_CATEGORY_RELATIONS.value": "links taxonomy nodes to each other, not a record to anything",
}


def _signatures() -> set[str]:
    found: set[str] = set()
    for root in ROOTS:
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                args = [ast.unparse(a) for a in node.args]
                keywords = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
                if name == "_reconcile_edges" and len(args) >= 4 and "." in args[3]:
                    found.add(f"reconcile:{args[3]}")
                elif name == "reconcile" and "edge_collection" in keywords:
                    found.add(f"reconcile:{keywords['edge_collection']}")
                elif name == "batch_create_edges" and len(args) >= 2 and "." in args[1]:
                    found.add(f"edges:{args[1]}")
                elif name == "delete_record_entity_relations" and len(args) >= 3:
                    found.add(f"entity_relations:{args[1]}:{args[2]}")
                elif name in {"batch_create_entity_relations", "replace_record_entity_relations"}:
                    origins = {
                        ast.unparse(v)
                        for d in ast.walk(node) if isinstance(d, ast.Dict)
                        for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant) and k.value == "origin"
                    }
                    targets = {
                        ast.unparse(a) for a in node.args if ast.unparse(a).startswith("CollectionNames.")
                    }
                    for origin in origins:
                        # Written from `link_organizations`: its delete names the same target and origin.
                        found.add(f"entity_relations:CollectionNames.ORGS.value:{origin}")
                    del targets
    return found


def test_every_stage_edge_write_is_cleared_or_excluded() -> None:
    unmapped = _signatures() - COVERED.keys() - EXCLUDED.keys()
    assert not unmapped, f"clear these in unindex_record or exclude them with a reason: {sorted(unmapped)}"


def test_the_mapping_names_only_writes_that_still_exist() -> None:
    stale = (COVERED.keys() | EXCLUDED.keys()) - _signatures()
    assert not stale, f"no longer written by a stage: {sorted(stale)}"


def test_each_covered_edge_is_cleared_by_the_routine() -> None:
    clearing = CLEARER.read_text() + UNINDEX.read_text()
    missing = {sig: text for sig, text in COVERED.items() if text not in clearing}
    assert not missing


def test_a_new_edge_collection_would_fail_the_guard() -> None:
    assert {"reconcile:CollectionNames.BELONGS_TO_NEW.value"} - COVERED.keys() - EXCLUDED.keys()
