"""How much text the assistant's searches put in the prompt, TARGETED_RETRIEVAL off and on.

Replays the shape of a knowledge question through the real search code
(`execute_search`: context builder, per-record cap, fit and render) and the
real fetch code (`execute_fetch_record`), with retrieval, blob loading and
graph enrichment stood in for. Nothing calls a model or a vector store, so it
runs offline and gives the same numbers on every run.

The corpus and questions are synthetic but shaped like a mixed workspace:
long Slack threads and pull requests (a hit inside one expands to the whole
section, as `get_flattened_results` does), short tickets, and long documents.
Each question runs a wave of 1-3 searches whose hits overlap, in the mix the
assistant's own runs show (about 3 searches per answer, most in waves of 2).

Per question it reports the characters the searches return, and, for the
"on" column, adds focused reads of the two best records (the most a question
would add if the model reads the right part of each). It also reports whether
one planted detail, a mid-ranked hit inside the best record, was shown by the
searches: the per-record cap makes that less likely, and the focused read is
what is meant to recover it.

Characters become estimated tokens at the ratio measured on real search
results: about 27,000 prompt tokens per 64,000-character result.

Run from backend/python:

    python tests/perf/bench_targeted_retrieval.py [--questions 40] [--seed 7]

Not collected by pytest (no ``test_`` prefix).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agents.actions.knowledge_graph.ops.fetch import (
    execute_fetch_record,
)
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.models.blocks import BlockType, GroupType

TOKENS_PER_CHAR = 27_000 / 64_000
HITS_PER_SEARCH = 50
_BUILDER = "app.modules.retrieval.context.builder"
_SEARCH = "app.agents.actions.knowledge_graph.ops.search"
_WORDS = (
    "sync connector index record thread release image tag deploy config retry "
    "event permission token kafka worker migration schema field status merged "
    "review comment branch docker helm chart value rollout operator team owner"
).split()


def _text(rng: random.Random, chars: int) -> str:
    words: list[str] = []
    size = 0
    while size < chars:
        word = rng.choice(_WORDS)
        words.append(word)
        size += len(word) + 1
    return " ".join(words)[:chars]


def _record(rng: random.Random, kind: str, n: int) -> dict[str, Any]:
    vrid = f"{kind}-{n}"
    if kind in ("thread", "pr"):
        items = rng.randint(20, 90) if kind == "thread" else rng.randint(30, 120)
        low, high = (200, 700) if kind == "thread" else (300, 1500)
        blocks = [
            {"index": i, "type": BlockType.TEXT.value, "parent_index": 0,
             "data": f"[{vrid} item {i}] " + _text(rng, rng.randint(low, high))}
            for i in range(items)
        ]
        groups = [{
            "index": 0,
            "type": GroupType.CONVERSATION.value if kind == "thread" else GroupType.LIST.value,
            "children": {"block_ranges": [{"start": 0, "end": items - 1}]},
        }]
    else:
        count, low, high = (rng.randint(5, 15), 200, 800) if kind == "ticket" else (rng.randint(80, 300), 400, 1200)
        blocks = [
            {"index": i, "type": BlockType.TEXT.value, "data": f"[{vrid} para {i}] " + _text(rng, rng.randint(low, high))}
            for i in range(count)
        ]
        groups = []
    return {
        "id": f"rec-{vrid}",
        "_id": f"records/rec-{vrid}",
        "virtual_record_id": vrid,
        "record_name": f"{kind} {n}",
        "record_type": "FILE",
        "context_metadata": (
            f"Record ID: rec-{vrid}\nName: {kind} {n}\nConnector: X\nType: {kind}\n"
            f"Web URL: https://example.test/{vrid}\nSummary: " + _text(rng, 400)
        ),
        "frontend_url": "https://app.example",
        "block_containers": {"blocks": blocks, "block_groups": groups},
    }


def _corpus(rng: random.Random) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for kind, count in (("thread", 14), ("pr", 12), ("ticket", 20), ("doc", 10)):
        for n in range(count):
            record = _record(rng, kind, n)
            records[record["virtual_record_id"]] = record
    return records


def _units_for(hits: list[tuple[str, int]], records: dict[str, Any]) -> list[dict[str, Any]]:
    """What `get_flattened_results` makes of ranked hits: a hit inside a
    section brings the whole section once, with every matched item noted."""
    units: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    for rank, (vrid, index) in enumerate(hits):
        score = 1 / (60 + rank)
        record = records[vrid]
        blocks = record["block_containers"]["blocks"]
        if record["block_containers"]["block_groups"]:
            unit = groups.get(vrid)
            if unit is None:
                children = [
                    {"virtual_record_id": vrid, "block_index": b["index"], "block_type": BlockType.TEXT.value,
                     "content": b["data"], "metadata": {}, "score": score}
                    for b in blocks
                ]
                unit = groups[vrid] = {
                    "virtual_record_id": vrid, "block_index": 0, "block_group_index": 0,
                    "block_type": record["block_containers"]["block_groups"][0]["type"],
                    "content": ("", children), "hit_block_scores": {}, "metadata": {}, "score": score,
                }
                units.append(unit)
            unit["hit_block_scores"].setdefault(index, score)
            continue
        units.append({
            "virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value,
            "content": blocks[index]["data"], "metadata": {}, "score": score,
        })
    return units


def _question(rng: random.Random, records: dict[str, Any]) -> dict[str, Any]:
    """A cluster of relevant records, a wave of overlapping searches, and one
    planted detail: a mid-ranked hit inside the best record."""
    cluster = rng.sample(sorted(records), 7)
    waves = rng.choices([1, 2, 3], weights=[105, 163, 56])[0]
    shared = [(vrid, rng.randrange(len(records[vrid]["block_containers"]["blocks"])))
              for vrid in cluster for _ in range(5)]
    searches = []
    for _ in range(waves):
        own = [(vrid, rng.randrange(len(records[vrid]["block_containers"]["blocks"])))
               for vrid in rng.sample(sorted(records), 12) for _ in range(2)]
        hits = list(dict.fromkeys(rng.sample(shared, 26) + own))
        rng.shuffle(hits)
        hits.sort(key=lambda hit: hit[0] not in cluster[:3])
        searches.append(hits[:HITS_PER_SEARCH])
    best = searches[0][0][0]
    blocks = records[best]["block_containers"]["blocks"]
    needle = (best, rng.randrange(len(blocks)))
    if needle not in searches[0]:
        searches[0].insert(12, needle)
        searches[0] = searches[0][:HITS_PER_SEARCH]
    return {"searches": searches, "best": [h[0] for h in dict.fromkeys(searches[0])][:2], "needle": needle}


def _state() -> dict[str, Any]:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200, "searchResults": [{"virtual_record_id": "x"}], "virtual_to_record_map": {},
    }
    return {
        "logger": MagicMock(), "retrieval_service": retrieval, "graph_provider": AsyncMock(),
        "config_service": None, "org_id": "o1", "user_id": "u1", "filters": {"apps": ["app-1"], "kb": []},
    }


async def _search(state: dict[str, Any], records: dict[str, Any], hits: list[tuple[str, int]]) -> str:
    units = _units_for(hits, records)

    async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
        vr_map.update({vrid: records[vrid] for vrid, _ in hits})
        return units

    with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
            patch(f"{_BUILDER}.enrich_records_with_address_links", new_callable=AsyncMock), \
            patch(f"{_SEARCH}.BlobStorage"):
        result = await execute_search(state, "q")
    return result if isinstance(result, str) else "".join(getattr(p, "text", "") for p in result)


async def _focused_read(records: dict[str, Any], vrids: list[str], needle: tuple[str, int]) -> str:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(side_effect=lambda **kw: {"searchResults": [
        {"metadata": {"virtualRecordId": vrid, "blockIndex": needle[1] if vrid == needle[0] else 0}, "score": 1.0}
        for vrid in kw["virtual_record_ids_from_tool"]
    ]})
    graph = AsyncMock()
    graph.config_service = MagicMock()
    context = SimpleNamespace(
        model_name="m", org_id="o1", user_id="u1", graph_provider=graph, full_records_fetched=set(),
        tool_state={}, is_multimodal_llm=False, context_length=200_000, query="q",
        retrieval_service=retrieval, needs_whole_document=False, include_retrieval_context=False,
    )
    structured = MagicMock()
    structured.coroutine = AsyncMock(return_value={
        "ok": True, "records": [records[v] for v in vrids], "not_available_ids": [],
    })
    with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=structured):
        output, _ = await execute_fetch_record(
            context=context, virtual_records={}, citation_ref_mapper=None,
            record_ids=[records[v]["id"] for v in vrids], reason="detail", query="the detail",
        )
    return str(output.data)


def _shown(text: str, needle: tuple[str, int]) -> bool:
    vrid, index = needle
    return f"[{vrid} item {index}] " in text or f"[{vrid} para {index}] " in text


async def _run(questions: int, seed: int) -> None:
    rng = random.Random(seed)
    records = _corpus(rng)
    plans = [_question(rng, records) for _ in range(questions)]
    rows = []
    for plan in plans:
        row: dict[str, Any] = {"searches": len(plan["searches"])}
        for mode in ("off", "on"):
            os.environ["TARGETED_RETRIEVAL"] = "true" if mode == "on" else "false"
            state = _state()
            texts = [await _search(state, records, hits) for hits in plan["searches"]]
            row[f"{mode}_chars"] = sum(len(t) for t in texts)
            row[f"{mode}_needle"] = any(_shown(t, plan["needle"]) for t in texts)
            row[f"{mode}_records"] = len({u.get("virtual_record_id") for u in state.get("final_results", [])})
        os.environ["TARGETED_RETRIEVAL"] = "true"
        read = await _focused_read(records, plan["best"], plan["needle"])
        row["read_chars"] = len(read)
        row["read_needle"] = _shown(read, plan["needle"])
        rows.append(row)

    def tokens(chars: float) -> str:
        return f"{chars * TOKENS_PER_CHAR / 1000:5.1f}K"

    searches = sum(r["searches"] for r in rows)
    off = statistics.mean(r["off_chars"] for r in rows)
    on = statistics.mean(r["on_chars"] for r in rows)
    read = statistics.mean(r["read_chars"] for r in rows)
    print(f"{questions} questions, {searches} searches ({searches / questions:.2f} per question), seed {seed}\n")
    print(f"{'':44}{'chars':>9}  {'~tokens':>8}")
    print(f"{'searches, off (per question)':44}{off:9,.0f}  {tokens(off):>8}")
    print(f"{'searches, on (per question)':44}{on:9,.0f}  {tokens(on):>8}")
    print(f"{'focused reads of the 2 best records, on':44}{read:9,.0f}  {tokens(read):>8}")
    print(f"{'on, searches + focused reads':44}{on + read:9,.0f}  {tokens(on + read):>8}")
    print(f"\nper search: off {off * questions / searches:,.0f} chars, on {on * questions / searches:,.0f} chars")
    print(f"change per question: searches only {100 * (on - off) / off:+.0f}%, "
          f"with focused reads {100 * (on + read - off) / off:+.0f}%")
    print(f"distinct records shown per question: off {statistics.mean(r['off_records'] for r in rows):.1f}, "
          f"on {statistics.mean(r['on_records'] for r in rows):.1f}")
    shown_off = sum(r["off_needle"] for r in rows)
    shown_on = sum(r["on_needle"] for r in rows)
    shown_read = sum(r["on_needle"] or r["read_needle"] for r in rows)
    print(f"\nplanted mid-ranked detail in the best record shown by the searches: "
          f"off {shown_off}/{questions}, on {shown_on}/{questions}; "
          f"on with a focused read: {shown_read}/{questions}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--questions", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(_run(args.questions, args.seed))


if __name__ == "__main__":
    main()
