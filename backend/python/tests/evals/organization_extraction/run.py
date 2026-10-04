"""Organisation extraction eval (KG-13 slice 3b).

Runs the real classification call (``DocumentExtraction.classify``, the same
prompt and schema indexing uses) on hand-labelled documents, filters the
names as indexing does (``usable_organization_names``), and scores them.

    python -m tests.evals.organization_extraction.run --model openai:<model> [--out report.json]
    python -m tests.evals.organization_extraction.run --model oracle   # no model: checks the harness

A real model reads ``TEST_OPENAI_API_KEY``. Matching is by
``organization_key``, so "Globex Corp." matches "Globex Corporation".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from app.models.blocks import Block, BlockType, DataFormat
from app.modules.entity_resolution.organizations import (
    organization_key,
    usable_organization_names,
)
from app.modules.transformers import document_extraction
from app.modules.transformers.document_extraction import DocumentExtraction

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

DATASET = Path(__file__).with_name("dataset.json")
logger = logging.getLogger("organization-extraction-eval")


@dataclass
class Score:
    predicted: int = 0
    correct: int = 0
    optional: int = 0
    gold: int = 0
    found: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add(self, doc: dict[str, Any], names: list[str]) -> None:
        gold = [{organization_key(a) for a in aliases} for aliases in doc["gold"]]
        optional = [{organization_key(a) for a in aliases} for aliases in doc.get("optional", [])]
        hit = [False] * len(gold)
        wrong = []
        for name in names:
            key = organization_key(name)
            self.predicted += 1
            match = next((i for i, keys in enumerate(gold) if key in keys), None)
            if match is not None:
                # A second spelling of one organisation is a duplicate node: wrong.
                if hit[match]:
                    wrong.append(name)
                else:
                    hit[match] = True
                    self.correct += 1
            elif any(key in keys for keys in optional):
                self.optional += 1
            else:
                wrong.append(name)
        missed = [doc["gold"][i][0] for i, h in enumerate(hit) if not h]
        self.gold += len(gold)
        self.found += sum(hit)
        if wrong or missed:
            self.errors.append({"id": doc["id"], "false_positives": wrong, "missed": missed})

    def report(self) -> dict[str, Any]:
        scored = self.predicted - self.optional
        precision = self.correct / scored if scored else 1.0
        recall = self.found / self.gold if self.gold else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {
            "predicted": self.predicted, "optional_hits": self.optional, "gold": self.gold,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "errors": self.errors,
        }


def _blocks(text: str) -> list[Block]:
    return [Block(type=BlockType.TEXT, format=DataFormat.TXT, data=text, index=0)]


def _model(spec: str) -> "BaseChatModel":
    provider, _, name = spec.partition(":")
    if provider != "openai" or not name:
        raise SystemExit(f"unknown model {spec!r}; use oracle or openai:<model>")
    key = os.environ.get("TEST_OPENAI_API_KEY")
    if not key:
        raise SystemExit("TEST_OPENAI_API_KEY is not set")
    from langchain_openai import ChatOpenAI

    # Indexing asks its model for low reasoning effort (get_llm_for_role).
    return ChatOpenAI(model=name, api_key=key, reasoning_effort="low")


async def _extract(extraction: DocumentExtraction, doc: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    classification = await extraction.classify(
        _blocks(doc["text"]), "eval-org", record_name=doc["record_name"], record_type=doc["record_type"],
    )
    return (list(classification.organizations) if classification else []), {}


async def evaluate(dataset: dict[str, Any], model: str, *, concurrency: int = 4) -> dict[str, Any]:
    raw, kept = Score(), Score()
    usage = {"input_tokens": 0, "output_tokens": 0}
    started = time.monotonic()
    if model == "oracle":
        outputs = {d["id"]: [g[0] for g in d["gold"]] + [dataset["tenant"]] for d in dataset["documents"]}
    else:
        llm = _model(model)

        async def llm_for_role(*_: object, **__: object) -> tuple[Any, dict[str, Any]]:
            return llm, {"isMultimodal": False, "contextLength": 128000}

        extraction = DocumentExtraction(logger, MagicMock(), MagicMock())
        gate = asyncio.Semaphore(concurrency)
        outputs = {}

        async def one(doc: dict[str, Any]) -> None:
            async with gate:
                outputs[doc["id"]], _ = await _extract(extraction, doc)

        with patch.object(document_extraction, "get_llm_for_role", llm_for_role):
            from langchain_core.callbacks import UsageMetadataCallbackHandler

            tracker = UsageMetadataCallbackHandler()
            llm.callbacks = [tracker]
            await asyncio.gather(*(one(d) for d in dataset["documents"]))
            for meta in tracker.usage_metadata.values():
                usage["input_tokens"] += meta.get("input_tokens", 0)
                usage["output_tokens"] += meta.get("output_tokens", 0)
    for doc in dataset["documents"]:
        names = outputs.get(doc["id"], [])
        raw.add(doc, names)
        kept.add(doc, usable_organization_names(
            names, tenant_name=dataset["tenant"], connector_name=doc["connector"],
        ))
    return {
        "model": model, "documents": len(dataset["documents"]), "seconds": round(time.monotonic() - started, 1),
        "usage": usage, "extracted": raw.report(), "after_filter": kept.report(),
        "outputs": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="oracle")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate(json.loads(args.dataset.read_text()), args.model))
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text)
    summary = {k: report[k] for k in ("model", "documents", "seconds", "usage")}
    for stage in ("extracted", "after_filter"):
        summary[stage] = {k: v for k, v in report[stage].items() if k != "errors"}
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
