"""Entity-resolution evaluation (KG-15, F1).

Runs ``dataset.json`` through the production pipeline — ``EntityResolver``,
``GraphDBTransformer`` and ``EntityVectorStore`` — record by record, over an
in-memory graph and vector service, and scores which node each extracted
name landed on against the gold entity (pairwise and B-cubed, see
``metrics.py``).

Choices that change the numbers:

- ``--embeddings bge`` (default) uses BAAI/bge-large-en-v1.5 through
  sentence-transformers, so tier 1 offers the winners it would in
  production with that model; ``hash`` is a deterministic stub with no
  semantic signal, useful only to check the harness.
- ``--model none`` (default) answers no merge question, so only exact and
  alias matches merge. ``oracle`` answers every question from the gold
  labels: the score is then the ceiling the candidate search allows (one
  offered winner per name, KG-12), whatever model is used.
  ``openai:<model>`` asks a real model (needs ``OPENAI_API_KEY``), which
  costs money.

  cd backend/python
  python -m tests.evals.entity_resolution.run --embeddings bge --model none
  python -m tests.evals.entity_resolution.run --model openai:gpt-4.1-mini --out report.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.blocks import SemanticMetadata
from app.modules.entity_resolution.models import (
    MergeDecision,
    MergeDecisions,
    ResolutionMode,
)
from app.modules.entity_resolution.normalizer import display_form
from app.modules.entity_resolution.resolver import EntityResolver
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.modules.transformers.graphdb import GraphDBTransformer
from tests.evals.entity_resolution.metrics import bcubed, pairwise
from tests.support.embedding_config import config_service as embedding_config_service
from tests.support.fake_entity_graph import FakeGraph
from tests.support.in_memory_vector_db import InMemoryVectorDBService

DATASET = Path(__file__).with_name("dataset.json")
logger = logging.getLogger("entity-resolution-eval")


class HashEmbeddings:
    """Deterministic vectors with no meaning: equal text, equal vector."""

    dimension = 16

    def embed_query(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.casefold().encode()).digest()
        raw = [b / 255.0 - 0.5 for b in digest[: self.dimension]]
        norm = sum(x * x for x in raw) ** 0.5 or 1.0
        return [x / norm for x in raw]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


class SentenceTransformerEmbeddings:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dimension = self._model.get_sentence_embedding_dimension()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.encode(texts, normalize_embeddings=True)]


Embeddings = HashEmbeddings | SentenceTransformerEmbeddings


def _embeddings(name: str) -> Embeddings:
    if name == "hash":
        return HashEmbeddings()
    if name == "bge":
        return SentenceTransformerEmbeddings("BAAI/bge-large-en-v1.5")
    raise ValueError(f"unknown embeddings {name!r}")


_PROMPT_ITEMS = re.compile(r"# Items\n(.*?)\n\n# Output", re.DOTALL)


class GoldOracle:
    """Answers the merge prompt from the gold labels: merge into the offered
    node when it holds the same entity, group items of one entity, else new.
    The node's entity is the gold entity of the first name linked to it."""

    def __init__(self) -> None:
        self.record_gold: dict[str, str] = {}
        self.node_entity: dict[str, str] = {}

    def start_record(self, gold: dict[str, str]) -> None:
        self.record_gold = {display_form(raw): entity for raw, entity in gold.items()}

    def learn(self, node_key: str, entity: str) -> None:
        self.node_entity.setdefault(node_key, entity)

    async def __call__(self, llm: object, messages: list, schema: object, **_: object) -> MergeDecisions:
        found = _PROMPT_ITEMS.search(messages[0].content)
        if found is None:
            # The resolver counts this as a failed model call; evaluate()
            # then refuses to report the run.
            raise AssertionError("merge prompt layout changed; update GoldOracle._PROMPT_ITEMS")
        items = json.loads(found.group(1))
        first_of_entity: dict[str, int] = {}
        decisions = []
        for item in items:
            entity = self.record_gold.get(item["name"])
            match = item.get("match") or {}
            if entity and match.get("id") and self.node_entity.get(match["id"]) == entity:
                decisions.append(MergeDecision(i=item["i"], same=True, target=match["id"]))
            elif entity in first_of_entity:
                decisions.append(MergeDecision(i=item["i"], same=True, same_as_item=first_of_entity[entity]))
            else:
                if entity:
                    first_of_entity[entity] = item["i"]
                decisions.append(MergeDecision(i=item["i"], same=False))
        return MergeDecisions(decisions=decisions)


def _model(spec: str) -> object | None:
    if spec in ("none", "oracle"):
        return None
    provider, _, name = spec.partition(":")
    if provider != "openai" or not name:
        raise ValueError(f"unknown model {spec!r}; use none, oracle or openai:<model>")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=name, temperature=0)


async def _store(service: InMemoryVectorDBService, embeddings: Embeddings) -> EntityVectorStore:
    store = EntityVectorStore(
        logger=logger, config_service=embedding_config_service(), vector_db_service=service,
        collection_name="entities_eval",
    )

    async def _use_embeddings(embedding_configs: list | None = None) -> None:
        store._dense_embeddings = embeddings
        store._embedding_size = embeddings.dimension
        store._model_id = "eval"

    store._init_embeddings = _use_embeddings  # type: ignore[method-assign]
    await store._ensure_initialized()
    return store


def _transformer(graph: FakeGraph) -> GraphDBTransformer:
    """The real transformer, its transaction yielding the in-memory graph."""
    transformer = GraphDBTransformer(graph_provider=MagicMock(), logger=logger)

    class _Txn:
        async def __aenter__(self) -> FakeGraph:
            return graph

        async def __aexit__(self, *exc: object) -> bool:
            return False

    transformer.graph_data_store = MagicMock()
    transformer.graph_data_store.graph_provider = graph
    transformer.graph_data_store.transaction = MagicMock(side_effect=lambda: _Txn())
    transformer.graph_data_store.execute_idempotent_in_transaction = partial(
        GraphDataStore.execute_idempotent_in_transaction, transformer.graph_data_store,
    )
    return transformer


async def evaluate(
    dataset: dict[str, Any], *, embeddings: Embeddings, llm: object | None, oracle: GoldOracle | None = None,
) -> dict[str, Any]:
    graph = FakeGraph()
    store = await _store(InMemoryVectorDBService(), embeddings)
    resolver = EntityResolver(
        logger=logger, config_service=MagicMock(), graph_provider=graph,
        entity_vector_store=store, mode=ResolutionMode.APPLY,
    )
    transformer = _transformer(graph)
    org = dataset["org"]
    gold: dict[tuple[str, str], str] = {}
    predicted: dict[tuple[str, str], str] = {}
    model_calls = merges = 0

    patches = [patch(
        "app.modules.entity_resolution.resolver.get_llm_for_role",
        new=AsyncMock(return_value=(llm or MagicMock(name="no-model"), {})),
    )]
    if llm is None:
        patches.append(patch(
            "app.modules.entity_resolution.resolver.invoke_with_structured_output_and_reflection",
            new=oracle if oracle is not None else AsyncMock(return_value=None),
        ))
    for active in patches:
        active.start()
    try:
        for record in dataset["records"]:
            record_id = record["id"]
            graph.add_record(record_id, org)
            if oracle is not None:
                oracle.start_record(record["gold"])
            metadata = SemanticMetadata(
                summary="", departments=[], categories=[], languages=[], topics=list(record["topics"]),
            )
            ctx = SimpleNamespace(
                record=SimpleNamespace(
                    id=record_id, org_id=org, connector_id="conn-eval", record_group_id="rg-eval",
                    virtual_record_id=f"vr-{record_id}", semantic_metadata=metadata, is_vlm_ocr_processed=False,
                ),
                entity_resolution=None, settings={},
            )
            resolution = await resolver.resolve(ctx)
            if oracle is not None and resolution.stats.model_failures:
                # The resolver falls back to "new" on a failed call, which
                # would report no-model scores as the oracle's.
                raise RuntimeError(f"the gold oracle failed on record {record_id}; not scoring the run")
            model_calls += resolution.stats.model_calls
            merges += resolution.stats.merges
            touched = await transformer.save_metadata_to_db(
                record_id, metadata, f"vr-{record_id}", resolution=resolution,
            )
            await store.upsert_entities_batch(touched)
            for entity in resolution.entries.values():
                for raw in entity.extracted_names:
                    predicted[(record_id, raw)] = entity.key
                    if oracle is not None and raw in record["gold"]:
                        oracle.learn(entity.key, record["gold"][raw])
            for raw, cluster in record["gold"].items():
                gold[(record_id, raw)] = cluster
    finally:
        for active in patches:
            active.stop()

    missing = set(gold) - set(predicted)
    # A name the resolver dropped is its own cluster: it merged with nothing.
    for mention in missing:
        predicted[mention] = f"dropped:{mention}"
    predicted = {m: predicted[m] for m in gold}
    return {
        "mentions": len(gold),
        "gold_entities": len(set(gold.values())),
        "predicted_nodes": len(set(predicted.values())),
        "dropped": len(missing),
        "model_calls": model_calls,
        "model_merges": merges,
        "pairwise": pairwise(gold, predicted).as_dict(),
        "bcubed": bcubed(gold, predicted).as_dict(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--embeddings", default="bge", choices=["bge", "hash"])
    parser.add_argument("--model", default="none", help="none, oracle or openai:<model>")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.WARNING)
    dataset = json.loads(args.dataset.read_text())
    report = asyncio.run(evaluate(
        dataset, embeddings=_embeddings(args.embeddings), llm=_model(args.model),
        oracle=GoldOracle() if args.model == "oracle" else None,
    ))
    report["config"] = {"embeddings": args.embeddings, "model": args.model, "dataset": args.dataset.name}
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
