#!/usr/bin/env python3
"""Knowledge-base harness for the Acme Corp fixture.

Uploads the fixture as markdown into knowledge bases on a PipesHub instance,
then asks each golden question N times and scores the citations against the
fixture's must_cite / must_not_cite lists. This is the cheap way to tune the
content before the demo connector exists: the words are identical either way.

Permissions are approximated with two knowledge bases: "shared" (everything
readable by engineering or support) and "restricted" (pricing committee only).
Run with --skip-restricted to model Alice, without it to model Bob.

Usage:
  python kb_harness.py --env bootstrap.env --fixture ../fixture/acme-corp.yaml --runs 3
  python kb_harness.py ... --skip-upload    # KBs already loaded; just ask
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx
import yaml
from pipeshub_sdk import Pipeshub, models

SYSTEM_LABEL = {"GITHUB": "GitHub", "JIRA": "Jira", "SLACK": "Slack", "DRIVE": "Google Drive", "ZENDESK": "Zendesk"}
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
        f"**Author:** {author} · **Date:** {rec['created'][:10]}" + (f" · **Link:** {rec['web_url']}" if rec.get("web_url") else ""),
        "",
    ]
    return "\n".join(head) + rec["body"].rstrip() + "\n"


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


def ask(ph: Pipeshub, question: str) -> tuple[str, list[str]]:
    answer, cited = [], []
    with ph.conversations.stream_chat(query=question, chat_mode="internal_search") as stream:
        for ev in stream:
            payload = json.loads(ev.data) if ev.data else {}
            if ev.event == "TEXT_MESSAGE_CONTENT":
                answer.append(payload.get("delta", ""))
            elif ev.event == "RUN_FINISHED":
                msgs = ((payload.get("result") or {}).get("conversation") or {}).get("messages") or []
                for c in (msgs[-1].get("citations") if msgs else None) or []:
                    meta = c.get("metadata") or {}
                    cited.append(meta.get("recordName") or meta.get("record_name") or "")
            elif ev.event == "RUN_ERROR":
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
    args = ap.parse_args()

    env = load_env(args.env)
    origin = env["PIPESHUB_ORIGIN"].rstrip("/")
    fx = yaml.safe_load(open(args.fixture))
    jwt = login(origin, env["PIPESHUB_ACCOUNT_EMAIL"], env["PIPESHUB_ACCOUNT_PASSWORD"])

    # name -> fixture id (and thread id), for scoring citations
    name_to_id: dict[str, str] = {}
    thread_of: dict[str, str] = {}
    for r in fx["records"]:
        n = safe_name(r["title"])
        name_to_id[n] = r["id"]
        if r.get("thread"): thread_of[r["id"]] = r["thread"]

    with Pipeshub(server_url=f"{origin}/api/v1", security=models.Security(bearer_auth=jwt)) as ph:
        if not args.skip_upload:
            shared, restricted = [], []
            for r in fx["records"]:
                item = (safe_name(r["title"]) + ".md", render(r, fx))
                (restricted if group_of(r, fx) == "pricing-committee" else shared).append(item)
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

        persona = "alice" if args.skip_restricted else "bob"
        only = set(args.only.split(",")) if args.only else None
        summary = []
        for q in fx["questions"]:
            if only and q["id"] not in only: continue
            expect = q["personas"][persona]
            passes = 0
            print(f"\n== {q['id']} [{persona}] {q['ask']}")
            for i in range(args.runs):
                t0 = time.time()
                answer, cited_names = ask(ph, q["ask"])
                cited_ids = set()
                for n in cited_names:
                    key = re.sub(r"\.md$", "", n)
                    rid = name_to_id.get(key)
                    if rid:
                        cited_ids.add(rid); cited_ids.add(thread_of.get(rid, rid))
                missing = [x for x in q.get("must_cite", []) if x not in cited_ids]
                any_of = q.get("must_cite_any_of")
                any_ok = (not any_of) or any(x in cited_ids for x in any_of)
                forbidden = [x for x in q.get("must_not_cite", []) if x in cited_ids]
                if expect == "none":
                    ok = not cited_ids
                    verdict = "PASS" if ok else f"FAIL (leaked: {sorted(cited_ids)})"
                else:
                    ok = not missing and any_ok and not forbidden
                    verdict = "PASS" if ok else f"FAIL (missing={missing} any_of_ok={any_ok} forbidden={forbidden})"
                passes += ok
                print(f"   run {i+1}: {verdict}  [{time.time()-t0:.0f}s]  cited={sorted(cited_ids - set(thread_of.values()))}")
                if not ok:
                    print("      answer:", answer[:300].replace("\n", " "))
            summary.append((q["id"], persona, passes, args.runs))

        print("\n== summary")
        for qid, p, ok, n in summary:
            print(f"   {qid} [{p}]: {ok}/{n}")


if __name__ == "__main__":
    main()
