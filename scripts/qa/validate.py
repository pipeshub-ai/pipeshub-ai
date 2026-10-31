"""Merge the floor with the model's additions into plan.json.

Invariants (each has a unit test in test_validate.py):
- every floor test is in the plan, whatever the model returned;
- a model failure or malformed output means the floor alone;
- an addition must be a catalog id in this run's edition and not quarantined;
- the budget trims additions by confidence, never the floor.
"""

from __future__ import annotations

from typing import Any

from qa import schema
from qa.config import Config
from qa.impact import PROFILE_ORDER

LEVELS = {"unit", "integration", "e2e"}
MAX_REASON = 200


def clean_text(value: Any, limit: int = MAX_REASON) -> str:
    """Model text goes into a PR comment: no mentions, markup or links."""
    text = str(value or "")
    for bad in ("@", "<", ">", "](", "http://", "https://", "`"):
        text = text.replace(bad, " ")
    return " ".join(text.split())[:limit]


def _seconds(row: dict[str, Any], cfg: Config) -> float:
    if row.get("avg_sec"):
        return float(row["avg_sec"])
    return float(cfg.always.get("default_seconds", {}).get(row["family"], 5))


def shape_problems(llm: Any) -> list[str]:
    if not isinstance(llm, dict):
        return ["model output is not an object"]
    return schema.check({k: v for k, v in llm.items() if not k.startswith("_")}, "plan-llm.v1.json")


def merge(
    cfg: Config,
    plan_input: dict[str, Any],
    llm: dict[str, Any] | None,
    rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    planner = dict((llm or {}).get("_planner") or {"adapter": "none", "status": "skipped"})
    problems = shape_problems(llm) if llm is not None else []
    if problems and planner.get("status") == "ok":
        planner.update(status="failed", error="; ".join(problems)[:500])
    usable = llm if (llm is not None and not problems and planner.get("status") == "ok") else {}

    scope = plan_input["edition"]
    quarantined = cfg.quarantined_ids()
    floor_items = plan_input["floor"]["items"]
    tests: dict[str, dict[str, Any]] = {
        tid: {"id": tid, "source": "floor", "why": item["why"], "grep": item.get("grep"), "flows": list(rows[tid].get("flows", []))}
        for tid, item in floor_items.items()
    }

    dropped: list[dict[str, str]] = []
    additions: list[tuple[float, dict[str, Any]]] = []
    for a in usable.get("add", []):
        tid = a["id"]
        row = rows.get(tid)
        if row is None:
            dropped.append({"id": clean_text(tid), "why": "not in the catalog"})
        elif scope != "all" and row["edition"] != scope:
            dropped.append({"id": tid, "why": f"belongs to edition {row['edition']}, run is {scope}"})
        elif tid in quarantined:
            dropped.append({"id": tid, "why": "quarantined"})
        elif row.get("status") == "planned":
            dropped.append({"id": tid, "why": "planned, not implemented"})
        elif tid in tests or tid in plan_input.get("deferred", []):
            continue
        else:
            try:
                conf = max(0.0, min(1.0, float(a.get("confidence", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            additions.append((conf, {"id": tid, "source": "model", "why": [clean_text(a.get("reason"))], "grep": None, "confidence": conf, "flows": list(row.get("flows", []))}))

    budget = float(plan_input.get("budget", {}).get("extra_minutes", 0)) * 60
    trimmed: list[dict[str, Any]] = []
    spent = 0.0
    for conf, item in sorted(additions, key=lambda x: -x[0]):
        cost = _seconds(rows[item["id"]], cfg)
        if spent + cost > budget:
            trimmed.append({"id": item["id"], "why": "over the profile's budget", "confidence": conf})
            continue
        spent += cost
        tests[item["id"]] = item

    allowed_matrix = set(cfg.always.get("matrix_entries", []))
    matrix = set(plan_input.get("matrix", []))
    for m in usable.get("add_matrix", []):
        entry = m.get("entry") if isinstance(m, dict) else None
        if entry in allowed_matrix:
            matrix.add(entry)
        else:
            dropped.append({"id": f"matrix:{clean_text(entry)}", "why": "unknown backend-matrix entry"})

    risk = usable.get("risk") or {"level": "low", "reasons": []}
    profile = plan_input["profile"]
    if risk.get("level") == "high":
        i = PROFILE_ORDER.index(profile)
        profile = PROFILE_ORDER[min(i + 1, PROFILE_ORDER.index("conservative"))] if profile != "full" else profile

    missing = list(plan_input.get("missing", []))
    for m in usable.get("missing", []):
        if isinstance(m, dict) and m.get("source"):
            missing.append({
                "source": clean_text(m.get("source")),
                "level": m.get("level") if m.get("level") in LEVELS else "unit",
                "suggested_tc": clean_text(m.get("suggested_tc"), 40),
                "spec": clean_text(m.get("spec")),
                "priority": m.get("priority") if m.get("priority") in ("high", "medium", "low") else "medium",
                "origin": "model",
            })

    stack = _stack(cfg, plan_input, profile, [rows[t] for t in tests])
    return {
        "schema": "qa-plan/v1",
        "mode": plan_input["mode"],
        "edition": scope,
        "profile": profile,
        "planner": planner,
        "base": plan_input["change"]["base"],
        "head": plan_input["change"]["head"],
        "branch": plan_input["change"].get("branch"),
        "tests": sorted(tests.values(), key=lambda t: t["id"]),
        "deferred": plan_input.get("deferred", []),
        "full_suites": plan_input.get("full_suites", {}),
        "matched_rules": plan_input.get("matched_rules", []),
        "flows": plan_input.get("flows", {}),
        "stack": stack,
        "matrix": sorted(matrix),
        "dropped": dropped,
        "budget_trimmed": trimmed,
        "missing": missing,
        "unmapped": plan_input.get("unmapped", []),
        "risk": {"level": risk.get("level", "low"), "reasons": [clean_text(r) for r in risk.get("reasons", [])][:5]},
        "notes": plan_input.get("notes", []),
    }


def _stack(cfg: Config, plan_input: dict[str, Any], profile: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    needs_stack = any("service:stack" in r["needs"] for r in rows)
    graph = plan_input.get("stack", {}).get("graph_db", "neo4j")
    if cfg.profile(profile).get("graph_db") == "both":
        graph = "both"
    markers = {m for r in rows if r["family"] == "it-pytest" for m in r["markers"]}
    broker = plan_input.get("stack", {}).get("broker") or ("kafka" if "requires_kafka" in markers else "redis")
    profiles = sorted(p for p, ms in cfg.compose_profiles.items() if markers & set(ms))
    if broker == "kafka" and "kafka" in cfg.compose_profiles and "kafka" not in profiles:
        profiles.append("kafka")
    return {
        "needed": needs_stack,
        "graph_db": ["neo4j", "arango"] if graph == "both" else [graph],
        "broker": broker,
        "compose_profiles": profiles,
    }
