"""Shared fakes for the entity-resolution suites.

Everything here is in-memory and deterministic so the end-to-end scenario
tests can drive the real resolver, the real graph transformer and the real
sink orchestrator without a database, a vector store or a model.
"""

from __future__ import annotations

import json
import re
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.blocks import SemanticMetadata
from app.modules.entity_resolution.models import MergeDecision, MergeDecisions
from app.modules.entity_resolution.normalizer import normalize_name
from app.modules.entity_resolution.resolver import EntityResolver
from app.modules.transformers.entity_vectorstore import EntityWriteOutcome
from app.modules.transformers.graphdb import GraphDBTransformer
from tests.support.fake_entity_graph import FakeGraph

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_TAXONOMY_COLLECTIONS: dict[tuple[str, str | None], str] = {
    ("category", None): CollectionNames.CATEGORIES.value,
    ("topic", None): CollectionNames.TOPICS.value,
    ("language", None): CollectionNames.LANGUAGES.value,
    ("subcategory", "1"): CollectionNames.SUBCATEGORIES1.value,
    ("subcategory", "2"): CollectionNames.SUBCATEGORIES2.value,
    ("subcategory", "3"): CollectionNames.SUBCATEGORIES3.value,
}


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

    def __init__(self, graph: FakeGraph | None = None) -> None:
        self.points: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.upserts: list[list[Any]] = []
        self.match_calls: list[tuple[list[str], str, str, str | None]] = []
        self.fail_matches = False
        self.force_winner: dict[str, str] = {}
        self.graph = graph

    def _back_with_graph_node(self, entity) -> None:
        """Every real entity point is projected from a graph node; seeding a
        point creates its per-org node so the resolver's winner check finds it."""
        if self.graph is None:
            return
        collection = _TAXONOMY_COLLECTIONS.get((entity.entity_type.value, entity.level))
        if collection is None or (collection, entity.entity_id) in self.graph.nodes:
            return
        self.graph.nodes[(collection, entity.entity_id)] = {
            "name": entity.name,
            "normalizedName": normalize_name(entity.name),
            "orgId": entity.org_id,
            "aliases": list(entity.aliases),
            "normalizedAliases": [normalize_name(a) for a in entity.aliases],
        }

    def point(self, org_id: str, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        return self.points.get((org_id, entity_type, entity_id))

    def points_of(self, org_id: str, entity_type: str) -> list[dict[str, Any]]:
        return [p for (o, t, _k), p in self.points.items() if o == org_id and t == entity_type]

    async def upsert_entities_batch(self, entities, batch_size=64, *, merge_membership=True) -> EntityWriteOutcome:
        self.upserts.append(list(entities))
        for entity in entities:
            self._back_with_graph_node(entity)
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
        return EntityWriteOutcome(written=len(entities))

    async def find_candidates(self, names, org_id, entity_type, level=None, *, k=3) -> list[list[dict[str, Any]]]:
        """Token-overlap ranking over the canonical names, best first.
        ``force_winner`` pins the first candidate for a name."""
        self.match_calls.append((list(names), org_id, entity_type, level))
        if self.fail_matches:
            raise RuntimeError("vector store down")
        candidates = [
            p for (o, t, _k), p in self.points.items()
            if o == org_id and t == entity_type and (p.get("level") or None) == (level or None)
        ]
        results: list[list[dict[str, Any]]] = []
        for name in names:
            if not candidates or not name.strip():
                results.append([])
                continue

            def _score(candidate: dict[str, Any], name: str = name) -> float:
                # Mirrors the real store: only the canonical name is searchable.
                overlap = _tokens(name) & _tokens(candidate["name"])
                union = _tokens(name) | _tokens(candidate["name"])
                return len(overlap) / len(union) if union else 0.0

            ranked = sorted(candidates, key=lambda c: (-_score(c), c["entityId"]))
            forced = self.force_winner.get(name.casefold())
            if forced is not None:
                ranked = [c for c in ranked if c["name"].casefold() == forced]
            results.append([
                {
                    "entityId": c["entityId"],
                    "entityType": c["entityType"],
                    "name": c["name"],
                    "aliases": list(c["aliases"]),
                    "level": c.get("level"),
                    "score": round(_score(c), 4),
                }
                for c in ranked[:k]
            ])
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

    async def __call__(self, llm, messages, schema, max_retries=2, **_kwargs) -> MergeDecisions | None:
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
                offered = {m["name"].casefold(): m["id"] for m in item.get("matches") or []}
                assert value.casefold() in offered, (
                    f"scenario expected winner {value!r} for {item['name']!r} among {sorted(offered)}"
                )
                decisions.append(MergeDecision(i=item["i"], same=True, target=offered[value.casefold()]))
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
def fake_store(fake_graph) -> FakeEntityVectorStore:
    return FakeEntityVectorStore(fake_graph)


@pytest.fixture
def scripted_model() -> Iterator[Callable[..., ScriptedModel]]:
    """Patch the model call; returns a factory that installs a script."""
    holder: dict[str, ScriptedModel] = {"model": ScriptedModel()}

    async def _invoke(llm, messages, schema, max_retries=2, **kwargs) -> MergeDecisions | None:
        return await holder["model"](llm, messages, schema, max_retries, **kwargs)

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
        transformer.graph_data_store.graph_provider = fake_graph
        transformer.graph_data_store.transaction = MagicMock(side_effect=lambda: _Txn())
        transformer.graph_data_store.execute_idempotent_in_transaction = partial(
            GraphDataStore.execute_idempotent_in_transaction, transformer.graph_data_store,
        )
        return transformer

    return _make
