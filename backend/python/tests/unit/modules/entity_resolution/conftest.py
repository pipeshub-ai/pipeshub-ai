"""Shared fakes for the entity-resolution suites.

Everything here is in-memory and deterministic so the end-to-end scenario
tests can drive the real resolver, the real graph transformer and the real
sink orchestrator without a database, a vector store or a model.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import SemanticMetadata
from app.modules.entity_resolution.models import MergeDecision, MergeDecisions
from app.modules.entity_resolution.normalizer import normalize_name
from app.modules.entity_resolution.resolver import EntityResolver
from app.modules.transformers.graphdb import GraphDBTransformer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

RECORDS = CollectionNames.RECORDS.value
DEPARTMENTS = CollectionNames.DEPARTMENTS.value


# ---------------------------------------------------------------------------
# Fake graph: provider-level and transaction-level methods on one object
# ---------------------------------------------------------------------------


class FakeGraph:
    """In-memory graph exposing both the provider methods the resolver uses
    and the transaction-store methods ``GraphDBTransformer`` uses."""

    def __init__(self) -> None:
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {}
        self.departments: dict[str, str] = {}
        self.calls: list[tuple[str, Any]] = []
        self.fail_find = False

    # ---- setup helpers ----
    def add_record(self, key: str, org_id: str, connector_id: str = "conn-1",
                   record_group_id: str = "rg-1") -> None:
        self.records[key] = {
            "_key": key, "orgId": org_id, "connectorId": connector_id,
            "recordGroupId": record_group_id,
        }

    def add_department(self, name: str, key: str | None = None) -> None:
        self.departments[name] = key or f"dept-{normalize_name(name)}"

    def add_legacy_node(self, collection: str, key: str, name: str) -> None:
        self.nodes[(collection, key)] = {"name": name}

    # ---- inspection helpers ----
    def nodes_in(self, collection: str) -> list[dict[str, Any]]:
        return [dict(n, key=k) for (c, k), n in self.nodes.items() if c == collection]

    def node(self, collection: str, key: str) -> dict[str, Any]:
        return self.nodes[(collection, key)]

    def edges_from(self, record_key: str, edge_collection: str) -> list[dict[str, Any]]:
        return [
            e for (c, f, _t), e in self.edges.items()
            if c == edge_collection and f == f"{RECORDS}/{record_key}"
        ]

    def edges_to(self, target_full: str) -> list[dict[str, Any]]:
        return [e for (_c, _f, t), e in self.edges.items() if t == target_full]

    # ---- provider-level (resolver) ----
    async def find_taxonomy_nodes(self, collection, org_id, normalized_names, transaction=None) -> list[dict[str, Any]]:
        self.calls.append(("find_taxonomy_nodes", (collection, org_id, list(normalized_names))))
        if self.fail_find:
            raise RuntimeError("graph down")
        wanted = set(normalized_names)
        return [
            {
                "id": key,
                "name": node["name"],
                "normalizedName": node.get("normalizedName"),
                "aliases": list(node.get("aliases") or []),
                "normalizedAliases": list(node.get("normalizedAliases") or []),
            }
            for (coll, key), node in self.nodes.items()
            if coll == collection
            and node.get("orgId") == org_id
            and (
                node.get("normalizedName") in wanted
                or wanted & set(node.get("normalizedAliases") or [])
            )
        ]

    async def create_taxonomy_node_if_absent(self, collection, node, transaction=None) -> None:
        self.calls.append(("create_taxonomy_node_if_absent", (collection, dict(node))))
        key = (collection, node["id"])
        if key in self.nodes:
            return
        stored = {k: v for k, v in node.items() if k not in ("id", "aliases")}
        stored.setdefault("aliases", [])
        self.nodes[key] = stored

    async def add_taxonomy_aliases(
        self, collection, key, aliases, normalized_aliases, *, max_aliases=20, transaction=None
    ) -> None:
        self.calls.append(("add_taxonomy_aliases", (collection, key, list(aliases), list(normalized_aliases))))
        node = self.nodes.get((collection, key))
        if node is None:
            return
        current = list(node.get("aliases") or [])
        current_normalized = list(node.get("normalizedAliases") or [])
        for alias, normalized in zip(aliases, normalized_aliases):
            if alias and normalized not in current_normalized:
                current.append(alias)
                current_normalized.append(normalized)
        node["aliases"] = current[:max_aliases]
        node["normalizedAliases"] = current_normalized[:max_aliases]

    # ---- transaction-store level (GraphDBTransformer) ----
    async def get_record_by_key(self, key) -> dict[str, Any] | None:
        return self.records.get(key)

    async def get_nodes_by_filters(self, collection, filters, return_fields=None) -> list[dict[str, Any]]:
        self.calls.append(("get_nodes_by_filters", (collection, dict(filters))))
        if collection == DEPARTMENTS:
            name = filters.get("departmentName")
            return [{"_key": self.departments[name]}] if name in self.departments else []
        return [
            {"_key": key, **node}
            for (coll, key), node in self.nodes.items()
            if coll == collection and all(node.get(f) == v for f, v in filters.items())
        ]

    async def batch_upsert_nodes(self, nodes, collection) -> bool:
        self.calls.append(("batch_upsert_nodes", (collection, [dict(n) for n in nodes])))
        for node in nodes:
            stored = {k: v for k, v in node.items() if k != "id"}
            self.nodes[(collection, node["id"])] = stored
        return True

    async def batch_update_nodes(self, nodes, collection) -> bool:
        return True

    async def get_edge(self, from_key, from_collection, to_key, to_collection, edge_collection) -> dict[str, Any] | None:
        return self.edges.get(
            (edge_collection, f"{from_collection}/{from_key}", f"{to_collection}/{to_key}")
        )

    async def get_edges_from_node_with_target_name(self, record_from, edge_collection) -> list[dict[str, Any]]:
        out = []
        for (coll, frm, to), edge in self.edges.items():
            if coll == edge_collection and frm == record_from:
                to_coll, to_key = to.split("/", 1)
                node = self.nodes.get((to_coll, to_key), {})
                out.append({"_to": to, "name": node.get("name", to_key), **edge})
        return out

    async def batch_create_edges(self, edges, edge_collection) -> bool:
        for edge in edges:
            frm = f"{edge['from_collection']}/{edge['from_id']}"
            to = f"{edge['to_collection']}/{edge['to_id']}"
            self.edges[(edge_collection, frm, to)] = dict(edge)
        return True

    async def batch_delete_edges(self, edges, edge_collection) -> int:
        deleted = 0
        for edge in edges:
            frm = f"{edge['from_collection']}/{edge['from_id']}"
            to = f"{edge['to_collection']}/{edge['to_id']}"
            if self.edges.pop((edge_collection, frm, to), None) is not None:
                deleted += 1
        return deleted


# ---------------------------------------------------------------------------
# Fake entity vector store
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


class FakeEntityVectorStore:
    """Stores EntityRecords and offers the best token-overlap match.

    No score threshold, matching the real store: as long as any point of the
    same org/type/level exists, one of them is the winner. ``force_winner``
    lets a scenario pin the winner for a query when overlap alone is a tie.
    """

    def __init__(self) -> None:
        self.points: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.upserts: list[list[Any]] = []
        self.match_calls: list[tuple[list[str], str, str, str | None]] = []
        self.fail_matches = False
        self.force_winner: dict[str, str] = {}

    def point(self, org_id: str, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        return self.points.get((org_id, entity_type, entity_id))

    def points_of(self, org_id: str, entity_type: str) -> list[dict[str, Any]]:
        return [p for (o, t, _k), p in self.points.items() if o == org_id and t == entity_type]

    async def upsert_entities_batch(self, entities, batch_size=64, *, merge_membership=True) -> None:
        self.upserts.append(list(entities))
        for entity in entities:
            key = (entity.org_id, entity.entity_type.value, entity.entity_id)
            existing = self.points.get(key)
            connector_ids = list(entity.connector_ids)
            record_group_ids = list(entity.record_group_ids)
            if existing and merge_membership:
                connector_ids = list(dict.fromkeys([*existing["connectorIds"], *connector_ids]))
                record_group_ids = list(
                    dict.fromkeys([*existing["recordGroupIds"], *record_group_ids])
                )
            self.points[key] = {
                "entityId": entity.entity_id,
                "entityType": entity.entity_type.value,
                "name": entity.name,
                "aliases": list(entity.aliases),
                "level": entity.level,
                "page_content": entity.embedding_text,
                "connectorIds": connector_ids,
                "recordGroupIds": record_group_ids,
            }

    async def find_best_matches(self, names, org_id, entity_type, level=None) -> list[dict[str, Any] | None]:
        self.match_calls.append((list(names), org_id, entity_type, level))
        if self.fail_matches:
            raise RuntimeError("vector store down")
        candidates = [
            p for (o, t, _k), p in self.points.items()
            if o == org_id and t == entity_type and (p.get("level") or None) == (level or None)
        ]
        results: list[dict[str, Any] | None] = []
        for name in names:
            if not candidates or not name.strip():
                results.append(None)
                continue
            forced = self.force_winner.get(name.casefold())
            best = None
            best_score = -1.0
            for candidate in candidates:
                if forced is not None:
                    if candidate["name"].casefold() == forced:
                        best = candidate
                        break
                    continue
                # Mirrors the real store: only the canonical name is searchable.
                haystack = candidate["name"]
                overlap = _tokens(name) & _tokens(haystack)
                union = _tokens(name) | _tokens(haystack)
                score = len(overlap) / len(union) if union else 0.0
                if score > best_score:
                    best, best_score = candidate, score
            if best is None:
                results.append(None)
                continue
            results.append({
                "entityId": best["entityId"],
                "entityType": best["entityType"],
                "name": best["name"],
                "aliases": list(best["aliases"]),
                "level": best.get("level"),
                "score": round(max(best_score, 0.0), 4),
            })
        return results


# ---------------------------------------------------------------------------
# Scripted model
# ---------------------------------------------------------------------------


_ITEMS_RE = re.compile(r"# Items\n(.*?)\n\n# Output", re.DOTALL)


def parse_prompt_items(prompt: str) -> list[dict[str, Any]]:
    match = _ITEMS_RE.search(prompt)
    assert match, "prompt has no items block"
    return json.loads(match.group(1))


class ScriptedModel:
    """Answers the merge prompt from a per-name script.

    Script values, keyed by the extracted name (case-insensitive):
      ("same", "<winner name>")      merge into the offered winner
      ("same_as", "<other name>")    same concept as another item
      ("new", "<canonical or ''>")   new entity
      "fail"                         make the call return None
      "bad_target"                   same with a made-up id
    Unlisted names are answered ``new`` with no canonical form.
    """

    def __init__(self, script: dict[str, Any] | None = None) -> None:
        self.script = {k.casefold(): v for k, v in (script or {}).items()}
        self.calls: list[list[dict[str, Any]]] = []
        self.raise_error = False

    async def __call__(self, llm, messages, schema, max_retries=2) -> MergeDecisions | None:
        if self.raise_error:
            raise RuntimeError("model down")
        items = parse_prompt_items(messages[0].content)
        self.calls.append(items)
        by_name = {item["name"].casefold(): item for item in items}
        decisions = []
        for item in items:
            spec = self.script.get(item["name"].casefold(), ("new", ""))
            if spec == "fail":
                return None
            if spec == "bad_target":
                decisions.append(MergeDecision(i=item["i"], same=True, target="not-offered"))
                continue
            kind, value = spec
            if kind == "same":
                match = item.get("match") or {}
                assert (match.get("name") or "").casefold() == value.casefold(), (
                    f"scenario expected winner {value!r} for {item['name']!r}, "
                    f"got {match.get('name')!r}"
                )
                decisions.append(MergeDecision(i=item["i"], same=True, target=match["id"]))
            elif kind == "same_as":
                other = by_name[value.casefold()]
                decisions.append(
                    MergeDecision(i=item["i"], same=True, same_as_item=other["i"])
                )
            else:
                decisions.append(MergeDecision(i=item["i"], same=False, canonical_name=value))
        return MergeDecisions(decisions=decisions)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def fake_store() -> FakeEntityVectorStore:
    return FakeEntityVectorStore()


@pytest.fixture
def scripted_model() -> Iterator[Callable[..., ScriptedModel]]:
    """Patch the model call; returns a factory that installs a script."""
    holder: dict[str, ScriptedModel] = {"model": ScriptedModel()}

    async def _invoke(llm, messages, schema, max_retries=2) -> MergeDecisions | None:
        return await holder["model"](llm, messages, schema, max_retries)

    with patch(
        "app.modules.entity_resolution.resolver.invoke_with_structured_output_and_reflection",
        side_effect=_invoke,
    ), patch(
        "app.modules.entity_resolution.resolver.get_llm_for_role",
        new=AsyncMock(return_value=(MagicMock(name="llm"), {})),
    ):
        def install(script: dict[str, Any] | None = None) -> ScriptedModel:
            holder["model"] = ScriptedModel(script)
            return holder["model"]

        install.current = lambda: holder["model"]  # type: ignore[attr-defined]
        yield install


@pytest.fixture
def make_resolver(fake_graph, fake_store) -> Callable[..., EntityResolver]:
    def _make(mode="apply", *, store=fake_store, graph=fake_graph, **kwargs) -> EntityResolver:
        from app.modules.entity_resolution.models import ResolutionMode

        if isinstance(mode, str):
            mode = ResolutionMode(mode)
        return EntityResolver(
            logger=MagicMock(),
            config_service=MagicMock(),
            graph_provider=graph,
            entity_vector_store=store,
            mode=mode,
            **kwargs,
        )

    return _make


def make_metadata(**kwargs) -> SemanticMetadata:
    defaults = {
        "summary": "A short summary.",
        "departments": [],
        "categories": [],
        "sub_category_level_1": None,
        "sub_category_level_2": None,
        "sub_category_level_3": None,
        "languages": [],
        "topics": [],
    }
    defaults.update(kwargs)
    return SemanticMetadata(**defaults)


def make_ctx(record_id: str, org_id: str, metadata: SemanticMetadata | None,
             connector_id: str = "conn-1", record_group_id: str = "rg-1") -> SimpleNamespace:
    record = SimpleNamespace(
        id=record_id,
        org_id=org_id,
        connector_id=connector_id,
        record_group_id=record_group_id,
        virtual_record_id=f"vr-{record_id}",
        semantic_metadata=metadata,
        is_vlm_ocr_processed=False,
    )
    return SimpleNamespace(record=record, entity_resolution=None, settings={})


@pytest.fixture
def metadata_factory() -> Callable[..., SemanticMetadata]:
    return make_metadata


@pytest.fixture
def ctx_factory() -> Callable[..., SimpleNamespace]:
    return make_ctx


@pytest.fixture
def make_transformer(fake_graph) -> Callable[[], GraphDBTransformer]:
    """A real GraphDBTransformer whose transaction yields the fake graph."""

    def _make() -> GraphDBTransformer:
        transformer = GraphDBTransformer(graph_provider=MagicMock(), logger=MagicMock())

        class _Txn:
            async def __aenter__(self_inner) -> FakeGraph:
                return fake_graph

            async def __aexit__(self_inner, *exc) -> bool:
                return False

        transformer.graph_data_store = MagicMock()
        transformer.graph_data_store.transaction = MagicMock(side_effect=lambda: _Txn())
        return transformer

    return _make
