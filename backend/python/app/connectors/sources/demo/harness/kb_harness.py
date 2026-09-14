#!/usr/bin/env python3
"""Knowledge-base harness for the Acme Corp fixture.

Uploads the fixture as markdown into knowledge bases on a PipesHub instance,
then asks each golden question N times and scores the citations against the
fixture's must_cite / must_not_cite lists. This is the cheap way to tune the
content before the demo connector exists: the words are identical either way.

Permissions are approximated with two knowledge bases: "shared" (everything
readable by engineering or support) and "restricted" (pricing committee only).
Run with --skip-restricted to model Alice, without it to model Bob.

Once the Demo connector is synced on the instance, the same questions can be
asked through the real permission path instead: --persona alice|bob logs in as
that fixture person (password from $DEMO_PERSONA_PASSWORD), --persona installer
uses the account in --env, and nothing is uploaded.

Usage:
  python kb_harness.py --env bootstrap.env --fixture ../fixture/acme-corp.yaml --runs 3
  python kb_harness.py ... --skip-upload    # KBs already loaded; just ask
  python kb_harness.py ... --persona alice  # connector mode, real permissions
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import yaml

if TYPE_CHECKING:
    from pipeshub_sdk import Pipeshub

SYSTEM_LABEL = {"GITHUB": "GitHub", "JIRA": "Jira", "SLACK": "Slack", "DRIVE": "Google Drive", "SERVICENOW": "ServiceNow"}
TYPE_LABEL = {"PULL_REQUEST": "Pull request", "TICKET": "Ticket", "MESSAGE": "Chat message", "FILE": "Document", "COMMENT": "Review comment"}


def load_env(path: str) -> dict[str, str]:
    env = {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip("'\"")
    return env


def login(origin: str, email: str, password: str) -> str:
    with httpx.Client(base_url=origin, timeout=60) as c:
        r = c.post("/api/v1/userAccount/initAuth", json={"email": email}); r.raise_for_status()
        session = r.headers["x-session-token"]
        r = c.post("/api/v1/userAccount/authenticate",
                   json={"method": "password", "email": email, "credentials": {"password": password}},
                   headers={"x-session-token": session}); r.raise_for_status()
        return r.json()["accessToken"]


def safe_name(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._#-]+", "", title).strip()[:120]


def render(rec: dict, fx: dict) -> str:
    people = {p["id"]: p for p in fx["people"]}
    containers = {c["id"]: c for c in fx["containers"]}
    c = containers[rec["container"]]
    author = people[rec["author"]]["name"]
    head = [
        f"# {rec['title']}",
        "",
        f"**System:** {SYSTEM_LABEL[c['system']]} · **Type:** {TYPE_LABEL[rec['type']]} · **In:** {c['name']}",
        f"**Author:** {author} · **Date:** {str(rec['created'])[:10]}" + (f" · **Link:** {rec['web_url']}" if rec.get("web_url") else ""),
        "",
    ]
    return "\n".join(head) + rec["body"].rstrip() + "\n"


def render_thread(t: dict, msgs: list[dict], fx: dict) -> str:
    people = {p["id"]: p for p in fx["people"]}
    containers = {c["id"]: c for c in fx["containers"]}
    c = containers[t["container"]]
    out = [f"# {t['title']}", "", f"**System:** Slack · **Type:** Thread · **In:** {c['name']}", ""]
    for m in msgs:
        out.append(f"**{people[m['author']]['name']}** · {str(m['created'])[:16].replace('T', ' ')}")
        out.append(m["body"].rstrip()); out.append("")
    return "\n".join(out)


def group_of(rec: dict, fx: dict) -> str:
    containers = {c["id"]: c for c in fx["containers"]}
    return rec.get("group") or containers[rec["container"]]["group"]


def ensure_kb(ph: Pipeshub, name: str) -> str:
    listing = ph.knowledge_base.list_knowledge_bases()
    for kb in getattr(listing, "knowledge_bases", None) or getattr(listing, "knowledgeBases", None) or []:
        if getattr(kb, "name", None) == name:
            return kb.id
    return ph.knowledge_base.create_knowledge_base(kb_name=name).id


def upload(ph: Pipeshub, kb_id: str, files: list[tuple[str, str]]) -> None:
    from pipeshub_sdk import models  # noqa: PLC0415 - only the KB-upload path needs the SDK

    payload = [models.UploadRecordsFile(file_name=n, content=b.encode(), content_type="text/markdown") for n, b in files]
    ok = fail = 0
    with ph.knowledge_base.upload_records(kb_id=kb_id, files=payload, record_type="FILE") as stream:
        for ev in stream:
            if ev.event == "file:succeeded": ok += 1
            elif ev.event == "file:failed": fail += 1; print("   failed:", (ev.data or "")[:160])
    print(f"   uploaded {ok} ok, {fail} failed")


def wait_indexed(ph: Pipeshub, probe_query: str, expect_substr: str, timeout: int = 900) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = ph.semantic_search.search(query=probe_query, limit=5)
            names = [h.metadata.record_name or "" for h in (s.search_response.search_results or []) if h.metadata]
            if any(expect_substr.lower() in n.lower() for n in names):
                print("   indexed"); return
        except Exception as e:
            print("   waiting…", str(e)[:70])
        time.sleep(15)
    sys.exit("indexing did not complete in time")


def iter_sse(resp: httpx.Response):
    """Yield (event, data) pairs from a text/event-stream response."""
    event, data = None, []
    for line in resp.iter_lines():
        if line == "":
            if event or data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def build_name_index(fx: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Record title -> fixture id, and message id -> thread id, for scoring citations.

    KB uploads carry the sanitised filename; connector records carry the exact title.
    """
    name_to_id: dict[str, str] = {}
    thread_of: dict[str, str] = {}
    for r in fx["records"]:
        name_to_id[safe_name(r["title"])] = r["id"]
        name_to_id[r["title"]] = r["id"]
        if r.get("thread"):
            thread_of[r["id"]] = r["thread"]
    for t in fx.get("threads", []):
        name_to_id[safe_name(t["title"])] = t["id"]
        name_to_id[t["title"]] = t["id"]
    return name_to_id, thread_of


def cited_fixture_ids(cited_names: list[str], name_to_id: dict[str, str], thread_of: dict[str, str]) -> set[str]:
    ids: set[str] = set()
    for n in cited_names:
        rid = name_to_id.get(re.sub(r"\.md$", "", n))
        if rid:
            ids.add(rid)
            ids.add(thread_of.get(rid, rid))
    return ids


def score(q: dict, expect: str, cited_ids: set[str], answer: str) -> tuple[bool, str]:
    """Score one answer against a golden question's must/must-not lists.

    ``expect`` is "cites" or "none" (the persona must not see the restricted
    material). Returns (passed, verdict text).
    """
    must = q.get("must_cite", [])
    missing = [x for x in must if x not in cited_ids]
    enough = (len(must) - len(missing)) >= q.get("min_cite", len(must))
    any_of = q.get("must_cite_any_of")
    any_of2 = q.get("must_cite_any_of_2")
    any_ok = ((not any_of) or any(x in cited_ids for x in any_of)) and ((not any_of2) or any(x in cited_ids for x in any_of2))
    forbidden = [x for x in q.get("must_not_cite", []) if x in cited_ids]
    mention = q.get("answer_must_mention", [])
    unmentioned = [m for m in mention if m.lower() not in answer.lower()]
    if expect == "none":
        leaked = [x for x in q.get("restricted", must) if x in cited_ids]
        return (not leaked), ("PASS" if not leaked else f"FAIL (leaked restricted: {leaked})")
    ok = enough and any_ok and not forbidden and not unmentioned
    full = "full" if not missing else f"{len(must)-len(missing)}/{len(must)}"
    verdict = f"PASS ({full})" if ok else f"FAIL (missing={missing} any_of_ok={any_ok} forbidden={forbidden} unmentioned={unmentioned})"
    return ok, verdict


def ask(origin: str, jwt: str, question: str) -> tuple[str, list[str]]:
    """Ask via the raw SSE endpoint; the generated SDK's stream parser mis-types `data` (spec bug)."""
    answer, cited = [], []
    with httpx.Client(base_url=origin, timeout=180) as c, c.stream(
        "POST", "/api/v1/conversations/stream",
        json={"query": question, "chatMode": "internal_search"},
        headers={"Authorization": f"Bearer {jwt}", "Accept": "text/event-stream"},
    ) as resp:
        resp.raise_for_status()
        for event, raw in iter_sse(resp):
            try: payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError: payload = {}
            if event == "TEXT_MESSAGE_CONTENT":
                answer.append(payload.get("delta", ""))
            elif event == "RUN_FINISHED":
                msgs = ((payload.get("result") or {}).get("conversation") or {}).get("messages") or []
                for c_ in (msgs[-1].get("citations") if msgs else None) or []:
                    meta = (c_.get("citationData") or {}).get("metadata") or c_.get("metadata") or {}
                    cited.append(meta.get("recordName") or "")
            elif event == "RUN_ERROR":
                return f"ERROR: {payload.get('message')}", []
    return "".join(answer), cited


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--skip-upload", action="store_true")
    ap.add_argument("--skip-restricted", action="store_true", help="model Alice: don't load the pricing-committee KB")
    ap.add_argument("--skip-shared", action="store_true", help="shared KB already uploaded in an earlier run")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--persona", choices=["alice", "bob", "installer"],
                    help="connector mode: ask as this person through the synced Demo connector; no uploads")
    ap.add_argument("--min-pass", type=int,
                    help="acceptance mode: exit 1 unless every question passes at least this many runs "
                         "(restricted questions must pass every run)")
    args = ap.parse_args()

    env = load_env(args.env)
    origin = env["PIPESHUB_ORIGIN"].rstrip("/")
    fx = yaml.safe_load(open(args.fixture))
    if args.persona in ("alice", "bob"):
        person = next(p for p in fx["people"] if p["id"] == args.persona)
        password = os.environ.get("DEMO_PERSONA_PASSWORD")
        if not password:
            sys.exit("set DEMO_PERSONA_PASSWORD to the password given to the invited persona accounts")
        jwt = login(origin, person["email"], password)
    else:
        jwt = login(origin, env["PIPESHUB_ACCOUNT_EMAIL"], env["PIPESHUB_ACCOUNT_PASSWORD"])

    name_to_id, thread_of = build_name_index(fx)

    uploading = not args.skip_upload and not args.persona
    if uploading:
        from pipeshub_sdk import Pipeshub, models  # noqa: PLC0415 - only the KB-upload path needs the SDK

        sdk = Pipeshub(server_url=f"{origin}/api/v1", security=models.Security(bearer_auth=jwt))
    else:
        sdk = contextlib.nullcontext()

    with sdk as ph:
        if uploading:
            shared, restricted = [], []
            threads = {t["id"]: t for t in fx.get("threads", [])}
            by_thread: dict[str, list[dict]] = defaultdict(list)
            for r in fx["records"]:
                if r.get("thread") and r["thread"] in threads:
                    by_thread[r["thread"]].append(r); continue
                item = (safe_name(r["title"]) + ".md", render(r, fx))
                (restricted if group_of(r, fx) == "pricing-committee" else shared).append(item)
            for tid, msgs in by_thread.items():
                t = threads[tid]; msgs.sort(key=lambda m: str(m["created"]))
                item = (safe_name(t["title"]) + ".md", render_thread(t, msgs, fx))
                (restricted if group_of(msgs[0], fx) == "pricing-committee" else shared).append(item)
            if not args.skip_shared:
                print(f"== uploading {len(shared)} shared records")
                kb_shared = ensure_kb(ph, "Acme Corp (shared)")
                upload(ph, kb_shared, shared)
            if not args.skip_restricted:
                print(f"== uploading {len(restricted)} restricted records")
                kb_res = ensure_kb(ph, "Acme Corp (pricing committee)")
                upload(ph, kb_res, restricted)
            print("== waiting for indexing")
            wait_indexed(ph, "why was the billing worker retry logic changed", "482")
            if not args.skip_restricted:
                wait_indexed(ph, "enterprise pricing strategy platform fee", "pricing")

        persona = args.persona or ("alice" if args.skip_restricted else "bob")
        only = set(args.only.split(",")) if args.only else None
        summary = []
        for q in fx["questions"]:
            if only and q["id"] not in only: continue
            # The installer joins the shared groups only, so they see what Alice sees.
            expect = q["personas"]["alice" if persona == "installer" else persona]
            passes = 0
            print(f"\n== {q['id']} [{persona}] {q['ask']}")
            for i in range(args.runs):
                t0 = time.time()
                answer, cited_names = ask(origin, jwt, q["ask"])
                cited_ids = cited_fixture_ids(cited_names, name_to_id, thread_of)
                ok, verdict = score(q, expect, cited_ids, answer)
                passes += ok
                print(f"   run {i+1}: {verdict}  [{time.time()-t0:.0f}s]  cited={sorted(cited_ids - set(thread_of.values()))}")
                if not ok:
                    print("      answer:", answer[:900].replace("\n", " "))
            summary.append((q["id"], persona, passes, args.runs))

        print("\n== summary")
        failed = []
        for qid, p, ok, n in summary:
            q = next(x for x in fx["questions"] if x["id"] == qid)
            # A leak of restricted material is a failure of the whole demo, so
            # questions with a restricted list must pass every run.
            need = n if q.get("restricted") else (args.min_pass if args.min_pass is not None else 0)
            verdict = "" if ok >= need else f"   <-- below {need}/{n}"
            print(f"   {qid} [{p}]: {ok}/{n}{verdict}")
            if ok < need:
                failed.append(qid)
        if args.min_pass is not None:
            if failed:
                print(f"ACCEPTANCE FAILED for {persona}: {', '.join(failed)}")
                sys.exit(1)
            print(f"ACCEPTANCE PASSED for {persona}")


if __name__ == "__main__":
    main()
