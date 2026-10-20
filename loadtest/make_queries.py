#!/usr/bin/env python3
"""Write a question file built from the records this deployment has indexed.

    ./make_queries.py                      # every indexed record -> queries_unique.txt
    ./make_queries.py --count 60 --out queries_wide.txt

One question per distinct record name. With --count, a sample spread over
connectors in proportion to how many indexed records each has. Run with `PIPESHUB_QUERY_ORDER=unique` so no
question repeats within a run: a rotation over a few questions mostly measures
cache hits, not the cost of answering a new question.

Neo4j deployments only. Reads the graph through `cypher-shell` in the Neo4j
container; the password comes from NEO4J_PASSWORD, or from the app container's
environment when that is unset.
"""
from __future__ import annotations

import argparse
import os
import random
import re
import subprocess
import sys

TEMPLATES = {
    "JIRA": [
        'What is the status of the Jira issue "{n}" and what does it describe?',
        'Summarise the Jira ticket "{n}".',
    ],
    "CONFLUENCE": [
        'Summarise the Confluence page "{n}".',
        'What are the key points of the page "{n}"?',
    ],
    "SLACK WORKSPACE": ['What was discussed in the Slack conversation "{n}"?'],
}
DEFAULT_TEMPLATE = ['What does the document "{n}" contain?']


def container(pattern: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    names = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=True,
    ).stdout.split()
    hits = [n for n in names if pattern in n]
    if not hits:
        sys.exit(f"no running container matching '{pattern}'; pass it explicitly")
    return hits[0]


def cypher(neo4j: str, password: str, query: str) -> list[list[str]]:
    out = subprocess.run(
        ["docker", "exec", "-e", "NEO4J_PASSWORD", neo4j, "cypher-shell", "-u", "neo4j", "--format", "plain", query],
        capture_output=True, text=True, env={**os.environ, "NEO4J_PASSWORD": password},
    )
    if out.returncode != 0:
        sys.exit(f"cypher-shell failed: {out.stderr.strip()}")
    rows = []
    for line in out.stdout.splitlines()[1:]:
        connector, _, name = line.partition(", ")
        rows.append([connector.strip('"'), name.strip().strip('"')])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=0, help="questions to write; 0 (default) means every record")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "queries_unique.txt"))
    ap.add_argument("--org", help="only records of this org id")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--neo4j-container")
    ap.add_argument("--app-container")
    args = ap.parse_args()

    neo4j = container("neo4j", args.neo4j_container)
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        app = container("pipeshub-ai", args.app_container)
        password = subprocess.run(
            ["docker", "exec", app, "printenv", "NEO4J_PASSWORD"], capture_output=True, text=True,
        ).stdout.strip()
    org = f' AND r.orgId = "{args.org}"' if args.org else ""
    rows = cypher(
        neo4j, password,
        'MATCH (r:Record) WHERE r.indexingStatus = "COMPLETED" AND coalesce(r.isDeleted, false) = false'
        f' AND r.recordName IS NOT NULL{org} RETURN r.connectorName, r.recordName',
    )
    if not rows:
        sys.exit("no indexed records found")

    rng = random.Random(args.seed)
    by_connector: dict[str, list[str]] = {}
    for connector, name in rows:
        by_connector.setdefault(connector, []).append(re.sub(r"\s+", " ", name).replace('"', "'"))
    out, seen = [], set()
    for connector, names in sorted(by_connector.items()):
        rng.shuffle(names)
        quota = max(1, round(args.count * len(names) / len(rows))) if args.count else len(names)
        for name in names:
            if quota == 0:
                break
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append(rng.choice(TEMPLATES.get(connector, DEFAULT_TEMPLATE)).format(n=name))
            quota -= 1
    rng.shuffle(out)
    if args.count:
        out = out[:args.count]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# One question per indexed record, written by make_queries.py.\n")
        f.write("# Use with PIPESHUB_QUERY_ORDER=unique so no question repeats within a run.\n")
        f.write("\n".join(out) + "\n")
    print(f"{len(out)} questions from {len(by_connector)} connectors -> {args.out}")


if __name__ == "__main__":
    main()
