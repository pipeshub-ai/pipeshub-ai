"""WP114-FU-E2E: the harness on a stack whose document store is PostgreSQL (run.sh with E2E_DOC_STORE=postgres), checked
in the store itself after the browser specs. No live model call: turns run on the stack's scripted model.

    python3 pg_world.py <run dir>      e.g. ~/.cache/pxh-e2e/<run>

PGW-1  a scripted turn's run, journal and conversation are rows of PostgreSQL (harness.runs, harness.session_events,
       conversation.chat_sessions) under the org, and MongoDB holds no harness or conversation document of the org.
PGW-2  the conversations the specs deleted (the durable spec's erased-sessions, and this check's own turn) are erased:
       each erasure job completed, what it deletes is gone, the journal rows are content-free tombstones (beside the erasure's own
       `session.erased` record), the runs
       hold no input, and the turn's own words are in no harness or conversation row of the org.
PGW-3  the org's deletion (DELETE /api/v1/org, the last step: it ends the stack's org) erases what the route promises
       on PostgreSQL: the org's memories, schedules, engine sign-ins and Inbox rows, and the org reads
       as deleted.
Prints one line per check; exits non-zero when one fails. Every table and column read here is the migrate job's
(backend/db/versions/harness, conversation).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

# What a session's erasure deletes outright (session-store-eraser/pg.ts); its journal rows stay as tombstones that keep
# the envelope, and its runs stay with their inputs emptied.
SESSION_DELETED = ("run_events", "admission_receipts", "workspace_heads", "workspace_manifests", "workspace_writers",
                   "sandbox_snapshots")
# What DELETE /api/v1/org erases before the org is marked deleted (org.controller deleteOrganization).
ORG_DELETE_TABLES = ("memories", "schedules", "engine_signins", "inbox_preferences",
                     "run_end_notices", "run_watchers")
MONGO_COLLECTIONS = ("agentRuns", "agentEvents", "sessionEvents", "chatSessions", "chatSessionMessages")
ERASURE_WAIT_S = 120

results: list[bool] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {detail}")  # noqa: T201 - the check's report


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve()
    os.environ["E2E_STATE_DIR"] = str(run_dir.parent)
    here = Path(__file__).resolve().parent
    sys.path[:0] = [str(here), str(here.parents[1] / "integration-tests/harness/e2e")]
    from lane import Stack
    from smoke_human import TERMINAL, answer, turn

    stack = Stack.attach(run_dir.name, "pg")
    if stack.env.get("E2E_DOC_STORE") != "postgres":
        print(f"pg_world: {run_dir.name} does not run its document store on PostgreSQL")  # noqa: T201
        return 2
    org = stack.org_id
    pg_container = stack.runtime.container("postgres")

    def sql(query: str, *args: str) -> str:
        # The superuser on the container's socket: the check reads across row policies, as an operator would.
        argv = ["docker", "exec", pg_container, "psql", "-U", "postgres", "-d", stack.env["PG_DATABASE"], "-AtX",
                "-v", "ON_ERROR_STOP=1"]
        for i, value in enumerate(args):
            argv += ["-v", f"a{i}={value}"]
        # psql substitutes :'name' only in a script it reads, never in -c: the values stay quoted literals.
        argv = [argv[0], argv[1], "-i", *argv[2:]]
        done = subprocess.run([*argv, "-f", "-"], input=query, check=False, capture_output=True, text=True,
                              timeout=60)
        if done.returncode != 0:
            raise RuntimeError(f"psql: {done.stderr[-400:]}")
        return done.stdout.strip()

    def count(table: str, where: str, *args: str) -> int:
        return int(sql(f"SELECT count(*) FROM {table} WHERE {where}", *args) or 0)

    # PGW-1
    marker = f"pgw-{secrets.token_hex(3)}"
    frames, session, run = turn(stack, stack.seed["agentKey"], f"say {marker}")
    ended = frames[-1].type if frames else "none"
    runs = count("harness.runs", "org_id = :'a0' AND session_id = :'a1'", org, session)
    events = count("harness.session_events", "org_id = :'a0' AND session_id = :'a1'", org, session)
    conversation = count("conversation.chat_sessions", "org_id = :'a0' AND id = :'a1'", org, session)
    check("PGW-1", ended in TERMINAL and marker in answer(frames) and runs >= 1 and events >= 1 and conversation == 1,
          f"turn ended {ended}; PostgreSQL holds {runs} run(s), {events} journal event(s), {conversation} conversation")
    in_mongo = {name: int(stack.mongo(f"print(d.getCollection({json.dumps(name)}).countDocuments({{}}))").strip() or 0)
                for name in MONGO_COLLECTIONS}
    check("PGW-1m", not any(in_mongo.values()), f"MongoDB documents: {in_mongo}")

    # PGW-2
    def word_holders() -> list[str]:
        """The harness and conversation tables with a row of the org that holds the turn's own words."""
        tables = sql("SELECT string_agg(table_schema || '.' || table_name, ' ') FROM information_schema.columns"
                     " WHERE table_schema IN ('harness', 'conversation') AND column_name = 'org_id'").split()
        return [t for t in tables
                if count(f"{t} AS r", "r.org_id = :'a0' AND to_jsonb(r)::text LIKE '%' || :'a1' || '%'", org, marker)]

    held = word_holders()
    stack.api.ok("DELETE", f"/api/v1/agents/{stack.seed['agentKey']}/conversations/{session}")
    erased_file = run_dir / "control" / "erased-sessions"
    sessions = [session, *(s for s in (erased_file.read_text().split() if erased_file.exists() else []) if s)]
    listed = ",".join(sessions)
    in_list = "org_id = :'a0' AND session_id = ANY(string_to_array(:'a1', ','))"
    deadline = time.monotonic() + ERASURE_WAIT_S
    while True:
        done = count("harness.session_erasures", f"{in_list} AND status = 'completed'", org, listed)
        if done == len(sessions) or time.monotonic() > deadline:
            break
        time.sleep(2)
    left = {t: count(f"harness.{t}", in_list, org, listed) for t in SESSION_DELETED if has_session_column(sql, t)}
    left["session_events with content"] = count("harness.session_events",
                                                f"{in_list} AND (doc->>'erased') IS DISTINCT FROM 'true'"
                                                " AND doc->>'type' <> 'session.erased'", org, listed)
    left["runs with an input"] = count("harness.runs", f"{in_list} AND COALESCE(jsonb_typeof(doc->'input'), 'null')"
                                       " <> 'null'", org, listed)
    left = {t: n for t, n in left.items() if n}
    holders = word_holders()
    # `held` shows the probe finds the words where they were kept before the deletion.
    check("PGW-2", bool(held) and done == len(sessions) and not left and not holders,
          f"{done}/{len(sessions)} erasures completed; left: {left or 'none'}; the turn's words in {held} before,"
          f" in {holders or 'none'} after")

    # PGW-3
    before = {t: count(f"harness.{t}", "org_id = :'a0'", org) for t in ORG_DELETE_TABLES}
    shown = stack.api.ok("GET", "/api/v1/org")
    shown = shown.get("data", shown)
    confirm = shown.get("registeredName") or shown.get("shortName") or shown.get("contactEmail")
    stack.api.ok("DELETE", "/api/v1/org", {"confirmName": confirm})
    after = {t: count(f"harness.{t}", "org_id = :'a0'", org) for t in ORG_DELETE_TABLES}
    deleted = sql("SELECT is_deleted FROM identity.orgs WHERE org_id = :'a0'", org)
    check("PGW-3", not any(after.values()) and deleted == "t",
          f"rows before {sum(before.values())} {before}, after {after}; org deleted: {deleted}")
    print(f"pg_world: {sum(results)}/{len(results)} checks passed (run {run}, org {org})")  # noqa: T201
    return 0 if all(results) else 1


def has_session_column(sql, table: str) -> bool:  # noqa: ANN001 - the psql runner above
    return sql("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'harness' "
               "AND table_name = :'a0' AND column_name = 'session_id'", table) == "1"


if __name__ == "__main__":
    sys.exit(main())
