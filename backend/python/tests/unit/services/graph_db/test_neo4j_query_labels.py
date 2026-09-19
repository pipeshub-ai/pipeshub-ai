"""Every node label and relationship type written into a Neo4j query must be one we store.

Neo4j accepts a query that names a label nothing has, and simply matches
nothing. A typo such as ``Department`` for ``Departments`` therefore never
errors; the feature just returns empty results. These checks read the Cypher
in the provider's source and compare it with the labels the writers use.
"""

import ast
import re
from pathlib import Path

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import (
    COLLECTION_TO_LABEL,
    EDGE_COLLECTION_TO_RELATIONSHIP,
    Neo4jLabel,
    Neo4jRelationshipType,
    collection_to_label,
    edge_collection_to_relationship,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

PROVIDER = Path(__file__).resolve().parents[4] / "app/services/graph_db/neo4j/neo4j_provider.py"

_CYPHER = re.compile(r"\b(MATCH|MERGE|CREATE|DETACH DELETE|RETURN)\b")
_COMMENT = re.compile(r"//[^\n]*")
_NODE = re.compile(r"\(\s*(?:[A-Za-z_]\w*)?\s*((?::\s*[A-Za-z_]\w*)+)")
_PREDICATE = re.compile(r"\b(?:WHERE|AND|OR|NOT)\s+[a-z_]\w*((?::[A-Z]\w*)+)")
_RELATIONSHIP = re.compile(r"\[\s*(?:[A-Za-z_]\w*)?\s*:\s*([A-Z_][A-Z0-9_]*(?:\s*\|\s*:?[A-Z_][A-Z0-9_]*)*)")


def _known_labels() -> set[str]:
    return (
        {label.value for label in Neo4jLabel}
        | set(COLLECTION_TO_LABEL.values())
        | {collection_to_label(collection.value) for collection in CollectionNames}
    )


def _known_relationships() -> set[str]:
    return (
        {rel.value for rel in Neo4jRelationshipType}
        | set(EDGE_COLLECTION_TO_RELATIONSHIP.values())
        | {edge_collection_to_relationship(collection.value) for collection in CollectionNames}
    )


def _query_fragments() -> list[tuple[int, str]]:
    """String literals (and the literal parts of f-strings) that hold Cypher, docstrings excluded."""
    tree = ast.parse(PROVIDER.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    fragments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value if isinstance(part, ast.Constant) else "{}"
                for part in node.values
            )
        else:
            continue
        if _CYPHER.search(text):
            fragments.append((node.lineno, _COMMENT.sub("", text)))
    return fragments


def _used(pattern: re.Pattern[str], split: str) -> dict[str, list[int]]:
    used: dict[str, list[int]] = {}
    for line, text in _query_fragments():
        for match in pattern.finditer(text):
            for name in re.split(split, match.group(1)):
                if name:
                    used.setdefault(name, []).append(line)
    return used


def test_the_scan_sees_the_provider_queries() -> None:
    labels = _used(_NODE, r"[:\s]+")
    assert {"Record", "User", "RecordGroup"} <= set(labels), "the scanner no longer finds the provider's Cypher"


def test_every_node_label_in_a_query_is_one_we_store() -> None:
    used = _used(_NODE, r"[:\s]+")
    for name, lines in _used(_PREDICATE, r":").items():
        used.setdefault(name, []).extend(lines)
    unknown = {name: lines for name, lines in used.items() if name not in _known_labels()}
    assert not unknown, f"labels that no writer stores (name: provider lines): {unknown}"


def test_every_relationship_type_in_a_query_is_one_we_store() -> None:
    unknown = {
        name: lines
        for name, lines in _used(_RELATIONSHIP, r"[|:\s]+").items()
        if name not in _known_relationships()
    }
    assert not unknown, f"relationship types that no writer stores (name: provider lines): {unknown}"


@pytest.mark.parametrize(
    ("key", "label", "name_property", "parameter"),
    [
        ("departments", "Departments", "departmentName", "departmentNames"),
        ("categories", "Categories", "name", "categoryNames"),
        ("subcategories1", "Subcategories1", "name", "subcat1Names"),
        ("subcategories2", "Subcategories2", "name", "subcat2Names"),
        ("subcategories3", "Subcategories3", "name", "subcat3Names"),
        ("languages", "Languages", "name", "languageNames"),
        ("topics", "Topics", "name", "topicNames"),
    ],
)
def test_each_metadata_filter_matches_the_label_the_indexer_writes(
    key: str, label: str, name_property: str, parameter: str
) -> None:
    clause, parameters = Neo4jProvider._metadata_filter({key: ["x"]})

    assert f"->(m:{label})" in clause
    assert f"m.{name_property} IN ${parameter}" in clause
    assert parameters == {parameter: ["x"]}
    collection = {"departments": CollectionNames.DEPARTMENTS}.get(key) or CollectionNames[key.upper()]
    assert collection_to_label(collection.value) == label


def test_metadata_filters_combine_and_skip_empty_values() -> None:
    clause, parameters = Neo4jProvider._metadata_filter({"topics": ["a"], "languages": [], "departments": ["Legal"]})

    assert clause.startswith(" AND ")
    assert clause.count("EXISTS") == 2
    assert parameters == {"topicNames": ["a"], "departmentNames": ["Legal"]}
    assert Neo4jProvider._metadata_filter(None) == ("", {})
    assert Neo4jProvider._metadata_filter({}) == ("", {})
