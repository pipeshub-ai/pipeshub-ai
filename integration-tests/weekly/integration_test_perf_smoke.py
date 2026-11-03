"""Perf smoke: the indexing and query benchmarks at one small size, against the stand-in model."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_IT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_IT / "perf"))

import bench_indexing  # noqa: E402
import bench_query  # noqa: E402
import smoke  # noqa: E402

pytestmark = pytest.mark.perf_smoke


def test_perf_smoke_stays_within_budget() -> None:
    """Index 50 files and ask 20 files' worth of questions as one user for a minute; no operation may pass twice its baseline.

    needs: env:TEST_OPENAI_API_KEY|OPENAI_API_KEY|TEST_AZURE_OPENAI_API_KEY+TEST_AZURE_OPENAI_ENDPOINT+TEST_AZURE_OPENAI_DEPLOYMENT_NAME|TEST_STAND_IN_MODEL_ENDPOINT
    """
    label = os.environ.get("PERF_SMOKE_LABEL") or "smoke-local-neo4j"
    out = _IT / "reports" / "perf" / "smoke"
    out.mkdir(parents=True, exist_ok=True)

    indexing = bench_indexing.run_benchmark(bench_indexing.build_parser().parse_args(
        ["--docs", str(smoke.SMOKE["docs"]), "--label", label, "--ai-models", "seed"]))
    (out / "indexing.json").write_text(json.dumps(indexing, indent=2) + "\n", encoding="utf-8")

    args = bench_query.build_parser().parse_args(
        ["--docs", str(smoke.SMOKE["query_docs"]), "--users", str(smoke.SMOKE["users"]),
         "--duration", str(smoke.SMOKE["duration"]), "--label", label, "--ai-models", "seed"])
    args.kinds_tuple = None
    query = bench_query.run_benchmark(args)
    (out / "query.json").write_text(json.dumps(query, indent=2) + "\n", encoding="utf-8")

    result = smoke.summarize(indexing, query)
    (out / "summary.json").write_text(json.dumps({"label": label, **result}, indent=2) + "\n", encoding="utf-8")
    print(f"perf smoke {label}: {result}")
    failures = smoke.budget_failures(result, smoke.load_baseline(label))
    assert failures == [], "; ".join(failures)
