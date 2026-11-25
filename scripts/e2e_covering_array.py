#!/usr/bin/env python3
"""Generate and verify the covering arrays of the end-to-end harness options matrix.

    scripts/e2e_covering_array.py            # rewrite docs/test-plan/data/e2e-{pairwise,threeway-core}.{json,csv}
                                             # from the committed rows: ids kept, refused rows mended, rows appended
    scripts/e2e_covering_array.py --check    # non-zero if that rewrite would change a committed file, or a row is
                                             # invalid or a tuple uncovered
    scripts/e2e_covering_array.py --fresh    # from scratch: renumbers every row id (only to start a new array)
    scripts/e2e_covering_array.py --render   # rewrite generated/e2e-matrix.md and the must-run block of 04 (fast)

Factors and levels: docs/test-plan/data/e2e-factors.json. Constraints are below, as named predicates over a
row (a dict factor -> level); each names the factors it reads and is checked once all of them are assigned.

Two arrays:
- pairwise: every valid pair of levels of any two factors appears in at least one row (strength 2, all factors);
- threeway-core: every valid triple over the trust-core factors (the file's `threeWiseCore`) appears, with the
  remaining factors filled to also respect the constraints (strength 3 on the core only).

A tuple is valid when some full row containing it satisfies every constraint. The generator is greedy (AETG
style) and deterministic (fixed seed); `--check` regenerates in memory and diffs. Runners,
known-red lists and tickets name rows by id, so a regeneration keeps them: a committed row a constraint now refuses
changes as few factors as make it valid, a factor added since takes its first level in every committed row, and
rows are appended for the tuples left uncovered.
Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import routing_table

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs/test-plan/data"
FACTORS = DATA / "e2e-factors.json"
SEED = 20261009
CANDIDATES = 40

# Routing is the routing table's (contracts/harness/vectors/routing.json), not this script's.
AGENT_DURABLE = routing_table.e2e_modes(kind="agent")
ENGINE_MODES = routing_table.e2e_engine_modes()
SINKS = {"mcp", "toolset"}
# A conversation grant is held to the destination a call names (UX1, app/harness/flow_destination.py): the toolset's
# write names its project; the fake MCP server's write names none, so its card offers "Allow once" only.
DESTINATION_FAMILIES = {"toolset"}
# The turns that spawn a child run: deep mode's coordinator holds no domain tool and hands the request to a sub-agent
# (`spawn_agent`, app/harness/assembly/deep.py). An engine's helpers are not child runs: they call the run's own model
# (MA1 §6).
DELEGATING_MODES = {"deep"}
CHILD_MODEL_REFUSAL = "child_grant_too_large"
# Stack profiles: a strength-2 covering array of the five store and deployment factors, so a nightly run brings up
# seven stacks, not one per row. ST-1 is the PR lane's stack; at most two are standalone MongoDB (legacy only).
STACK_FACTORS = ("graph", "mongo", "kv", "broker", "deploy")
STACKS = {
    "ST-1": ("neo4j", "replset", "redis", "redis_streams", "compose"),
    "ST-2": ("neo4j", "replset", "redis", "redis_streams", "helm"),
    "ST-3": ("neo4j", "replset", "redis", "kafka", "compose"),
    "ST-4": ("neo4j", "standalone", "etcd", "redis_streams", "compose"),
    "ST-5": ("arango", "replset", "redis", "redis_streams", "compose"),
    "ST-6": ("arango", "replset", "etcd", "kafka", "helm"),
    "ST-7": ("arango", "standalone", "redis", "kafka", "helm"),
}
ENGINE_WIRES = {
    "opencode": {"openai_chat", "azure_openai"},
    "codex": {"openai_responses", "azure_openai"},
    "claude_code": {"anthropic_messages"},
}


@dataclass(frozen=True)
class Constraint:
    name: str
    factors: tuple[str, ...]
    holds: Callable[[dict], bool]
    why: str


def effective_durable(r: dict) -> bool:
    return routing_table.e2e_route({"mode": r["mode"], "path": r["path"]})[0] == "durable"


def child_refused(r: dict) -> bool:
    """The spawn is refused (MA1 §3 C3): the role's model is outside the org's routes. The script's tool calls are the
    sub-agent's, so none of them runs; the parent is told and answers."""
    return r["child_model"] == "forbidden" and effective_durable(r)


def child_expected(r: dict) -> str:
    """Whose model a child run calls (MA1 §3 C1..C3). The legacy loop's sub-agents are not child runs and MA1 gives
    them no rule, so a role model there is `unspecified` (QA37-F1)."""
    if r["child_model"] == "none":
        return "inherit"
    if not effective_durable(r):
        return "unspecified"
    return "refused:models.roles" if child_refused(r) else f"role:{r['child_model']}"


def prompt_expected(r: dict) -> bool:
    """An approval card appears: a durable run calls an external-write tool after a read, and something holds it."""
    if not (effective_durable(r) and r["tool_family"] in SINKS) or r["flow_guard"] == "org_deny" or child_refused(r):
        return False
    return r["engine"] != "none" or r["write_policy"] == "ask" or r["flow_guard"] == "ask"


def oracle(r: dict) -> dict:
    """Expected outcome of a full row. Every sink script reads first (a private record or an MCP result), then writes."""
    route, reason = routing_table.e2e_route(r)
    sink = r["tool_family"] in SINKS
    if not sink:
        outcome, effects = "answered", 0
    elif child_refused(r):
        outcome, effects = f"denied:{CHILD_MODEL_REFUSAL}", 0
    elif route == "legacy":
        outcome, effects = ("refused:approval_unavailable", 0) if r["flow_guard"] != "off" else ("written", 1)
    elif r["flow_guard"] == "org_deny":
        outcome, effects = "denied:flow_external_write", 0
    elif not prompt_expected(r):
        outcome, effects = "written", 1
    else:
        outcome, effects = {"allow_once": ("approved:written", 1), "allow_conversation": ("approved:written+grant", 1),
                            "deny": ("denied:user", 0), "expire": ("expired:approval_expired", 0)}[r["decision"]]
    # CTL-6: an engine agent's turn runs on the loop, which may hand off to its engine through code_task.
    executor = "legacy" if route != "durable" else (f"loop+code_task:{r['engine']}" if r["engine"] != "none" else "loop")
    return {"route": route, "reason": reason, "executor": executor, "card": prompt_expected(r), "outcome": outcome,
            "effects": effects, "child": child_expected(r)}


def stack_of(r: dict) -> str | None:
    key = tuple(r[f] for f in STACK_FACTORS)
    return next((sid for sid, t in STACKS.items() if t == key), None)


CONSTRAINTS = (
    Constraint("stack_profile", STACK_FACTORS, lambda r: stack_of(r) is not None,
               "rows run on one of the seven stack profiles (STACKS), which pairwise-cover the store and deployment factors"),
    Constraint("standalone_is_infra", ("mongo", "path"),
               lambda r: (r["mongo"] == "standalone") == (r["path"] == "standalone_mongo"),
               "a standalone MongoDB is what makes the path infrastructure_unavailable, and only it does"),
    Constraint("engine_needs_durable_agent", ("engine", "path", "mode"),
               lambda r: r["engine"] == "none" or (r["path"] == "durable" and r["mode"] in ENGINE_MODES),
               "engine runs are durable runs of an engine agent, or of the default chat's Agent mode handing a job to "
               "the person's or the org's coding engine (ENGPICK)"),
    Constraint("engine_needs_broker", ("engine", "sandbox"),
               lambda r: r["engine"] == "none" or r["sandbox"].startswith("broker_"),
               "the worker plans no engine without the sandbox broker"),
    Constraint("engine_wire", ("engine", "wire"),
               lambda r: r["engine"] == "none" or r["wire"] in ENGINE_WIRES[r["engine"]],
               "opencode speaks chat completions, Codex Responses, Claude Code Messages through the model gateway"),
    Constraint("compiler_is_loop_only", ("compiler", "engine", "path"),
               lambda r: r["compiler"] == "off" or (r["engine"] == "none" and r["path"] == "durable"),
               "the compiler runs only in the durable worker's loop kernel"),
    Constraint("memory_is_durable", ("tool_family", "path", "mode"),
               lambda r: r["tool_family"] != "memory" or effective_durable(r),
               "governed memory is absent on the legacy route"),
    Constraint("memory_needs_a_person", ("tool_family", "trigger"),
               lambda r: r["tool_family"] != "memory" or r["trigger"] == "interactive",
               "a scheduled run is unattended and is granted no memory write scope (P7.3), so it is offered no remember tool"),
    Constraint("run_code_needs_sandbox", ("tool_family", "sandbox"),
               lambda r: r["tool_family"] != "run_code" or r["sandbox"] != "unset",
               "run_code is hidden without SANDBOX_MODE"),
    Constraint("mode_tool_families", ("mode", "tool_family"),
               lambda r: r["tool_family"] in {
                   "plain": {"none", "memory"}, "internal_search": {"knowledge", "memory"},
                   "project": {"none", "knowledge"},
               }.get(r["mode"], set(["none", "knowledge", "mcp", "toolset", "run_code", "skills", "memory"])),
               "assistant modes and project chats expose only these tools"),
    Constraint("scheduled_runs", ("trigger", "path", "mode", "action"),
               lambda r: r["trigger"] == "interactive" or (r["path"] == "durable" and r["mode"] in AGENT_DURABLE and r["action"] == "send"),
               "schedules fire durable agent runs (a firing the rollout says is legacy is refused) and send a fresh turn"),
    Constraint("helm_sandbox_modes", ("deploy", "sandbox"),
               lambda r: r["deploy"] != "helm" or r["sandbox"] != "docker",
               "the chart offers broker mode (and local for values-local), not docker mode"),
    Constraint("decision_iff_prompt",
               ("decision", "path", "mode", "tool_family", "flow_guard", "write_policy", "engine", "child_model"),
               lambda r: (r["decision"] != "none") == prompt_expected(r),
               "a row has a user decision exactly when the oracle expects an approval card (see prompt_expected)"),
    Constraint("conversation_grant_offer", ("decision", "tool_family", "flow_guard", "write_policy", "engine"),
               lambda r: r["decision"] != "allow_conversation" or (
                   r["tool_family"] in DESTINATION_FAMILIES and r["write_policy"] == "allow"
                   and (r["engine"] != "none" or r["flow_guard"] == "ask")),
               "'Allow for this conversation' is offered for a call that names its destination, held by the flow rule "
               "or an engine's default alone: an org's own write rule is never granted (QA10-F3)"),
    Constraint("child_model_needs_delegation", ("child_model", "mode", "engine"),
               lambda r: r["child_model"] == "none" or (r["mode"] in DELEGATING_MODES and r["engine"] == "none"),
               "a role's model is read when a run spawns a child run, which only deep mode on a loop run does; an "
               "engine's helpers call the run's own model (MA1 §6)"),
)


def load() -> tuple[list[str], dict[str, list[str]], list[str]]:
    data = json.loads(FACTORS.read_text())
    names = [f["id"] for f in data["factors"]]
    levels = {f["id"]: f["levels"] for f in data["factors"]}
    return names, levels, data["threeWiseCore"]


def consistent(row: dict) -> bool:
    for c in CONSTRAINTS:
        if all(f in row for f in c.factors) and not c.holds(row):
            return False
    return True


def complete(row: dict, names: list[str], levels: dict[str, list[str]], rng: random.Random | None = None) -> dict | None:
    """Depth-first completion of a partial row; None when no completion satisfies the constraints.

    Factors are assigned in the file's order and a constraint is checked once its last factor is: a factor a
    constraint reads belongs before the store and role factors, or a refusal is found only after trying each of them."""
    if not consistent(row):
        return None
    todo = [n for n in names if n not in row]
    if not todo:
        return dict(row)
    f = todo[0]
    opts = list(levels[f])
    if rng:
        rng.shuffle(opts)
    for lv in opts:
        row[f] = lv
        out = complete(row, names, levels, rng)
        del row[f]
        if out is not None:
            return out
    return None


def tuples_for(factors: list[str], strength: int, levels: dict[str, list[str]], names: list[str]) -> set[tuple]:
    out = set()
    for combo in itertools.combinations(factors, strength):
        for lvls in itertools.product(*(levels[f] for f in combo)):
            t = tuple(zip(combo, lvls))
            if complete(dict(t), names, levels) is not None:
                out.add(t)
    return out


def covered_by(row: dict, factors: list[str], strength: int) -> set[tuple]:
    return {tuple((f, row[f]) for f in combo) for combo in itertools.combinations(factors, strength)}


def generate(names, levels, factors, strength) -> list[dict]:
    return extend(names, levels, factors, strength, tuples_for(factors, strength, levels, names))


def extend(names, levels, factors, strength, uncovered: set[tuple]) -> list[dict]:
    """Rows, chosen greedily, until every tuple of ``uncovered`` is in one."""
    rng = random.Random(SEED + strength)
    uncovered = set(uncovered)
    rows: list[dict] = []
    while uncovered:
        best, best_gain = None, -1
        seed_tuple = sorted(uncovered)[0]
        for _ in range(CANDIDATES):
            row = dict(seed_tuple)
            order = [n for n in names if n not in row]
            rng.shuffle(order)
            for f in order:
                scored = []
                for lv in levels[f]:
                    row[f] = lv
                    if complete(dict(row), names, levels) is None:
                        continue
                    gain = sum(1 for t in covered_by_partial(row, factors, strength, f) if t in uncovered)
                    scored.append((gain, rng.random(), lv))
                del row[f]
                gain_lv = max(scored)
                row[f] = gain_lv[2]
            gain = len(covered_by(row, factors, strength) & uncovered)
            if gain > best_gain:
                best, best_gain = dict(row), gain
        rows.append(best)
        uncovered -= covered_by(best, factors, strength)
    return rows


def mend(row: dict, names, levels) -> dict:
    """A refused row made valid: one factor of a broken constraint changed (factors, then levels, in order), or
    failing that those factors re-completed together."""
    broken = [f for c in CONSTRAINTS if not c.holds(row) for f in c.factors]
    suspects = list(dict.fromkeys(broken))
    for f in suspects:
        for lv in levels[f]:
            candidate = {**row, f: lv}
            if all(c.holds(candidate) for c in CONSTRAINTS):
                return candidate
    out = complete({k: v for k, v in row.items() if k not in suspects}, names, levels)
    if out is None:
        raise ValueError(f"no valid row keeps {sorted(set(names) - set(suspects))} of {row}")
    return out


def relevel(row: dict, names, levels) -> dict:
    """A row holding a level its factor no longer has: those factors take the first levels (in order) that make
    the row valid with everything else kept, or failing that are re-completed."""
    gone = [f for f in names if row[f] not in levels[f]]
    if not gone:
        return row
    rest = {k: v for k, v in row.items() if k not in gone}
    for chosen in itertools.product(*(levels[f] for f in gone)):
        candidate = {**rest, **dict(zip(gone, chosen))}
        if all(c.holds(candidate) for c in CONSTRAINTS):
            return candidate
    out = complete(rest, names, levels)
    if out is None:
        raise ValueError(f"no valid row keeps {sorted(rest)} of {row}")
    return out


def repair(rows: list[dict], names, levels, factors, strength) -> list[dict]:
    """The committed rows, in order, each mended if it holds a level that is gone or a constraint refuses it, then
    rows for what is left uncovered.

    A factor the rows were committed without takes its first level: a new factor's first level is the behaviour
    before it existed, so a committed row keeps what it tests."""
    rows = [relevel({n: r.get(n, levels[n][0]) for n in names}, names, levels) for r in rows]
    kept = [r if all(c.holds(r) for c in CONSTRAINTS) else mend(r, names, levels) for r in rows]
    covered = set().union(*(covered_by(r, factors, strength) for r in kept)) if kept else set()
    return kept + extend(names, levels, factors, strength, tuples_for(factors, strength, levels, names) - covered)


def covered_by_partial(row: dict, factors: list[str], strength: int, last: str) -> list[tuple]:
    present = [f for f in factors if f in row and f != last]
    if last not in factors:
        return []
    out = []
    for combo in itertools.combinations(present, strength - 1):
        fs = sorted(combo + (last,), key=factors.index)
        out.append(tuple((f, row[f]) for f in fs))
    return out


def verify(rows: list[dict], names, levels, factors, strength) -> list[str]:
    problems = []
    for i, r in enumerate(rows, 1):
        if set(r) != set(names):
            problems.append(f"row {i}: factors {sorted(set(names) ^ set(r))} missing or extra")
            continue
        bad = [c.name for c in CONSTRAINTS if not c.holds(r)]
        if bad:
            problems.append(f"row {i}: violates {bad}")
        for f in names:
            if r[f] not in levels[f]:
                problems.append(f"row {i}: {f}={r[f]} is not a level")
    need = tuples_for(factors, strength, levels, names)
    have = set().union(*(covered_by(r, factors, strength) for r in rows)) if rows else set()
    missing = need - have
    if missing:
        problems.append(f"{len(missing)} valid {strength}-tuples not covered, e.g. {sorted(missing)[:3]}")
    return problems


def to_csv(rows: list[dict], names: list[str], prefix: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "stack"] + names)
    for i, r in enumerate(rows, 1):
        w.writerow([f"{prefix}{i:03d}", stack_of(r)] + [r[n] for n in names])
    return buf.getvalue()


def payload(rows, names, factors, strength, prefix, tuples) -> dict:
    return {
        "generatedBy": "scripts/e2e_covering_array.py",
        "factorsFile": str(FACTORS.relative_to(ROOT)),
        "strength": strength,
        "coveredFactors": factors,
        "seed": SEED + strength,
        "validTuples": tuples,
        "stacks": {sid: dict(zip(STACK_FACTORS, t)) for sid, t in STACKS.items()},
        "rows": [{"id": f"{prefix}{i:03d}", "stack": stack_of(r), **{n: r[n] for n in names}}
                 for i, r in enumerate(rows, 1)],
        "constraints": [{"name": c.name, "factors": list(c.factors), "why": c.why} for c in CONSTRAINTS],
    }


ARRAYS = (("pairwise", 2, None, "PW-"), ("threeway-core", 3, "core", "T3-"))
MUST_RUN = DATA / "e2e-must-run.json"
MATRIX_MD = ROOT / "docs/test-plan/generated/e2e-matrix.md"
E2E_DOC = ROOT / "docs/test-plan/04-e2e-test-plan.md"
BEGIN, END = "<!-- BEGIN generated: must-run (scripts/e2e_covering_array.py --render) -->", "<!-- END generated: must-run -->"


def _cell(v: object) -> str:
    return str(v).replace("|", "\\|")


def render_must_run(defaults: dict, scenarios: list[dict]) -> str:
    out = ["| Id | Scenario | Lane | Runs | Factors (others at defaults) | Today |", "|---|---|---|---|---|---|"]
    for sc in scenarios:
        f = ", ".join(f"{k}={v}" for k, v in sc["factors"].items()) or "defaults"
        out.append(f"| {sc['id']} | {_cell(sc['title'])} | {sc['lane']} | {sc['runs']} | {_cell(f)} | {_cell(sc['exists'])} |")
    out.append("")
    for sc in scenarios:
        row = {**defaults, **sc["factors"]}
        exp = oracle(row)
        out.append(f"#### {sc['id']} {sc['title']}")
        out.append("")
        out.append(f"- **Risks (00 §2):** {sc['risk']}. **Lane:** {sc['lane']} ({sc['runs']}). **Depends on:** {sc['depends']}.")
        out.append(f"- **Oracle for the row:** route {exp['route']}{' (' + exp['reason'] + ')' if exp['reason'] else ''}, executor "
                   f"{exp['executor']}, card {'yes' if exp['card'] else 'no'}, outcome {exp['outcome']}, external effects {exp['effects']}, "
                   f"child model {exp['child']}.")
        out.append("- **Steps:**")
        out += [f"  {i}. {st}" for i, st in enumerate(sc["steps"], 1)]
        out.append("- **User sees:** " + " ".join(sc["expect_user"]))
        out.append("- **Journal:** " + "; ".join(sc["assert_journal"]) + ".")
        out.append("- **Effects:** " + "; ".join(sc["assert_effects"]) + ".")
        out.append("- **Audit:** " + "; ".join(sc["assert_audit"]) + ".")
        out.append(f"- **Covered today:** {sc['exists']}.")
        out.append("")
    return "\n".join(out)


def render_matrix(names: list[str]) -> str:
    lines = ["# End-to-end covering arrays (generated)", "",
             "Generated by `scripts/e2e_covering_array.py --render` from `docs/test-plan/data/e2e-{pairwise,threeway-core}.json`.",
             "Each row also shows the oracle's expectation (04 §4.3): route (and why legacy), executor, whether an approval card",
             "appears, the outcome of the external write, the number of external effects the fake SaaS or MCP server must record,",
             "and whose model a child run calls.", ""]
    for stem, strength, which, prefix in ARRAYS:
        data = json.loads((DATA / f"e2e-{stem}.json").read_text())
        rows = data["rows"]
        lines.append(f"## {stem} (strength {strength}, {len(rows)} rows, {data['validTuples']} valid tuples)")
        lines.append("")
        cols = [n for n in names]
        lines.append("| Id | Stack | " + " | ".join(cols) + " | route | executor | card | outcome | effects | child |")
        lines.append("|---|---|" + "|".join("---" for _ in cols) + "|---|---|---|---|---|---|")
        for r in rows:
            row = {k: v for k, v in r.items() if k not in ("id", "stack")}
            e = oracle(row)
            route = e["route"] + (f" ({e['reason']})" if e["reason"] else "")
            lines.append(f"| {r['id']} | {r['stack']} | " + " | ".join(row[c] for c in cols)
                         + f" | {route} | {e['executor']} | {'yes' if e['card'] else 'no'} | {e['outcome']} | {e['effects']} | {e['child']} |")
        lines.append("")
    lines.append("## Stack profiles")
    lines.append("")
    lines.append("| Stack | " + " | ".join(STACK_FACTORS) + " |")
    lines.append("|---|" + "|".join("---" for _ in STACK_FACTORS) + "|")
    for sid, t in STACKS.items():
        lines.append(f"| {sid} | " + " | ".join(t) + " |")
    return "\n".join(lines) + "\n"


def render(names: list[str]) -> dict[Path, str]:
    mr = json.loads(MUST_RUN.read_text())
    block = render_must_run(mr["defaults"], mr["scenarios"])
    doc = E2E_DOC.read_text()
    a, b = doc.index(BEGIN), doc.index(END)
    new_doc = doc[: a + len(BEGIN)] + "\n\n" + block + "\n" + doc[b:]
    return {MATRIX_MD: render_matrix(names), E2E_DOC: new_doc}


def committed_rows(jpath: Path) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in ("id", "stack")} for r in json.loads(jpath.read_text())["rows"]]


def array_files(names, levels, core, fresh: bool) -> dict[Path, str]:
    """What the generator writes for each array: the committed rows repaired (ids kept), or a greedy array from
    scratch when ``fresh`` or nothing is committed. Runners, notes and tickets name rows by id, and the committed
    rows are a chain of repairs since 927eff13b, so from scratch renumbers nearly every row."""
    out = {}
    for stem, strength, which, prefix in ARRAYS:
        factors = core if which == "core" else names
        jpath = DATA / f"e2e-{stem}.json"
        if fresh or not jpath.exists():
            rows = generate(names, levels, factors, strength)
        else:
            rows = repair(committed_rows(jpath), names, levels, factors, strength)
        problems = verify(rows, names, levels, factors, strength)
        if problems:
            raise ValueError(f"{stem}: " + "; ".join(problems))
        n_tuples = len(tuples_for(factors, strength, levels, names))
        out[jpath] = json.dumps(payload(rows, names, factors, strength, prefix, n_tuples), indent=1) + "\n"
        out[DATA / f"e2e-{stem}.csv"] = to_csv(rows, names, prefix)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="non-zero if a regeneration would change a committed file")
    ap.add_argument("--render", action="store_true", help="only rewrite the generated markdown from the committed data")
    ap.add_argument("--repair", action="store_true", help="the default: keep the committed rows and ids, add rows")
    ap.add_argument("--fresh", action="store_true", help="generate from scratch, renumbering every row id")
    args = ap.parse_args(argv)
    names, levels, core = load()
    if args.render:
        for path, body in render(names).items():
            path.write_text(body)
        return 0
    if args.check:
        failed = False
        for stem, strength, which, _prefix in ARRAYS:
            jpath = DATA / f"e2e-{stem}.json"
            if not jpath.exists():
                print(f"missing: {jpath.relative_to(ROOT)}", file=sys.stderr)
                return 1
            for p in verify(committed_rows(jpath), names, levels, core if which == "core" else names, strength):
                print(f"{jpath.name}: {p}", file=sys.stderr)
                failed = True
        if failed:
            return 1
        for path, body in {**array_files(names, levels, core, fresh=False), **render(names)}.items():
            if path.read_text() != body:
                print(f"stale: {path.relative_to(ROOT)} (run scripts/e2e_covering_array.py)", file=sys.stderr)
                failed = True
        return 1 if failed else 0
    for path, body in array_files(names, levels, core, args.fresh).items():
        path.write_text(body)
    for path, body in render(names).items():
        path.write_text(body)
    for stem, *_ in ARRAYS:
        print(f"{stem}: {len(json.loads((DATA / f'e2e-{stem}.json').read_text())['rows'])} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
