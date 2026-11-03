"""The pre-rename hierarchy edge name must not come back.

`recordRelations` / `RECORD_RELATION` were renamed to `nodeRelations` /
`NODE_RELATION` across the code base. The dangerous part of that rename is that
the old names live inside query *text* — f-strings of Cypher and AQL — where a
stray reappearance raises nothing at import time and nothing at type-check time.
It surfaces only as a query that quietly matches no edges. This test is the
backstop.

`RecordRelations`, the relationship-type enum (PARENT_CHILD, ATTACHMENT, ...),
is deliberately *not* renamed and must keep matching nothing here: every pattern
below is case-sensitive, which is exactly what kept it intact during the sweep.
"""

from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

LEGACY_NAMES = ("recordRelations", "RECORD_RELATION", "record_relations")

# The one module allowed to name the old edge: it is what migrates away from it.
EXEMPT = {"node_relation_migration.py"}


def _offenders() -> list[str]:
    hits: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if path.name in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in LEGACY_NAMES:
            if name in text:
                line = next(
                    (i for i, ln in enumerate(text.splitlines(), 1) if name in ln), 0
                )
                hits.append(f"{path.relative_to(APP_ROOT)}:{line} contains {name!r}")
    return hits


def test_legacy_edge_name_is_gone_from_production_code() -> None:
    offenders = _offenders()
    assert not offenders, (
        "The pre-rename edge name reappeared. These fail silently at runtime "
        "(a query matching no edges), so they must be fixed, not suppressed:\n  "
        + "\n  ".join(offenders)
    )


def test_relationship_type_enum_was_not_renamed() -> None:
    """Guards the guard: proves the patterns above are case-sensitive.

    A case-insensitive check would match `RecordRelations` and would have
    destroyed the relationship-type enum across the code base.
    """
    from app.config.constants.arangodb import RecordRelations

    assert RecordRelations.PARENT_CHILD.value == "PARENT_CHILD"
    assert RecordRelations.ATTACHMENT.value == "ATTACHMENT"
    assert not any(name in "RecordRelations" for name in LEGACY_NAMES)


@pytest.mark.parametrize(
    "module,attr,expected",
    [
        ("app.config.constants.arangodb", "NODE_RELATIONS", "nodeRelations"),
        ("app.config.constants.neo4j", "NODE_RELATIONS", "NODE_RELATION"),
    ],
)
def test_wire_names_are_the_new_ones(module: str, attr: str, expected: str) -> None:
    """The stored names the migration renames data *to*."""
    import importlib

    mod = importlib.import_module(module)
    enum = mod.CollectionNames if "arangodb" in module else mod.Neo4jRelationshipType
    assert getattr(enum, attr).value == expected
