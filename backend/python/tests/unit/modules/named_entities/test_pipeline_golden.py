"""Extraction, resolution and the graph write give the frozen output.

Fixed documents and fixed model replies go through the single-call extractor,
the resolver and the graph writer; everything they produce is compared with
``fixtures/pipeline_golden_v1.json``. A performance change must leave this
untouched. Regenerate only for an intended behaviour change:
``NER_GOLDEN_REGENERATE=1 pytest <this file>``.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.modules.entity_resolution.models import ResolutionMode
from app.modules.named_entities import graph_writer as graph_writer_module
from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import default_enabled_kinds
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
)
from app.modules.named_entities.graph_writer import NamedEntityGraphWriter
from app.modules.named_entities.resolution import NamedEntityResolver
from app.modules.named_entities.strategies.single_call import (
    SingleCallExtractionStrategy,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

FIXTURE = Path(__file__).parent / "fixtures" / "pipeline_golden_v1.json"
ORG = "org-golden"
NOW_MS = 1767225600000

LONG_TAIL = " ".join(f"Filler sentence {i} about routine operations." for i in range(120))

DOCS: list[dict] = [
    {
        "name": "incident",
        "reference_ms": 1767225600000,
        "blocks": [
            "Maria Gonzalez from Acme Payments Inc. opened ticket 4411 on March 3, 2026.",
            "The rollout to prod/us-west-2 at 2026-03-04T04:12:08Z broke parsing; refunds of -$1,250.50 "
            "and ($300) were issued. Contact maria.gonzalez@acmepay.example or +1 (415) 555-0134.",
            "Sam Patel will follow up tomorrow and again next Friday. Latency rose 35% to 450 ms over 3 days.",
            "Card 4111 1111 1111 1111 must never be stored. Server 10.0.4.17 restarted at 14:00 UTC.",
            "Docs: https://docs.acmepay.example/runbooks/rollback?id=7&utm_source=mail for the Q3 2026 review.",
        ],
        "items": [
            {"text": "Maria Gonzalez", "block": 0, "kind": "person"},
            {"text": "Acme Payments Inc.", "block": 0, "kind": "organization"},
            {"text": "March 3, 2026", "block": 0, "kind": "date"},
            {"text": "2026-03-04T04:12:08Z", "block": 1, "kind": "date_time"},
            {"text": "-$1,250.50", "block": 1, "kind": "currency"},
            {"text": "Sam Patel", "block": 2, "kind": "person"},
            {"text": "tomorrow", "block": 2, "kind": "date"},
            {"text": "next Friday", "block": 2, "kind": "date"},
            {"text": "14:00 UTC", "block": 3, "kind": "date_time"},
            {"text": "Q3 2026", "block": 4, "kind": "date_range"},
            {"text": "Acme Payment", "block": 1, "kind": "organization"},
            {"text": "Mariah Gonzales", "block": 0, "kind": "person"},
            {"text": "Sam  Patel", "block": 2, "kind": "PERSON"},
            {"text": "4111 1111 1111 1111", "block": 3, "kind": "person"},
            {"text": "ticket", "block": 0, "kind": "skill"},
            {"text": "Friday", "block": 2, "kind": "date"},
            {"text": "35%", "block": 2, "kind": "percentage"},
            {"text": "450 ms", "block": 2, "kind": "duration"},
            {"text": "Kubernetes", "block": 9, "kind": "product"},
        ],
    },
    {
        "name": "contract",
        "reference_ms": 1780000000000,
        "blocks": [
            "Müller & Söhne GmbH signs with Zoë Café Ltd. in São Paulo, Brazil, effective 15 March 2027.",
            "The fee is EUR 12,500 per quarter (€50k a year), payable within 30 days; FY2027 budget 2.5m USD.",
            "Jane O’Neil (CFO) and jane o'neil both approved. The 5 km site is 72 F in June.",
            LONG_TAIL + " Jane O'Neil reviewed the appendix. " + LONG_TAIL,
            "Escalation: last week, this quarter, 12 April, 09/18-09, and EOD Wed are open questions.",
        ],
        "items": [
            {"text": "Müller & Söhne GmbH", "block": 0, "kind": "organization"},
            {"text": "Zoë Café Ltd.", "block": 0, "kind": "organization"},
            {"text": "São Paulo", "block": 0, "kind": "city"},
            {"text": "Brazil", "block": 0, "kind": "country_region"},
            {"text": "15 March 2027", "block": 0, "kind": "date"},
            {"text": "EUR 12,500", "block": 1, "kind": "currency"},
            {"text": "€50k", "block": 1, "kind": "currency"},
            {"text": "FY2027", "block": 1, "kind": "date_range"},
            {"text": "2.5m USD", "block": 1, "kind": "currency"},
            {"text": "Jane O'Neil", "block": 2, "kind": "person"},
            {"text": "CFO", "block": 2, "kind": "person_type"},
            {"text": "jane o'neil", "block": 2, "kind": "person"},
            {"text": "5 km", "block": 2, "kind": "dimension"},
            {"text": "72 F", "block": 2, "kind": "dimension"},
            {"text": "June", "block": 2, "kind": "date"},
            {"text": "Jane O'Neil", "block": 3, "kind": "person"},
            {"text": "Jane ONeil", "block": 3, "kind": "person"},
            {"text": "appendix review", "block": 3, "kind": "event"},
            {"text": "last week", "block": 4, "kind": "date_range"},
            {"text": "this quarter", "block": 4, "kind": "date_range"},
            {"text": "12 April", "block": 4, "kind": "date"},
            {"text": "09/18-09", "block": 4, "kind": "date"},
            {"text": "EOD Wed", "block": 4, "kind": "date_time"},
        ],
    },
    {
        "name": "chat",
        "reference_ms": None,
        "blocks": [
            "raj: deploy-bot rolled redwood-instruct-2025-v2.1 to 100% after the canary.",
            "alex: assigning to eng-ml; Priya Raman owns the formatter fix in Redwood Labs.",
        ],
        "items": [
            {"text": "redwood-instruct-2025-v2.1", "block": 0, "kind": "product"},
            {"text": "100%", "block": 0, "kind": "percentage"},
            {"text": "Priya Raman", "block": 1, "kind": "person"},
            {"text": "Redwood Labs", "block": 1, "kind": "organization"},
            {"text": "eng-ml", "block": 1, "kind": "organization"},
        ],
    },
]

# Rows the graph already holds, so the resolver takes its merge and alias paths.
EXISTING = {
    "person": [
        {"_key": "node-maria", "normKey": "name:person:maria gonzalez", "normalizedAliases": []},
        {"_key": "node-jane", "normKey": "name:person:j. o'neil", "normalizedAliases": ["name:person:jane o'neil"]},
    ],
    "organization": [
        {"_key": "node-acme", "normKey": "name:organization:acme payments", "mergedInto": "node-acme-2"},
    ],
}


class _Graph:
    def __init__(self) -> None:
        self.calls: list = []

    async def find_named_entities(self, org_id: str, kind: str, keys: list[str]) -> list[dict]:
        self.calls.append(["find", kind, sorted(keys)])
        return [row for row in EXISTING.get(kind, []) if row["normKey"] in keys or set(row.get("normalizedAliases") or []) & set(keys)]

    async def resolve_named_entity_redirects(self, org_id: str, ids: list[str]) -> dict[str, str]:
        return {"node-acme": "node-acme-2"} if "node-acme" in ids else {}

    async def create_named_entities_if_absent(self, docs: list[dict]) -> None:
        self.calls.append(["create", docs])

    async def add_named_entity_aliases(self, *args: object) -> None:
        self.calls.append(["alias", list(args)])

    async def clear_named_entity_persist_retry(self, record_id: str, due: int | None, txn: str | None) -> bool:
        return True

    async def replace_named_entity_values(self, record_id: str, values: list[dict], txn: str | None) -> None:
        self.calls.append(["values", record_id, values])

    def is_named_entity_write_retryable(self, error: BaseException) -> bool:
        return False


class _Tx:
    txn = "tx"

    def __init__(self, graph: _Graph) -> None:
        self._graph = graph

    async def get_edges_from_node_with_target_name(self, *args: object, **kwargs: object) -> list:
        return []

    async def batch_create_edges(self, edges: list[dict], collection: str) -> None:
        self._graph.calls.append(["edges", collection, edges])

    async def batch_delete_edges(self, edges: list[dict], collection: str) -> int:
        return 0


class _Store:
    def __init__(self, graph: _Graph) -> None:
        self._graph = graph

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Tx]:
        yield _Tx(self._graph)


def _replier(doc: dict) -> Callable[[str, str], Awaitable[str]]:
    """Answers each window with the items of the blocks it shows; an item naming
    a block that does not exist comes with the first window."""
    calls = 0

    async def complete(_system: str, prompt: str) -> str:
        nonlocal calls
        calls += 1
        shown = {int(n) for n in re.findall(r"^\[B(\d+)\]", prompt, re.MULTILINE)}
        items = [
            item for item in doc["items"]
            if item["block"] in shown or (calls == 1 and item["block"] >= len(doc["blocks"]))
        ]
        return json.dumps({"entities": [{**item, "normalized": ""} for item in items]})

    return complete


def _strip_timestamps(value: object) -> object:
    if isinstance(value, dict):
        return {k: _strip_timestamps(v) for k, v in value.items() if not k.endswith("Timestamp")}
    if isinstance(value, list):
        return [_strip_timestamps(v) for v in value]
    return value


async def _run(doc: dict) -> dict:
    blocks = [SimpleNamespace(index=i, id=f"{doc['name']}-b{i}", type="text", data=text) for i, text in enumerate(doc["blocks"])]
    single = SingleCallExtractionStrategy(complete=_replier(doc))
    request = NamedEntityRequest(
        blocks=blocks,
        org_id=ORG,
        record_name=doc["name"],
        record_type="FILE",
        reference_time_ms=doc["reference_ms"],
        tz="UTC",
        enabled=default_enabled_kinds(),
        budgets=NamedEntityBudgets(max_window_chars=2_000),
        supports_tools=False,
    )
    extraction = await NamedEntityExtractor(single=single).extract(request)
    graph = _Graph()
    resolved = await NamedEntityResolver(graph, mode=ResolutionMode.APPLY).resolve(ORG, extraction.entities)
    embeddable = await NamedEntityGraphWriter(graph, _Store(graph)).write(ORG, f"rec-{doc['name']}", resolved)
    stored = extraction.model_dump(mode="json")
    stored["stats"].pop("extract_ms")
    return _strip_timestamps({
        "extraction": stored,
        "resolved": [[r.graph_key, r.merged_into, r.shadow_target, r.entity.norm_key] for r in resolved],
        "graph": graph.calls,
        "embeddable": [record.model_dump(mode="json") for record in embeddable],
    })


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_writer_module, "get_epoch_timestamp_in_ms", lambda: NOW_MS)


async def test_pipeline_output_is_unchanged() -> None:
    # "chat" has no reference time; it holds no relative date, so the clock does not matter.
    produced = json.loads(json.dumps({doc["name"]: await _run(doc) for doc in DOCS}, sort_keys=True, default=str))
    if os.getenv("NER_GOLDEN_REGENERATE"):
        FIXTURE.write_text(json.dumps(produced, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    expected = json.loads(FIXTURE.read_text())
    assert produced.keys() == expected.keys()
    for name in expected:
        assert produced[name] == expected[name], name
