"""Static audit of what the app writes to the graph, against the node and edge schemas.

Strict schema validation rejects an unknown key (additionalProperties false), a value of the wrong
type, a value outside an enum and, on a create, a missing required key. Nothing here runs a
database: the tests read ``app/`` with ``ast`` and compare the payloads that writers build, the
targeted Cypher ``SET`` statements of the Neo4j provider and the record converters with the
adapted schemas. A writer that starts sending something the schema refuses fails a test named
after the writer.

Two kinds of test live here:

* pins of what is clean today, written as scans so a new writer is covered without a list to
  keep up (they pass);
* ``test_known_violation_*``: one plain failing test per payload the audit found that strict
  validation would reject. They fail until the schema or the writer is fixed, and are never
  marked xfail.

Only payloads the scan can read completely are checked: a dict literal, or a name bound to dict
literals in the same function. A payload that comes from a parameter, a converter or a
comprehension is left to the converter and runtime tests.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.neo4j import (
    COLLECTION_TO_LABEL,
    EDGE_COLLECTION_TO_RELATIONSHIP,
    LABEL_TO_COLLECTION,
    Neo4jRelationshipType,
)
from app.schema.arango import documents as arango_documents
from app.schema.node_schema_registry import adapt_schema, get_node_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[3] / "app"
NEO4J_PROVIDER = APP / "services" / "graph_db" / "neo4j" / "neo4j_provider.py"
ENTITIES = APP / "models" / "entities.py"

# Keys the backend adds or strips itself; the validator never sees them.
_ENDPOINT_KEYS = frozenset({"_from", "_to", "from_id", "to_id", "from_collection", "to_collection", "_key", "_id", "id"})
_INTERNAL_KEYS = frozenset({"khSortName", "khScope"})

# provider method -> (kind, position of the collection argument, position of the payload argument)
_WRITERS: dict[str, tuple[str, int | None, int]] = {
    "batch_upsert_nodes": ("upsert", 1, 0),
    "ensure_nodes": ("create", 1, 0),
    "create_node_if_absent": ("create", 1, 2),
    "update_node": ("update", 1, 2),
    "batch_update_nodes": ("update", 1, 0),
    "update_node_fields_if_match": ("update", 1, 2),
    "update_node_fields_if": ("update", 1, 2),
    "update_node_if_match": ("replace", 1, 2),
    "batch_create_edges": ("edge", 1, 0),
    "create_edges_if_absent": ("edge", 1, 0),
    "replace_edges_to": ("edge", 3, 2),
}
_EDGE_KINDS = {"edge"}
_SKIPPED_DIRS = ("services/graph_db/arango",)


# --------------------------------------------------------------------------- schemas


@lru_cache(maxsize=None)
def _node_schema(collection: str) -> dict | None:
    registered = get_node_schema(collection)
    if registered is not None:
        return registered
    # Collections the registry does not carry yet but ArangoDB validates.
    by_collection = {
        CollectionNames.PULLREQUESTS.value: arango_documents.pull_request_record_schema,
        CollectionNames.CODE_FILES.value: arango_documents.code_file_record_schema,
        CollectionNames.PRODUCTS.value: arango_documents.product_record_schema,
        CollectionNames.DEALS.value: arango_documents.deal_record_schema,
        CollectionNames.SQL_TABLES.value: arango_documents.sql_table_record_schema,
        CollectionNames.SQL_COLUMNS.value: arango_documents.sql_column_record_schema,
        CollectionNames.SQL_VIEWS.value: arango_documents.sql_view_record_schema,
        CollectionNames.AGENT_KNOWLEDGE.value: arango_documents.knowledge_schema,
        CollectionNames.AGENT_TOOLSETS.value: arango_documents.toolset_schema,
        CollectionNames.AGENT_TOOLS.value: arango_documents.tool_schema,
        CollectionNames.AGENT_MCP_SERVERS.value: arango_documents.mcp_server_schema,
    }
    schema = by_collection.get(collection)
    return adapt_schema(schema) if schema else None


@lru_cache(maxsize=None)
def _edge_schema(collection: str) -> dict | None:
    from app.services.graph_db.arango.arango_http_provider import EDGE_COLLECTIONS

    for name, schema in EDGE_COLLECTIONS:
        if name == collection:
            return schema["rule"] if schema else None
    return None


def _schema_for(collection: str, kind: str) -> dict | None:
    return _edge_schema(collection) if kind in _EDGE_KINDS else _node_schema(collection)


def _undeclared(schema: dict, keys: set[str]) -> list[str]:
    """Keys a schema with additionalProperties false does not declare."""
    if schema.get("additionalProperties", True) is not False:
        return []
    declared = set(schema.get("properties", {}))
    patterns = list(schema.get("patternProperties", {}))
    return sorted(
        key for key in keys - declared - _ENDPOINT_KEYS - _INTERNAL_KEYS
        if not any(re.search(pattern, key) for pattern in patterns)
    )


def _value_errors(schema: dict, key: str, value: object) -> list[str]:
    prop = schema.get("properties", {}).get(key)
    if prop is None:
        return []
    wrapper = {"type": "object", "properties": {key: prop}}
    return [error.message for error in Draft202012Validator(wrapper).iter_errors({key: value})]


# --------------------------------------------------------------------------- reading the writers


@dataclass(frozen=True)
class Site:
    file: str
    line: int
    function: str
    method: str
    kind: str
    collection: str
    keys: frozenset[str]
    literals: tuple[tuple[str, Any], ...]  # (key, constant value) for the values that are constants

    @property
    def id(self) -> str:
        return f"{self.file}::{self.function}::{self.method}::{self.collection}"


def _module_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = _const_str(node.value, {})
            if value is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = value
    return out


def _const_str(node: ast.AST | None, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Attribute) and node.attr == "value"
        and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "CollectionNames"
    ):
        member = CollectionNames.__members__.get(node.value.attr)
        return member.value if member else None
    if isinstance(node, ast.Name):
        return names.get(node.id)
    return None


def _enclosing_function(node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
        node = getattr(node, "_parent", None)
    return None


def _dicts_of(expr: ast.AST | None, fn: ast.AST | None, depth: int = 0) -> tuple[list[ast.Dict], bool]:
    """The dict literals an expression is made of, and whether that is all of it."""
    if expr is None or depth > 3:
        return [], False
    if isinstance(expr, ast.Dict):
        return [expr], True
    if isinstance(expr, (ast.List, ast.Tuple)):
        dicts: list[ast.Dict] = []
        closed = True
        for element in expr.elts:
            sub, ok = _dicts_of(element, fn, depth + 1)
            dicts += sub
            closed = closed and ok
        return dicts, closed
    if isinstance(expr, ast.Name) and fn is not None:
        dicts, closed, found = [], True, False
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == expr.id for t in node.targets):
                found = True
                sub, ok = _dicts_of(node.value, fn, depth + 1)
                dicts += sub
                closed = closed and ok
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == expr.id and node.func.attr in {"append", "extend", "update"}:
                found = True
                sub, ok = _dicts_of(node.args[0] if node.args else None, fn, depth + 1)
                dicts += sub
                closed = closed and ok and not node.keywords
            elif isinstance(node, (ast.AugAssign, ast.For)) and (
                getattr(node, "target", None) is not None and isinstance(node.target, ast.Name) and node.target.id == expr.id
            ):
                closed = False
        names_written_by_subscript = _subscript_keys(expr.id, fn)
        if names_written_by_subscript is None:
            closed = False
        return dicts, closed and found
    return [], False


def _subscript_keys(name: str, fn: ast.AST) -> dict[str, ast.AST] | None:
    """`name["k"] = v` writes in a function; None when a key is not a constant."""
    out: dict[str, ast.AST] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == name:
                    if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                        out[target.slice.value] = node.value
                    else:
                        return None
    return out


def _site_keys(expr: ast.AST, fn: ast.AST | None) -> tuple[set[str], list[tuple[str, Any]]] | None:
    dicts, closed = _dicts_of(expr, fn)
    if not dicts or not closed:
        return None
    keys: set[str] = set()
    literals: list[tuple[str, Any]] = []
    for d in dicts:
        for key, value in zip(d.keys, d.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None  # a spread or a computed key: not readable here
            keys.add(key.value)
            if isinstance(value, ast.Constant):
                literals.append((key.value, value.value))
    if isinstance(expr, ast.Name) and fn is not None:
        extra = _subscript_keys(expr.id, fn)
        if extra is None:
            return None
        keys |= set(extra)
        literals += [(k, v.value) for k, v in extra.items() if isinstance(v, ast.Constant)]
    return keys, literals


def _arg(call: ast.Call, position: int | None, names: tuple[str, ...]) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg in names:
            return keyword.value
    if position is not None and len(call.args) > position:
        return call.args[position]
    return None


@lru_cache(maxsize=1)
def _sites() -> tuple[Site, ...]:
    sites: list[Site] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel.startswith(_SKIPPED_DIRS):
            continue
        text = path.read_text(encoding="utf-8-sig")
        if not any(f".{method}(" in text for method in _WRITERS):
            continue
        tree = ast.parse(text)
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child._parent = parent  # type: ignore[attr-defined]
        consts = _module_constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _WRITERS):
                continue
            kind, coll_pos, pay_pos = _WRITERS[node.func.attr]
            fn = _enclosing_function(node)
            collection = _const_str(_arg(node, coll_pos, ("collection", "edge_collection")), consts)
            payload = _arg(node, pay_pos, ("nodes", "edges", "node", "node_updates", "updates"))
            if collection is None or payload is None:
                continue
            read = _site_keys(payload, fn)
            if read is None:
                continue
            keys, literals = read
            sites.append(Site(rel, node.lineno, fn.name if fn else "<module>", node.func.attr, kind, collection,
                              frozenset(keys), tuple(literals)))
    return tuple(sites)


def _sites_of(*kinds: str) -> list[Site]:
    return [site for site in _sites() if site.kind in kinds]


def _ids(sites: list[Site]) -> list[str]:
    return [site.id for site in sites]


# --------------------------------------------------------------------------- the scan is not empty


def test_the_scan_reads_a_meaningful_number_of_writers() -> None:
    # A scan that silently reads nothing would pass every test below.
    assert len(_sites()) >= 40
    assert {"update", "upsert", "edge"} <= {site.kind for site in _sites()}


# --------------------------------------------------------------------------- literal payloads


def _node_update_sites() -> list[Site]:
    return [s for s in _sites_of("update", "upsert", "create", "replace") if (_node_schema(s.collection) or {}).get("properties")]


@pytest.mark.parametrize("site", _node_update_sites(), ids=_ids(_node_update_sites()))
def test_literal_node_keys_are_declared_by_the_collection_schema(site: Site) -> None:
    schema = _node_schema(site.collection)
    assert schema is not None
    assert _undeclared(schema, set(site.keys)) == [], f"{site.file}:{site.line} {site.method} on {site.collection}"


@pytest.mark.parametrize("site", _node_update_sites(), ids=_ids(_node_update_sites()))
def test_literal_node_values_have_the_declared_type_and_enum(site: Site) -> None:
    schema = _node_schema(site.collection)
    assert schema is not None
    problems = [
        f"{key}={value!r}: {message}"
        for key, value in site.literals
        for message in _value_errors(schema, key, value)
    ]
    assert problems == [], f"{site.file}:{site.line} {site.method} on {site.collection}"


def _edge_sites() -> list[Site]:
    return [s for s in _sites_of("edge") if _edge_schema(s.collection)]


@pytest.mark.parametrize("site", _edge_sites(), ids=_ids(_edge_sites()))
def test_literal_edge_keys_values_and_required_match_the_edge_schema(site: Site) -> None:
    schema = _edge_schema(site.collection)
    assert schema is not None
    problems = [f"unknown key {key!r}" for key in _undeclared(schema, set(site.keys))]
    problems += [
        f"{key}={value!r}: {message}"
        for key, value in site.literals
        for message in _value_errors(schema, key, value)
    ]
    problems += [f"missing required {key!r}" for key in schema.get("required", []) if key not in site.keys]
    assert problems == [], f"{site.file}:{site.line} {site.method} on {site.collection}"


# --------------------------------------------------------------------------- record converters


def _converter_literals() -> list[tuple[str, str, str, frozenset[str]]]:
    """(class, method, collection, keys) for each ``to_arango_*`` that returns one dict literal."""
    collection_of = {
        ("FileRecord", "to_arango_record"): "files",
        ("MessageRecord", "to_arango_record"): "messages",
        ("MailRecord", "to_arango_record"): "mails",
        ("WebpageRecord", "to_arango_record"): "webpages",
        ("SharePointPageRecord", "to_arango_record"): "webpages",
        ("LinkRecord", "to_arango_record"): "links",
        ("CommentRecord", "to_arango_record"): "comments",
        ("ProjectRecord", "to_arango_record"): "projects",
        ("ProductRecord", "to_arango_record"): "products",
        ("DealRecord", "to_arango_record"): "deals",
        ("SQLViewRecord", "to_arango_record"): "sqlViews",
        ("SQLTableRecord", "to_arango_record"): "sqlTables",
        ("SQLColumnRecord", "to_arango_record"): "sqlColumns",
        ("PullRequestRecord", "to_arango_record"): "prs",
        ("ArtifactRecord", "to_arango_artifact_record"): "artifacts",
        ("CodeFileRecord", "to_arango_record"): "codeFiles",
        ("MeetingRecord", "to_arango_record"): "meetings",
        ("RecordGroup", "to_arango_base_record_group"): "recordGroups",
        ("Org", "to_arango_org"): "organizations",
        ("Person", "to_arango_person"): "person",
        ("AppUser", "to_arango_base_user"): "users",
        ("AppRole", "to_arango_base_role"): "roles",
    }
    tree = ast.parse(ENTITIES.read_text(encoding="utf-8"))
    out = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and (cls.name, fn.name) in collection_of:
                returns = [n.value for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
                if len(returns) == 1 and all(isinstance(k, ast.Constant) for k in returns[0].keys):
                    keys = frozenset(k.value for k in returns[0].keys if isinstance(k, ast.Constant))
                    out.append((cls.name, fn.name, collection_of[(cls.name, fn.name)], keys))
    return out


_CONVERTERS = _converter_literals()


def test_the_converter_scan_reads_the_record_types() -> None:
    assert len(_CONVERTERS) >= 18


@pytest.mark.parametrize(
    "cls,method,collection,keys",
    [c for c in _CONVERTERS if c[0] != "MeetingRecord"],
    ids=[f"{c[0]}.{c[1]}" for c in _CONVERTERS if c[0] != "MeetingRecord"],
)
def test_a_converter_emits_only_keys_its_collection_schema_declares(
    cls: str, method: str, collection: str, keys: frozenset[str]
) -> None:
    schema = _node_schema(collection)
    assert schema is not None
    assert _undeclared(schema, set(keys)) == []


# --------------------------------------------------------------------------- raw targeted Cypher writes

_KEYWORDS = r"(?:WITH|RETURN|MERGE|MATCH|WHERE|REMOVE|FOREACH|DELETE|DETACH|ON|UNWIND|CALL|CREATE|OPTIONAL|SET|ORDER|LIMIT|SKIP)"
_NODE_PATTERN = re.compile(r"\(\s*(\w+)\s*:\s*([A-Za-z_]\w*)")
_REL_PATTERN = re.compile(r"\[\s*(\w+)\s*:\s*([A-Za-z_]\w*)")
_SET_CLAUSE = re.compile(rf"\bSET\b(.*?)(?=\b{_KEYWORDS}\b|$)", re.S)
_CONSTANT = r"(true|false|'[^']*'|-?\d+(?:\.\d+)?)"
_ASSIGN = re.compile(r"(?<![\w.])(\w+)\.(\w+)\s*=(?!=)\s*(?:" + _CONSTANT + r"(?=\s*(?:,|$|[)}\]])))?")
_MAP_MERGE = re.compile(r"(?<![\w.])(\w+)\s*\+=\s*\{([^}]*)\}")
_MAP_KEY = re.compile(r"(?:^|,)\s*(\w+)\s*:")
_REMOVE_CLAUSE = re.compile(rf"\bREMOVE\b(.*?)(?=\b{_KEYWORDS}\b|$)", re.S)
_REMOVE_FIELD = re.compile(r"(?<![\w.])(\w+)\.(\w+)")

_RELATIONSHIP_TO_EDGE = {value: key for key, value in EDGE_COLLECTION_TO_RELATIONSHIP.items()}
_RELATIONSHIP_TO_EDGE[Neo4jRelationshipType.RECORD_LINK.value] = CollectionNames.RECORD_LINKS.value


@dataclass(frozen=True)
class RawWrite:
    line: int
    method: str
    target: str  # collection
    is_edge: bool
    field: str
    operation: str
    literal: str = ""  # the constant the statement assigns, as written in Cypher

    @property
    def id(self) -> str:
        return f"{self.method}::{self.target}.{self.field}::{self.operation}"

    def value(self) -> object:
        if self.literal in {"true", "false"}:
            return self.literal == "true"
        if self.literal.startswith("'"):
            return self.literal[1:-1]
        return float(self.literal) if "." in self.literal else int(self.literal)


def _fragment_values(name: str, fn: ast.AST | None, consts: dict[str, str]) -> list[str] | None:
    """Every constant a name can hold in a function (a label, or a clause that is sometimes empty)."""
    if name in consts:
        return [consts[name]]
    if fn is None:
        return None
    values: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            branches = [node.value.body, node.value.orelse] if isinstance(node.value, ast.IfExp) else [node.value]
            for branch in branches:
                if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                    values.append(branch.value)
                elif isinstance(branch, ast.Call) and isinstance(branch.func, ast.Name) and branch.func.id == "collection_to_label"                         and branch.args and (collection := _const_str(branch.args[0], consts)) is not None:
                    values.append(COLLECTION_TO_LABEL.get(collection, collection.capitalize()))
                else:
                    return None
    return values or None


def _render(node: ast.JoinedStr, fn: ast.AST | None, consts: dict[str, str]) -> str:
    parts = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            parts.append(str(part.value))
            continue
        values = _fragment_values(part.value.id, fn, consts) if isinstance(part.value, ast.Name) else None
        parts.append(" ".join(values) if values else "{}")
    return "".join(parts)


def _cypher_texts() -> Iterator[tuple[int, str, str]]:
    """(line, method, text) of each string constant or f-string of the Neo4j provider that holds Cypher."""
    tree = ast.parse(NEO4J_PROVIDER.read_text(encoding="utf-8"))
    consts = _module_constants(tree)
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]
    for node in ast.walk(tree):
        fn = _enclosing_function(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = _render(node, fn, consts)
        else:
            continue
        if not re.search(r"\b(SET|REMOVE)\b", text) or not re.search(r"\b(MATCH|MERGE|CREATE|UNWIND)\b", text):
            continue
        # The constant pieces of an f-string are visited on their own: keep the whole only.
        if isinstance(getattr(node, "_parent", None), ast.JoinedStr):
            continue
        yield node.lineno, fn.name if fn else "<module>", text


@lru_cache(maxsize=1)
def _raw_writes() -> tuple[RawWrite, ...]:
    out: set[RawWrite] = set()
    for line, method, text in _cypher_texts():
        nodes: dict[str, set[str]] = {}
        for var, label in _NODE_PATTERN.findall(text):
            nodes.setdefault(var, set()).add(label)
        rels = dict(_REL_PATTERN.findall(text))
        for var, label in re.findall(r"\bSET\s+(\w+)\s*:\s*(\w+)", text):  # a relabel: later writes hit the new label
            nodes[var] = {label}

        def target(var: str, nodes=nodes, rels=rels) -> tuple[str, bool] | None:
            labels = nodes.get(var)
            if labels and len(labels) == 1:
                collection = LABEL_TO_COLLECTION.get(next(iter(labels)))
                return (collection, False) if collection else None
            rel = rels.get(var)
            if rel and rel in _RELATIONSHIP_TO_EDGE:
                return _RELATIONSHIP_TO_EDGE[rel], True
            return None

        for clause in _SET_CLAUSE.findall(text):
            for var, field, literal in _ASSIGN.findall(clause):
                hit = target(var)
                if hit:
                    out.add(RawWrite(line, method, hit[0], hit[1], field, "set", literal))
            for var, body in _MAP_MERGE.findall(clause):
                hit = target(var)
                if hit:
                    for field in _MAP_KEY.findall(body):
                        out.add(RawWrite(line, method, hit[0], hit[1], field, "merge"))
        for clause in _REMOVE_CLAUSE.findall(text):
            for var, field in _REMOVE_FIELD.findall(clause):
                hit = target(var)
                if hit:
                    out.add(RawWrite(line, method, hit[0], hit[1], field, "remove"))
        # A property set and removed by the same statement is a row lock, never stored.
        here = [w for w in out if w.line == line]
        locks = {(w.target, w.field) for w in here if w.operation == "set"} & {(w.target, w.field) for w in here if w.operation == "remove"}
        out -= {w for w in here if (w.target, w.field) in locks}
    return tuple(sorted(out, key=lambda w: (w.method, w.target, w.field, w.operation)))


def _raw_cases(is_edge: bool) -> list[RawWrite]:
    seen: set[str] = set()
    cases = []
    for write in _raw_writes():
        # A REMOVE drops a stored property and carries no value to validate.
        if write.is_edge != is_edge or write.id in seen or write.operation == "remove":
            continue
        seen.add(write.id)
        cases.append(write)
    return cases


def test_the_cypher_scan_finds_the_targeted_writes() -> None:
    fields = {(w.target, w.field) for w in _raw_writes()}
    assert len(_raw_writes()) >= 30
    assert (CollectionNames.RECORDS.value, "indexingStatus") in fields
    assert (CollectionNames.RECORDS.value, "isDeleted") in fields


def _node_raw_cases() -> list[RawWrite]:
    return [w for w in _raw_cases(False) if (_node_schema(w.target) or {}).get("properties")]


@pytest.mark.parametrize("write", _node_raw_cases(), ids=[w.id for w in _node_raw_cases()])
def test_a_targeted_node_write_names_a_field_the_schema_declares(write: RawWrite) -> None:
    schema = _node_schema(write.target)
    assert schema is not None
    assert _undeclared(schema, {write.field}) == [], f"neo4j_provider.py:{write.line} {write.method}"


def _literal_raw_cases() -> list[RawWrite]:
    return [w for w in _node_raw_cases() if w.literal]


@pytest.mark.parametrize("write", _literal_raw_cases(), ids=[w.id for w in _literal_raw_cases()])
def test_a_targeted_node_write_of_a_constant_has_the_declared_type_and_enum(write: RawWrite) -> None:
    schema = _node_schema(write.target)
    assert schema is not None
    assert _value_errors(schema, write.field, write.value()) == [], f"neo4j_provider.py:{write.line} {write.method}"


def _edge_raw_cases() -> list[RawWrite]:
    return [w for w in _raw_cases(True) if (_edge_schema(w.target) or {}).get("properties")]


@pytest.mark.parametrize("write", _edge_raw_cases(), ids=[w.id for w in _edge_raw_cases()])
def test_a_targeted_edge_write_names_a_field_the_edge_schema_declares(write: RawWrite) -> None:
    schema = _edge_schema(write.target)
    assert schema is not None
    assert _undeclared(schema, {write.field}) == [], f"neo4j_provider.py:{write.line} {write.method}"


# Status fields written by a literal in Cypher or by an enum: every value must be inside the schema enum.
_STATUS_FIELDS = ("indexingStatus", "parsingStatus", "extractionStatus")


@pytest.mark.parametrize("field", _STATUS_FIELDS)
def test_every_progress_status_is_accepted_by_the_records_schema(field: str) -> None:
    schema = _node_schema(CollectionNames.RECORDS.value)
    assert schema is not None
    for status in ProgressStatus:
        assert _value_errors(schema, field, status.value) == [], status.value


@pytest.mark.parametrize("field", _STATUS_FIELDS)
def test_status_literals_in_provider_cypher_are_inside_the_schema_enum(field: str) -> None:
    allowed = set(_node_schema(CollectionNames.RECORDS.value)["properties"][field]["enum"])  # type: ignore[index]
    found: set[str] = set()
    pattern = re.compile(rf"\b{field}\s*(?:=|:)\s*'([A-Z_]+)'")
    for _, _, text in _cypher_texts():
        found |= set(pattern.findall(text))
    assert found <= allowed, sorted(found - allowed)


# --------------------------------------------------------------------------- validate before conversion


def _validator_calls_after_conversion() -> list[str]:
    """Functions of the Neo4j provider that validate a payload AFTER `_arango_to_neo4j_node` made it.

    Conversion JSON-encodes nested objects, so a schema that expects an object then rejects the string.
    """
    tree = ast.parse(NEO4J_PROVIDER.read_text(encoding="utf-8"))
    late: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        converted_at: dict[str, int] = {}
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call) \
                    and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "_arango_to_neo4j_node":
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        converted_at[target.id] = node.lineno
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr.startswith("validate_node") and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Name) and arg.id in converted_at and converted_at[arg.id] < node.lineno:
                    late.append(f"{fn.name}:{node.lineno}")
    return sorted(late)


def test_the_provider_validates_each_payload_before_converting_it() -> None:
    assert _validator_calls_after_conversion() == []


# --------------------------------------------------------------------------- the set of writers is closed

# Methods of the Neo4j provider whose Cypher creates, merges, sets or removes data on a node or an edge.
# Each was read for what it writes and checked against the schemas. A method that appears here for the
# first time writes something nobody has checked against the strict rules: read it, then add it.
_AUDITED_CYPHER_WRITERS = frozenset({
    '_delete_unlinked_taxonomy_nodes', '_move_stub_edges_query', '_record_group_link_cypher',
    '_replace_edges_to_cypher', '_require_unique_id', '_stash_connector_delete_handles',
    '_upsert_record_with_type_cypher', 'add_taxonomy_aliases', 'backfill_app_org_ids',
    'backfill_block_name_folded', 'backfill_hierarchy', 'backfill_indexed_copy_flags', 'stamp_external_org_parents', 'batch_create_edges',
    'batch_create_entity_relations', 'batch_update_connector_status', 'batch_update_nodes',
    'batch_update_team_member_roles', 'batch_upsert_node_relations', 'batch_upsert_nodes', 'batch_upsert_people',
    'claim_push_record', 'compare_and_set_indexing_status', 'compare_and_set_record_fields',
    'create_edges_if_absent', 'create_node_if_absent', 'create_organization_if_absent', 'create_taxonomy_node_if_absent',
    'delete_records_recursive', 'drop_redundant_group_edges', 'ensure_app_membership', 'ensure_nodes',
    'ensure_taxonomy_hierarchy_edge', 'ensure_team_app_edge', 'heal_taxonomy_alias_nodes', 'increment_node_field',
    'link_record_to_group', 'migrate_legacy_kb_to_app', 'migrate_legacy_relation_edge', 'migrate_person_to_user',
    'move_taxonomy_edges', 'normalize_folder_mime_types', 'purge_trash_kept_record_groups',
    'purge_trashed_records', 'record_purge_failure', 'reset_indexing_status_for_connector', 'restore_records',
    'retag_push_record', 'soft_delete_records', 'split_link_edges', 'stage_state_compare_and_set',
    'stage_state_create', 'stage_states_delete_unheld_revisions', 'store_page_token',
    'take_back_kept_record_group', 'update_agent_permission', 'update_drive_sync_state', 'update_edge',
    'update_kb_permission', 'update_knowledge_base', 'update_node', 'update_node_fields_if',
    'update_node_fields_if_match', 'update_node_if_match', 'update_nodes_fields_if_match', 'update_sync_point_if',
    'update_user_sync_state', 'upsert_authenticated_as', 'upsert_person_by_email', 'upsert_person_by_source_key', 'upsert_record_under_parent',
    'upsert_sync_point'
})


def _cypher_writers() -> set[str]:
    tree = ast.parse(NEO4J_PROVIDER.read_text(encoding="utf-8"))
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]
    consts = _module_constants(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(getattr(node, "_parent", None), ast.JoinedStr):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = _render(node, _enclosing_function(node), consts)
        else:
            continue
        writes = re.search(r"\b(SET|REMOVE|MERGE)\b", text) or re.search(r"\bCREATE\s*\(", text)
        if writes and re.search(r"\b(MATCH|MERGE|CREATE|UNWIND|FOREACH)\b", text):
            fn = _enclosing_function(node)
            if fn:
                names.add(fn.name)
    # Schema bootstrap and the derived listing state are not data writes of a caller.
    return {n for n in names if not n.startswith(("_generate_", "_cypher_kh_", "ensure_schema"))}


def test_every_cypher_writer_of_the_neo4j_provider_has_been_audited() -> None:
    assert sorted(_cypher_writers() - _AUDITED_CYPHER_WRITERS) == []


def test_no_audited_cypher_writer_has_disappeared_from_the_list() -> None:
    assert sorted(_AUDITED_CYPHER_WRITERS - _cypher_writers()) == []


# --------------------------------------------------------------------------- the PostgreSQL provider

POSTGRES = APP / "services" / "graph_db" / "postgres"


@pytest.mark.skipif(not POSTGRES.is_dir(), reason="this tree has no PostgreSQL graph backend")
def test_the_postgres_provider_adds_no_backend_internal_keys_to_a_stored_document() -> None:
    # The validator's internal-key list exists for the Neo4j listing state; if a PostgreSQL writer
    # starts stamping such keys, strict validation of a read-modify-write would see them.
    internal = ("khSortName", "khScope")
    hits = [
        f"{path.name}:{number}"
        for path in sorted(POSTGRES.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(key in line for key in internal)
    ]
    assert hits == []


# --------------------------------------------------------------------------- known violations

def test_known_violation_the_meetings_schema_does_not_declare_recording_url() -> None:
    """MeetingRecord.to_arango_record sends `recordingUrl`; meetings has additionalProperties false."""
    schema = _node_schema(CollectionNames.MEETINGS.value)
    assert schema is not None
    keys = next(k for c, m, _, k in _CONVERTERS if c == "MeetingRecord")
    assert _undeclared(schema, set(keys)) == []


def _creates_missing_required(function: str, collection: str) -> list[str]:
    """Required keys missing from every literal payload `function` upserts into `collection`.

    Used for writers that only reach the write for a node that does not exist yet, so the
    payload is a create and must be complete.
    """
    sites = [s for s in _sites() if s.function == function and s.collection == collection and s.kind == "upsert"]
    assert sites, f"no readable upsert of {collection} in {function}: the scan or the writer changed"
    schema = _node_schema(collection)
    assert schema is not None
    return sorted({key for s in sites for key in schema.get("required", []) if key not in s.keys and key not in _ENDPOINT_KEYS})


def test_known_violation_agent_hub_knowledge_migration_creates_knowledge_nodes_without_created_by() -> None:
    """migrate_agent_hub_knowledge writes fresh agentKnowledge nodes; the schema requires `createdBy`."""
    assert _creates_missing_required("migrate_agent_hub_knowledge", CollectionNames.AGENT_KNOWLEDGE.value) == []


def test_known_violation_app_user_upsert_creates_a_bare_app_node_missing_required_keys() -> None:
    """batch_upsert_app_users creates `{id, name, isActive}` when the app is absent; apps requires more."""
    assert _creates_missing_required("batch_upsert_app_users", CollectionNames.APPS.value) == []


def test_known_violation_skill_status_enum_does_not_cover_every_skill_status() -> None:
    """The skill store can hold `draft` and `candidate` (SkillStatus); agentSkills.status accepts neither."""
    from app.agent_loop_lib.modules.providers.skills.base import SkillStatus

    schema = _node_schema(CollectionNames.AGENT_SKILLS.value)
    assert schema is not None
    rejected = [s.value for s in SkillStatus if _value_errors(schema, "status", s.value)]
    assert rejected == []


def test_known_violation_salesforce_edges_can_carry_a_null_created_at_timestamp() -> None:
    """The edges written for accounts and opportunities take their timestamps from a parser that returns
    None for a missing date; the edge schemas type `createdAtTimestamp` as a non-null number."""
    path = APP / "connectors" / "sources" / "salesforce" / "connector.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parser = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_parse_salesforce_timestamp")
    assert any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and n.value.value is None for n in ast.walk(parser))

    def from_parser(fn: ast.AST, name: str) -> bool:
        return any(
            isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
            and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "_parse_salesforce_timestamp"
            for n in ast.walk(fn)
        )

    unguarded: list[str] = []
    for fn in ast.walk(tree):
        # The contact and lead paths build a Person first, whose int fields reject None.
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in {"_sync_accounts", "_sync_opportunities"}:
            continue
        for d in (n for n in ast.walk(fn) if isinstance(n, ast.Dict)):
            for key, value in zip(d.keys, d.values):
                if isinstance(key, ast.Constant) and key.value in {"createdAtTimestamp", "updatedAtTimestamp"}:
                    raw = (isinstance(value, ast.Call) and getattr(value.func, "id", "") == "_parse_salesforce_timestamp") or (
                        isinstance(value, ast.Name) and from_parser(fn, value.id)
                    )
                    if raw:
                        unguarded.append(f"{fn.name}:{value.lineno}")
    assert unguarded == []
