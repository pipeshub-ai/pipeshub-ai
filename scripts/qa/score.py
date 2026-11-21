"""Score a planner adapter against past changes whose failing tests are known.

A case is a JSON file in scripts/qa/score/cases/:
    {"name": "...", "base": "<sha>", "head": "<sha>", "failing": ["<catalog id>", ...]}

For each case the full pipeline runs (collect, impact, plan, validate) and the
score is how many known failures the final plan would have run. The floor's
recall and the plan's recall are reported separately, so the model's value-add
is visible on its own.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from qa import catalog, collect, impact, planner, validate
from qa.config import Config


def load_cases(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) | {"file": p.name} for p in sorted(directory.glob("*.json"))]


def score_case(cfg: Config, case: dict[str, Any], adapter: str, rows: list[dict[str, Any]], profile: str = "standard") -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="qa-score-") as tmp:
        out = Path(tmp)
        changeset, diff = collect.collect(cfg, case["base"], case["head"], mode="pr")
        (out / "changeset.diff").write_text(diff, encoding="utf-8")
        plan_input = impact.compute(cfg, changeset, rows, profile=profile)
        (out / "plan-input.json").write_text(json.dumps(plan_input), encoding="utf-8")
        started = time.monotonic()
        status = planner.run(cfg.root, adapter, out / "plan-input.json", out / "plan-llm.json")
        llm = json.loads((out / "plan-llm.json").read_text(encoding="utf-8"))
        plan = validate.merge(cfg, plan_input, llm, catalog.index(rows))
    failing = set(case["failing"])
    floor = set(plan_input["floor"]["tests"])
    final = {t["id"] for t in plan["tests"]}
    unknown = sorted(failing - {r["id"] for r in rows})
    return {
        "name": case.get("name", case["file"]),
        "failing": len(failing),
        "floor_recall": round(len(failing & floor) / len(failing), 3) if failing else 1.0,
        "plan_recall": round(len(failing & final) / len(failing), 3) if failing else 1.0,
        "added": len(final - floor),
        "dropped": len(plan["dropped"]),
        "planner": status.get("status"),
        "seconds": round(time.monotonic() - started, 1),
        "missed": sorted(failing - final),
        "unknown_ids": unknown,
    }


def run(cfg: Config, cases_dir: Path, adapter: str, profile: str = "standard") -> dict[str, Any]:
    rows = catalog.build(cfg)
    cases = load_cases(cases_dir)
    scores = [score_case(cfg, c, adapter, rows, profile) for c in cases]
    n = len(scores) or 1
    return {
        "adapter": adapter,
        "cases": len(scores),
        "floor_recall": round(sum(s["floor_recall"] for s in scores) / n, 3),
        "plan_recall": round(sum(s["plan_recall"] for s in scores) / n, 3),
        "planner_failures": sum(1 for s in scores if s["planner"] == "failed"),
        "scores": scores,
    }
