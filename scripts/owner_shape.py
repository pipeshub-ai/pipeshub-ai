#!/usr/bin/env python3
"""R31-JOURNEY: the account shapes of a real stack's people, as structure only, for the test stacks' owner fixture.

    python3 scripts/owner_shape.py --derive --container C --port P [--db D]   print the shape of a stack's runs
    python3 scripts/owner_shape.py --check                                     the fixture is whole and consistent

Reads a stack's MongoDB read-only (``docker exec <container> mongosh``, find and count only) and keeps only structure:
per run, whether it is the default assistant or a saved agent, top-level or a child, its executor and engine kind, its
run kind, via and surface, how many MCP servers it holds whole and how many by named tools, whether it has skills,
knowledge, a code task, mounts; the approval policy's rule kinds; the tool-grant entry counts. Never an id, a name, a
token, a message, a sealed field (nothing is decrypted) or an org's scope: servers are counted, agents are counted.

The fixture deployment/e2e/owner_shape.json records the derived counts (``derived``, pasted from ``--derive``) next to
how the test stacks realise each shape (``owner``, ``shapes``); ``--check`` holds the realisation to the counts that
matter (as many servers held whole as the real org's default chat holds at most, a job of each engine kind it ran).

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "deployment/e2e/owner_shape.json"
ASSISTANT_KEY = "agentIdPlaceholder"

# Find and count only. Every value printed is a count, a boolean or an enum of the run spec's own schema.
QUERY = r"""
const tally = {}; const agents = new Set(); const servers = {};
db.agentRuns.find({pinned: {$exists: true}}, {pinned: 1, parent: 1}).forEach(r => {
  let p; try { p = JSON.parse(r.pinned); } catch (e) { return; }
  const s = p.spec || {}; const x = ((s.extensions || {})["x.pipeshub"]) || {};
  const key = (x.agent || {}).agentKey;
  const agent = key === "%ASSISTANT%" ? "assistant" : (key ? "saved" : "none");
  if (agent === "saved") agents.add(key);
  const executor = (s.executor || {}).kind || "loop";
  const mcp = x.mcp || [];
  const whole = mcp.filter(m => m.tools === "at_first_use").length;
  mcp.forEach(m => { servers[m.instanceId] = servers[m.instanceId] || {whole: 0, named: 0};
                     servers[m.instanceId][m.tools === "at_first_use" ? "whole" : "named"]++; });
  const sources = new Set(((s.grants || {}).tools || []).map(t => String(t.toolId).split("@")[0])
                                                        .map(src => src.startsWith("mcp") ? "mcp" : src));
  const shape = {agent, run: r.parent ? "child" : "top", executor,
    engine: executor === "engine" ? ((s.executor.engine || {}).kind || "?") : "-", runKind: s.runKind || "?",
    via: x.via || "?", surface: x.surface || "?", mcpWhole: whole, mcpNamed: mcp.length - whole,
    skills: (x.skills || []).length > 0, knowledge: (((x.knowledge || {}).kb) || []).length > 0,
    codeTask: !!x.codeTask, mounts: (x.mounts || []).length > 0, grantSources: [...sources].sort()};
  const k = JSON.stringify(shape); tally[k] = (tally[k] || 0) + 1;
});
const layers = db.cpPolicyLayers.find({}, {kind: 1, body: 1}).toArray().map(l => {
  let b = {}; try { b = JSON.parse(l.body); } catch (e) {}
  const a = b.approvals || {};
  return {kind: l.kind, approvals: Object.fromEntries(Object.entries(a).map(([k, v]) =>
    [k, typeof v === "object" && v ? [...new Set(Object.values(v).map(x => typeof x === "string" ? x : typeof x))].sort() : typeof v]))};
});
print(JSON.stringify({
  runs: Object.entries(tally).map(([k, n]) => ({...JSON.parse(k), count: n})).sort((a, b) => b.count - a.count),
  savedAgents: agents.size,
  servers: Object.values(servers).sort((a, b) => (b.whole + b.named) - (a.whole + a.named)),
  policyLayers: layers,
  principals: db.agentRuns.distinct("attribution.principalKind"),
}));
"""


def derive(container: str, port: int, db: str) -> dict[str, Any]:
    script = QUERY.replace("%ASSISTANT%", ASSISTANT_KEY)
    done = subprocess.run(["docker", "exec", container, "mongosh", "--quiet", "--port", str(port), db, "--eval", script],
                          capture_output=True, text=True, check=True, timeout=300)
    return json.loads(done.stdout.strip().splitlines()[-1])


def problems(fixture: dict[str, Any]) -> list[str]:
    out: list[str] = []
    derived = fixture.get("derived") or {}
    runs = derived.get("runs") or []
    if not runs:
        return ["owner_shape.json: no derived runs (paste scripts/owner_shape.py --derive)"]
    assistant_whole = max((r["mcpWhole"] for r in runs if r["agent"] == "assistant"), default=0)
    owner = fixture["owner"]
    if len(owner["servers"]) != assistant_whole:
        out.append(f"owner servers {owner['servers']}: the owner's default chat held {assistant_whole} servers whole")
    if owner["firstUseServer"] not in owner["servers"]:
        out.append(f"owner firstUseServer {owner['firstUseServer']} is not one of the owner's servers")
    ran = sorted({r["engine"] for r in runs if r["agent"] == "assistant" and r["executor"] == "engine"})
    out += [f"the owner ran {engine} jobs from the default chat; journeys.json has no job.{engine} path"
            for engine in ran if f"job.{engine}" not in {p["id"] for p in json.loads(
                (ROOT / "docs/test-plan/data/journeys.json").read_text())["paths"]}]
    if derived.get("savedAgents") != fixture["shapes"]["seeded"]["agents"]:
        out.append(f"seeded agents {fixture['shapes']['seeded']['agents']}: the owner's org has "
                   f"{derived.get('savedAgents')}")
    text = json.dumps(derived)
    for word in ("org:", "orgId", "instanceId", "agentKey", "token", "password", "Sealed", "@"):
        if word in text:
            out.append(f"owner_shape.json's derived shape holds {word!r}: only structure belongs in it")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--derive", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--container", help="--derive: the stack's MongoDB container")
    parser.add_argument("--port", type=int, help="--derive: the port mongod listens on in that container")
    parser.add_argument("--db", default="pxh_human_ht1")
    args = parser.parse_args(argv)
    if args.derive:
        if args.container is None or args.port is None:
            parser.error("--derive needs --container and --port")
        print(json.dumps(derive(args.container, args.port, args.db), indent=1))
        return 0
    found = problems(json.loads(FIXTURE.read_text()))
    for problem in found:
        print(f"owner_shape: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
