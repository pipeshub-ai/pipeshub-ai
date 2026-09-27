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
    from collections.abc import Callable

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
        f"**Author:** {author} · **Date:** {str(rec['created'])[:10]}",
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


def restricted_groups(fx: dict) -> set[str]:
    """Groups the installing admin doesn't join: each holds a "who can see this" lesson."""
    return {g["id"] for g in fx["groups"] if not g.get("installer_joins")}


def all_questions(fx: dict) -> list[dict]:
    """The chat landing's questions, then every Build Pack's."""
    packs = [q for qs in (fx.get("pack_questions") or {}).values() for q in qs]
    return list(fx["questions"]) + packs


def select_questions(fx: dict, only: set[str] | None) -> list[dict]:
    """Questions to ask; an unknown id in `only` is an error, never a silent pass."""
    questions = all_questions(fx)
    if not only:
        return questions
    unknown = sorted(only - {q["id"] for q in questions})
    if unknown:
        raise SystemExit(f"unknown question ids: {', '.join(unknown)}")
    return [q for q in questions if q["id"] in only]


def expectation(q: dict, persona: str, fx: dict) -> str:
    """"cites" or "none" for this persona. The installer is in the groups marked
    installer_joins only, so a question hinging on a restricted group is "none"."""
    if persona != "installer":
        return q["personas"][persona]
    if not q.get("restricted"):
        return "cites"
    records = {r["id"]: r for r in fx["records"]}
    threads = {t["id"]: t for t in fx.get("threads", [])}
    closed = restricted_groups(fx)
    for x in q["restricted"]:
        if x in records:
            group = group_of(records[x], fx)
        else:
            first = min((r for r in fx["records"] if r.get("thread") == x), key=lambda r: str(r["created"]))
            group = group_of(first, fx) if x in threads else ""
        if group in closed:
            return "none"
    return "cites"


def upload_groups(fx: dict, persona: str) -> set[str]:
    """Restricted groups whose records the upload models as readable for `persona`."""
    person = next(p for p in fx["people"] if p["id"] == persona)
    return restricted_groups(fx) & set(person.get("groups", []))


def upload_plan(fx: dict) -> tuple[list[tuple[str, str]], dict[str, list[tuple[str, str]]]]:
    """Files to upload: what the installer can read goes in one shared knowledge
    base, and each restricted group's records in their own. Threads are one file."""
    closed = restricted_groups(fx)
    shared: list[tuple[str, str]] = []
    restricted: dict[str, list[tuple[str, str]]] = defaultdict(list)
    threads = {t["id"]: t for t in fx.get("threads", [])}
    by_thread: dict[str, list[dict]] = defaultdict(list)

    def place(group: str, item: tuple[str, str]) -> None:
        (restricted[group] if group in closed else shared).append(item)

    for r in fx["records"]:
        if r.get("thread") and r["thread"] in threads:
            by_thread[r["thread"]].append(r)
            continue
        place(group_of(r, fx), (safe_name(r["title"]) + ".md", render(r, fx)))
    for tid, msgs in by_thread.items():
        t = threads[tid]
        msgs.sort(key=lambda m: str(m["created"]))
        place(group_of(msgs[0], fx), (safe_name(t["title"]) + ".md", render_thread(t, msgs, fx)))
    return shared, dict(restricted)


def find_kb(ph: Pipeshub, name: str) -> str | None:
    listing = ph.knowledge_base.list_knowledge_bases()
    for kb in getattr(listing, "knowledge_bases", None) or getattr(listing, "knowledgeBases", None) or []:
        if getattr(kb, "name", None) == name:
            return kb.id
    return None


def ensure_kb(ph: Pipeshub, name: str, *, fresh: bool = False) -> str:
    """The knowledge base called `name`. `fresh` replaces an existing one, so a run
    never scores against files a previous run left behind under the same names."""
    existing = find_kb(ph, name)
    if existing and not fresh:
        return existing
    if existing:
        ph.knowledge_base.delete_knowledge_base(kb_id=existing)
    return ph.knowledge_base.create_knowledge_base(kb_name=name).id


def kb_names_for(fx: dict, persona: str) -> list[str]:
    """The knowledge bases an upload run for `persona` loads, by name: shared first."""
    names = {g["id"]: g["name"] for g in fx["groups"]}
    _, restricted = upload_plan(fx)
    return ["Acme Corp (shared)"] + [
        f"Acme Corp ({names[g].lower()})" for g in sorted(upload_groups(fx, persona) & set(restricted))
    ]


def existing_kb_ids(ph: Pipeshub, fx: dict, persona: str) -> list[str]:
    """For --skip-upload: the ids of the knowledge bases an earlier upload run loaded."""
    ids = []
    for name in kb_names_for(fx, persona):
        kb_id = find_kb(ph, name)
        if not kb_id:
            sys.exit(f"--skip-upload: no knowledge base named {name!r}; run once without --skip-upload")
        ids.append(kb_id)
    return ids


def upload(ph: Pipeshub, kb_id: str, files: list[tuple[str, str]]) -> None:
    from pipeshub_sdk import models  # noqa: PLC0415 - only the KB-upload path needs the SDK

    payload = [models.UploadRecordsFile(file_name=n, content=b.encode(), content_type="text/markdown") for n, b in files]
    ok = fail = 0
    with ph.knowledge_base.upload_records(kb_id=kb_id, files=payload, record_type="FILE") as stream:
        for ev in stream:
            if ev.event == "file:succeeded": ok += 1
            elif ev.event == "file:failed": fail += 1; print("   failed:", (ev.data or "")[:160])
    print(f"   uploaded {ok} ok, {fail} failed")
    if ok != len(files):
        # A skipped or failed file would leave the run scoring against a partial corpus.
        sys.exit(f"upload incomplete: {ok} of {len(files)} files uploaded")


def kb_record_states(origin: str, jwt: str, kb_id: str, transport: httpx.BaseTransport | None = None) -> list[str]:
    """The indexing status of every record in a knowledge base, as the web app lists it."""
    states: list[str] = []
    page = 1
    with httpx.Client(base_url=origin, timeout=60, transport=transport) as c:
        while True:
            r = c.get(
                f"/api/v1/knowledgeBase/knowledge-hub/nodes/app/{kb_id}",
                params={"flattened": "true", "nodeTypes": "record", "limit": 100, "page": page},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            r.raise_for_status()
            body = r.json()
            states += [n.get("indexingStatus") or "" for n in body.get("items") or []]
            if not (body.get("pagination") or {}).get("hasNext"):
                return states
            page += 1


# Still on its way to COMPLETED; any other status means it won't get there.
_INDEXING = {"", "NOT_STARTED", "QUEUED", "IN_PROGRESS"}


def wait_kb_indexed(
    origin: str, jwt: str, kb_id: str, expected: int, timeout: int = 900, poll: int = 15,
    states: Callable[[str, str, str], list[str]] = kb_record_states,
) -> None:
    """Wait until all `expected` files uploaded to a knowledge base are indexed. It asks
    the knowledge base itself, so a connector record with the same name can't stand in."""
    deadline = time.time() + timeout
    while True:
        now = states(origin, jwt, kb_id)
        stuck = [s for s in now if s != "COMPLETED" and s not in _INDEXING]
        if stuck:
            sys.exit(f"{len(stuck)} records in the knowledge base did not index: {sorted(set(stuck))}")
        done = sum(s == "COMPLETED" for s in now)
        if done >= expected:
            print(f"   indexed {done} of {expected}")
            return
        if time.time() >= deadline:
            sys.exit(f"indexing did not complete in time: {done} of {expected} records")
        print(f"   waiting… {done} of {expected} indexed")
        time.sleep(poll)


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


# "but" turns the sentence; "and" doesn't, so "not healthy and on track" stays negated.
_CLAUSE_BREAK = re.compile(r"[.;:,!?]|\bbut\b")
_NEGATION = re.compile(r"^(?:not|never|no|nor|cannot)$|n't$")
# A cap, not a denial: "no more than five days", "not later than Friday". "No less
# than five" is a minimum, so it stays a negation.
_COMPARATIVE = re.compile(r"\b(?:no|not)\s+(?:more|later|earlier|sooner)\s+than\s*$")
_NEGATION_WINDOW = 3


def _negated(before: str) -> bool:
    """Whether the words just before a phrase, in the same clause, negate it:
    "not on track", "no longer on time", "has not yet been reissued", "has yet to
    be reissued". Three words back, so "you need no approval for purchases up to
    $250" still states the limit; a comparative ("no more than five") is a limit."""
    clause = _CLAUSE_BREAK.split(before)[-1]
    if _COMPARATIVE.search(clause):
        return False
    window = clause.split()[-_NEGATION_WINDOW:]
    return any(_NEGATION.search(w) for w in window) or bool(re.search(r"\byet\s+to\b", " ".join(window)))


def mentions(answer: str, phrase: str) -> bool:
    """Whether the answer states `phrase`: case-insensitive, not negated, and a
    number is not read inside another ("21" in "#211", "250" in "$2500" or "$250,000").
    Used for the pack questions' any-of and must-not phrases; `answer_must_mention`
    stays a plain substring check, as the chat landing's questions were scored."""
    return bool(mention_spans(_normalized(answer), phrase))


def _normalized(answer: str) -> str:
    # Markdown emphasis and curly apostrophes are how the chat writes "up to **$250**" and "don’t".
    return answer.lower().replace("*", "").replace("\u2019", "'")


_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_UNITS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine"}
_MULTIPLIER = r"(?![\s-]*(?:hundred|thousand|million)\b)"
_NUMBERED = ("section", "version", "page", "chapter", "step", "article", "rule", "clause", "item", "no.")


def mention_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    """Where `mentions` finds `phrase` in already-normalized text, as (start, end)."""
    p = phrase.lower().replace("\u2019", "'")
    if p[:1].isdigit():
        # "5th", "v5" and "section 5" aren't five of anything.
        before = r"(?<![\w#.,])" + "".join(rf"(?<!{w} )" for w in _NUMBERED)
    elif p[:1].isalpha():
        # A word starts a word; "five" in "twenty-five" or "twenty five" is another number.
        before = r"(?<![\w-])" + ("".join(rf"(?<!{t} )" for t in _TENS) if p in _UNITS else "")
    else:
        before = ""
    if p[-1:].isdigit() or p.split()[-1:] and p.split()[-1] in _UNITS:
        # "5" or "five" before "hundred" is another number; "250" isn't read in "2500".
        after = (r"(?!\w|[.,]\d)" if p[-1:].isdigit() else r"(?![\w-])") + _MULTIPLIER
    elif " " in p and p[-1:].isalpha():
        # "on time" isn't "on timeout"; a single word still reads inside a longer one ("reissued").
        after = r"(?:e?s)?(?![\w-])"
    else:
        after = ""
    return [
        m.span() for m in re.finditer(before + re.escape(p) + after, text) if not _negated(text[:m.start()])
    ]


# Sentence ends, not clause breaks: "Up to $250 per purchase: no approval needed" is one statement.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
# "$2,500" is one amount; the comma in "$250, no approval" is punctuation.
_AMOUNT = re.compile(r"\$\s?\d+(?:,\d{3})*(?:\.\d+)?|\b\d+(?:,\d{3})*(?:\.\d+)?\s?dollars?\b")


# Parts of a sentence: "; : but" and a comma before a space ("$2,500" stays one number).
_PART = re.compile(r";|:|,(?=\s)|\bbut\b")
# Words that move a part of the sentence to a higher band: "above that", "larger amounts".
# An amount (with any determiner: "the/that/their $250 limit") before a bare "that".
_BAND_OBJECT = (
    r"(?:(?:(?:the|a|an|your|our|their|his|her|that|this|these|those)\s+)?(?:\$|\d)"
    r"|that|this|it|those|these|the limit)"
)
_BIGGER = r"(?:higher|larger|greater|bigger)"
_RAISES = re.compile(
    rf"\b(?:above|over|past|beyond|more than|exceed(?:s|ed|ing)?|in excess of)\s+{_BAND_OBJECT}"
    # "higher amounts", "higher than that", "anything larger", "and higher,"; not "a larger team".
    rf"|\b{_BIGGER}\s+(?:than|amounts?|purchases?|sums?|values?|spend\w*)\b"
    rf"|\b(?:anything|and|or)\s+{_BIGGER}\b"
)
# Pieces of a part, for what follows the no-approval phrase: "and", "but", punctuation.
_PIECE = re.compile(r"\b(?:and|but)\b|[;:]|,(?=\s)")
# The manager, however the answer names them: "your manager", "their manager".
_MANAGER = r"(?:(?:your|a|an|the|my|our|their|his|her)\s+)?manager\b"
# "by submitting the receipt", "by promptly submitting": how it's filed, not who approves.
_FILING = r"(?:\w+ly\s+)?(?!sign|approv|authori)\w+ing\b"
# Who else might approve: "by finance", "from the finance team", "by the CFO".
_APPROVER = (
    r"(?:(?:the|a|an|your|our)\s+)?(?:finance|accounting|accounts|procurement|legal|cfo|controller"
    r"|director|vp|head of \w+|(?:finance|accounts|procurement|legal)\s+team)\b"
)
# What may follow the second-list phrase when it stands alone after the amount: "no approval is needed".
_BARE_REST = re.compile(
    r"^(?:\s*\b(?:is|are|at all|needed|required|necessary)\b)*"
    rf"(?:\s+(?:from|by)\s+{_MANAGER})?(?:\s+by\s+{_FILING}[^;:]*)?[\s.!]*$"
)
# Any mention of approving, outside the no-approval phrases themselves: "must be
# approved", "your manager's sign-off", "require your manager to approve them".
# "sign it off", "sign the purchase off" and "sign-offs" are sign-offs too.
_APPROVAL_WORD = re.compile(r"\bapprov\w*|\bauthori[sz]\w*|\bsign(?:s|ed|ing)?(?:\s+[\w$.,]+){0,3}?[- ]?offs?\b")
# "authorization is not needed", "without signing off": the word is there, but nobody approves.
_NOT_AFTER = re.compile(r"^\s+(?:is|are|was)\s+not\b|^\s+(?:isn't|aren't|wasn't)\b|^\s+not\s+(?:needed|required)\b")
_WITHOUT_BEFORE = re.compile(
    r"\bwithout\s+(?:(?:any|a|an|the|your|my|our|their|his|her)\s+)?(?:\w+'s\s+)?$"
)
# "no approval (is needed) from finance" is about another approver, unless it's the manager.
_OTHER_APPROVER = re.compile(
    rf"^(?:\s+(?:is|are|needed|required|necessary|at all))*\s+(?:from|by)\s+(?:{_APPROVER}|signing|\w+ly\s+signing)"
)
# A later piece that names another approver: "by finance", "from the finance team".
_OTHER_APPROVER_PIECE = re.compile(rf"^\s*(?:from|by)\s+{_APPROVER}")


def _amount_values(text: str) -> set[float]:
    """The amounts named in `text` by value: "$250", "$250.00" and "250 dollars" are one."""
    return {float(re.sub(r"[^\d.]", "", a)) for a in _AMOUNT.findall(text)}


def _approves(text: str, no_approval: list[str]) -> bool:
    """Whether `text` says someone approves, once its no-approval phrases are blanked out."""
    for m in no_approval:
        for start, end in mention_spans(text, m):
            text = text[:start] + " " * (end - start) + text[end:]
    return any(
        not _negated(text[:m.start()])
        and not _without_cancels(text[:m.start()])
        and not _NOT_AFTER.match(text[m.end():])
        for m in _APPROVAL_WORD.finditer(text)
    )


# "can't submit it without your manager's sign-off" still needs the sign-off.
_REQUIRES_BEFORE = re.compile(r"\b(?:can't|cannot|can not|not|never|nothing|nobody|no one|won't)\b|n't\b")


# "It is rejected", "filing fails"; "a validation failure" is a noun, not a block.
# Bare "block", "stop", "halt" and "decline" are left out: they are nouns as often as verbs.
_BLOCKED = (
    r"\b(?:reject(?:s|ed)?|blocks|blocked|fail(?:s|ed)?|den(?:y|ies|ied)|refuse[sd]?|declines|declined"
    r"|bounce[sd]|stops|stopped|halts|halted)\b"
)
# "nothing goes through", "no purchase goes through", "it is rejected".
_REQUIRES_ANYWHERE = re.compile(
    r"\b(?:can't|cannot|can not|not|never|nothing|nobody|no one|won't)\b|n't\b|\bno\s+(?!need|problem|worries)\w+"
    rf"|{_BLOCKED}"
)
# "not rejected", "does not come to a halt": the negation cancels the block.
_NOT_BLOCKED = re.compile(
    rf"(?:\b(?:not|never)\b|n't\b)\s*(?:\w+\s+){{0,3}}?(?:{_BLOCKED}|\b(?:halt|stop|standstill|block)\b)"
)


# "or" / "either" offering an alternative; "or more", "or above" extend a band instead.
_ALTERNATIVE = re.compile(r"\b(?:or|either)\b(?!\s+(?:more|above|higher|over|greater|beyond|larger|bigger)\b)")
_CLAIM_BREAK = re.compile(r"\b(?:but|however|whereas)\b|;")


def _required_without(sentence: str, no_approval: list[str], band_amounts: set[float]) -> bool:
    """Whether a sentence makes an approval a condition: "without your manager's
    sign-off" with a requirement anywhere in it ("…, nothing goes through")."""
    for m in no_approval:
        for start, end in mention_spans(sentence, m):
            sentence = sentence[:start] + " " * (end - start) + sentence[end:]
    # "It is not rejected without …" says the opposite.
    sentence = _mark_new_ranges(_NOT_BLOCKED.sub(lambda m: " " * len(m.group(0)), sentence), band_amounts)
    # Higher bands: "above that", and a range above $250.
    bands = [m.start() for m in _RAISES.finditer(sentence)] + [
        m.start() for m in re.finditer(rf"{_RANGE_MARK}+", sentence)
    ]

    def segment(breaks: re.Pattern[str], at: int) -> tuple[int, int]:
        lo, hi = 0, len(sentence)
        for m in breaks.finditer(sentence):
            if m.end() <= at:
                lo = m.end()
            elif m.start() >= at:
                hi = m.start()
                break
        return lo, hi

    def someone_approves(lo: int, hi: int) -> bool:
        # A finished approval in sentence[lo:hi]; "without your manager's sign-off"
        # is the condition itself.
        return any(
            lo <= m.start() < hi
            and not _negated(sentence[:m.start()]) and not _WITHOUT_BEFORE.search(sentence[:m.start()])
            for m in _APPROVAL_WORD.finditer(sentence)
        )

    def covers(x: int, at: int) -> bool:
        # With no approval between them ("Above $2,500, finance approves expenses
        # rejected …" is a finished claim); from before, the band's whole piece counts.
        lo, hi = segment(_PIECE, x)
        if x < at:
            # "Above $2,500 or it is rejected …": an alternative before the band governs anything.
            governs = min((p for p in anchors if p > x), default=at)
            return not someone_approves(x if at < hi else lo, at) and not _ALTERNATIVE.search(sentence[x:governs])
        # A later band covers it in its piece, or right after a comma when the band
        # is all its piece says (", above $2,500"); not a new clause ("and above …")
        # or an alternative ("or above $2,500").
        if someone_approves(at, x) or _ALTERNATIVE.search(sentence[at:x]):
            return False
        before = [m.group() for m in _PIECE.finditer(sentence) if m.end() == lo]
        return lo <= at < hi or (before == [","] and _only_range(sentence[lo:hi], band_amounts, raises=True))

    # Where the band could first govern: a requirement word or a sign-off.
    anchors = [m.start() for m in _REQUIRES_ANYWHERE.finditer(sentence)] + [
        m.start() for m in _APPROVAL_WORD.finditer(sentence)
    ]

    def covered(a: int, r: int) -> bool:
        # A higher band covers the sign-off when it shares their claim ("but" and ";"
        # start a new one) and covers the requirement, and the sign-off too unless it
        # leads into the requirement ("Without …, nothing above $2,500 goes through").
        claim = segment(_CLAIM_BREAK, a)
        if segment(_CLAIM_BREAK, r) != claim:
            return False
        return any(
            claim[0] <= x < claim[1] and covers(x, r) and (a < r or covers(x, a)) for x in bands
        )

    return any(
        not covered(a.start(), r.start())
        for a in _APPROVAL_WORD.finditer(sentence) if _WITHOUT_BEFORE.search(sentence[:a.start()])
        for r in _REQUIRES_ANYWHERE.finditer(sentence)
    )


def _without_cancels(before: str) -> bool:
    """Whether a "without" just before an approval word means nobody approves,
    rather than that nothing happens without the approval."""
    m = _WITHOUT_BEFORE.search(before)
    if not m:
        return False
    clause = _NOT_BLOCKED.sub(" ", _CLAUSE_BREAK.split(before[:m.start()])[-1])
    return not _REQUIRES_BEFORE.search(clause) and not re.search(_BLOCKED, clause)


# Both bounds of a range: "from $251 to $2,500", "between $251 and $2,500", "$251–2,500".
_BOUND = rf"(?:{_AMOUNT.pattern}|\b\d{{1,3}}(?:,\d{{3}})+\b|\b\d{{3,}}\b)"
_DET = r"(?:(?:the|a|an|your|our)\s+)?"
_RANGE = re.compile(
    rf"\b(?:from|between)\s+({_AMOUNT.pattern})\s*(?:up\s+(?:to|until)|to|and|through|until|-|–|—)\s*{_DET}({_BOUND})"
    rf"|({_AMOUNT.pattern})\s*(?:up\s+(?:to|until)|to|through|until|-|–|—)\s*{_DET}({_BOUND})"
)


# A ceiling: "up to $2,500", "under $2,500", "less than the $2,500 limit".
_CEILING = re.compile(
    r"\b(?:up\s+to|to|under|below|less\s+than|through|until|within|at\s+most|capped\s+at"
    r"|(?:no|not|nothing)\s+(?:more|greater|higher|larger|bigger)\s+than"
    r"|not\s+(?:above|over|beyond|past|exceeding|to\s+exceed)"
    r"|(?:cannot|can't|can\s+not|does\s+not|doesn't|do\s+not|don't|must\s+not|may\s+not|should\s+not|won't|will\s+not)"
    r"\s+exceed"
    r"|(?:at\s+)?(?:an?\s+|the\s+)?(?:maximum|max|cap|ceiling|threshold|limit)(?:\s+of)?)"
    rf"\s+{_DET}({_AMOUNT.pattern})"
)
_RANGE_MARK = "\0"
# An approval with no object of its own: "your manager approves", "approval is required".
_BARE_APPROVAL_END = re.compile(r"(?:\s*\b(?:is|are|needed|required|necessary)\b)*[\s.!]*")


def _mark_new_ranges(text: str, band_amounts: set[float], *, reaching_above: bool = False) -> str:
    """`text` with every range above the $250 band replaced by a mark that no
    splitter breaks ("between $251 and $2,500"); what a sentence says about
    approval there belongs to that range, not to $250. With `reaching_above`, a
    range that only ends above $250 ("from $250 to $2,500") is marked too."""
    def mark(m: re.Match[str]) -> str:
        low, high = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        bounds = [float(re.sub(r"[^\d.]", "", b)) for b in (low, high)]
        above = (max(bounds) if reaching_above else min(bounds)) > max(band_amounts)
        return _RANGE_MARK * len(m.group(0)) if above else m.group(0)
    return _RANGE.sub(mark, text)


def _new_range(text: str, band_amounts: set[float]) -> bool:
    return _RANGE_MARK in _mark_new_ranges(text, band_amounts)


# Words that leave a piece's claim as the range: "only", "for purchases", "applicable".
_RANGE_LEAD = re.compile(
    r"\b(?:but|and|or|else|either|however|whereas|which|that|only|just|for|purchases?|amounts?|expenses?|spend\w*|the|a|an"
    r"|range|applicable|applies|valid|in|of|is|are|limit|cap|ceiling|maximum|threshold)\b"
)


def _ceiling_above(text: str, band_amounts: set[float]) -> bool:
    """Whether `text` sets a ceiling above the $250 band ("under $2,500", "up to $2,500")."""
    return any(max(_amount_values(m.group(1))) > max(band_amounts) for m in _CEILING.finditer(text))


def _only_range(
    text: str, band_amounts: set[float], *, reaching_above: bool = False, raises: bool = False, ceilings: bool = False
) -> bool:
    """Whether `text` says nothing but a range above the $250 band ("only from $251
    to $2,500", "up to the $2,500 limit"), or with `raises` a higher band (", above
    $2,500"), or with `ceilings` a ceiling above it (", up to $2,500")."""
    marked = _mark_new_ranges(text, band_amounts, reaching_above=reaching_above)
    if ceilings:
        marked = _CEILING.sub(
            lambda m: _RANGE_MARK if max(_amount_values(m.group(1))) > max(band_amounts) else m.group(0), marked
        )
    if raises and _RAISES.search(marked):
        # The raise pattern ends at the amount's first character ("above $"); drop the rest.
        marked = re.sub(r"\d[\d,.]*", " ", _RAISES.sub(_RANGE_MARK, marked))
    return _RANGE_MARK in marked and not re.sub(r"[\s\0.!?,;:]", "", _RANGE_LEAD.sub(" ", marked))


def _range_only_next(texts: list[str], band_amounts: set[float], next_sentence: str = "") -> bool:
    """Whether the rest of the sentence (`texts`) says nothing but a range or band
    above $250 (", however, from $251 to $2,500"), or, when nothing is left, the next
    sentence says only that: the no-approval phrase before it is about that band."""
    pieces = [p for text in texts for p in _PIECE.split(_mark_new_ranges(text, band_amounts, reaching_above=True))]
    said = [p for p in pieces if re.sub(r"[\s.!?]", "", _RANGE_LEAD.sub(" ", p))]
    if said:
        # Only when the band ends the sentence: "; above that, your manager approves"
        # opens a new claim.
        return len(said) == 1 and _only_range(said[0], band_amounts, reaching_above=True, raises=True, ceilings=True)
    return _only_range(next_sentence, band_amounts, reaching_above=True, raises=True, ceilings=True)


def _band_approved(parts: list[str], band_amounts: set[float], second: list[str]) -> bool:
    """Whether these parts say someone approves the $250 purchase: a part that names
    that amount (outside a higher-band phrase such as "above $250") and approves, or
    is followed by a part that only says someone approves ("Up to $250: your
    manager's approval is required")."""
    for i, part in enumerate(parts):
        # "approve the $250 purchase from $251 to $2,500" still names $250.
        if not band_amounts & _amount_values(_RAISES.sub(" ", _mark_new_ranges(part, band_amounts))):
            continue
        if _approves(part, second):
            return True
        # Read on past blank parts and lead-ins ("However,", "Please note:") until a
        # part approves or moves to a higher band or a range above $250.
        # A comparison, deadline or ceiling ("compared to $2,500") doesn't end it.
        for after in parts[i + 1:]:
            if _RAISES.search(after) or _new_range(after, band_amounts):
                break
            if _approves(after, second):
                return True
    return False


def states_together(answer: str, first: list[str], second: list[str]) -> bool:
    """Whether a second-list phrase is about a first-list amount. Either one part
    of a sentence holds both, with nothing before the later of the two that moves
    to a higher band or says someone approves ("up to $250 without approval"), or
    the part after the amount is only that phrase ("up to $250: no approval
    needed"). "No approval from finance" is about someone else."""

    def phrase_spans(part: str) -> list[tuple[int, int]]:
        return [
            (start, end) for m in second for start, end in mention_spans(part, m)
            if not _OTHER_APPROVER.match(part[end:])
        ]

    def approved_later(rest: list[list[str]], band_amounts: set[str]) -> bool:
        # Someone approves the $250 purchase later on: in the rest of this sentence
        # or in a later one. A piece naming a higher band ("above that, your manager
        # approves") covers the rest of its sentence, until the $250 amount returns.
        for sentence_rest in rest:
            if _required_without(" ".join(sentence_rest), second, band_amounts):
                return True
            raised = False
            skipped = False  # an approval set aside because a band was in force
            # Each piece with the separator before it; a new part starts after a break.
            pieces, seps = [], []
            for text in sentence_rest:
                chunks = re.split(f"({_PIECE.pattern})", _mark_new_ranges(text, band_amounts))
                pieces += chunks[::2]
                seps += ["", *chunks[1::2]]
            named = False  # an earlier piece names the $250 purchase ("The $250 purchase, …")
            for j, piece in enumerate(pieces):
                if _OTHER_APPROVER_PIECE.match(piece):
                    return True
                # The band phrase's own amount ("over $250") is its object; $250
                # anywhere else is the $250 purchase again, and so was what the
                # sentence approved while the band was in force.
                if _RAISES.search(piece) or _RANGE_MARK in piece:
                    raised = True
                approves = _approves(piece, second)
                if band_amounts & _amount_values(_RAISES.sub(" ", piece)):
                    if raised and skipped:
                        return True
                    raised = False
                # "Your manager approves, from $251 to $2,500": the range that follows
                # is what this approval is about.
                k = next((k for k in range(j + 1, len(pieces)) if pieces[k].strip()), None)
                last = list(_APPROVAL_WORD.finditer(piece))
                range_next = (
                    k is not None and any("," in sep for sep in seps[j + 1:k + 1])
                    # ", or from $251 to $2,500" is an alternative, not what's approved.
                    and not re.match(r"\s*(?:or|either)\b", pieces[k])
                    and _only_range(pieces[k], band_amounts, raises=True)
                    and not named and not band_amounts & _amount_values(piece)
                    and bool(last) and _BARE_APPROVAL_END.fullmatch(piece[last[-1].end():]) is not None
                )
                if approves and not raised and not range_next:
                    return True
                skipped = skipped or approves
                named = named or bool(
                    band_amounts & _amount_values(_RAISES.sub(" ", piece))
                    and not any(mention_spans(piece, m) for m in second)
                )
        return False

    sentences = _SENTENCE_END.split(_normalized(answer))
    for k, sentence in enumerate(sentences):
        later = [[s] for s in sentences[k + 1:]]
        next_sentence = sentences[k + 1] if k + 1 < len(sentences) else ""
        parts = _PART.split(sentence)
        starts = [0] + [m.end() for m in _PART.finditer(sentence)]
        for i, part in enumerate(parts):
            bands = [span for m in first for span in mention_spans(part, m)]
            if not bands:
                continue
            band_end = max(end for _, end in bands)
            band_amounts = {v for b0, b1 in bands for v in _amount_values(part[b0:b1])}
            # An earlier sentence or part that has someone approve the $250 purchase
            # contradicts the no-approval answer that follows it.
            # Read as one run, so "Up to $250. Your manager's approval is required." counts.
            earlier = [p for s in sentences[:k] for p in _PART.split(s)] + parts[:i]
            if _band_approved(earlier, band_amounts, second):
                continue
            for _, end in phrase_spans(part):
                upto = part[:max(end, band_end)]
                # "with no approval above $2,500" or "… from $251 to $2,500": the phrase
                # is about the higher band.
                own_piece_rest = _PIECE.split(
                    _mark_new_ranges(part[max(end, band_end):], band_amounts, reaching_above=True)
                )[0]
                if (
                    not _RAISES.search(upto) and not _approves(upto, second)
                    and not _RAISES.search(own_piece_rest) and _RANGE_MARK not in own_piece_rest
                    and not _ceiling_above(own_piece_rest, band_amounts)
                    and _RANGE_MARK not in _mark_new_ranges(upto, band_amounts, reaching_above=True)
                    and not _range_only_next([sentence[starts[i] + max(end, band_end):]], band_amounts, next_sentence)
                    and not approved_later([[sentence[starts[i] + max(end, band_end):]], *later], band_amounts)
                ):
                    return True
            if _RAISES.search(part) or _approves(part, second):
                continue
            after = parts[i + 1] if i + 1 < len(parts) else ""
            first_piece, *more = _PIECE.split(after)
            if _RAISES.search(first_piece) or _AMOUNT.search(first_piece):
                continue
            stripped = first_piece.strip()
            # "by submitting it for your manager to approve" still has the manager approving.
            bare = any(
                start == 0 and _BARE_REST.match(stripped[end:]) and not _approves(stripped[end:], second)
                for start, end in phrase_spans(stripped)
            )
            # The rest of the sentence after that phrase, with its real separators.
            rest = sentence[starts[i + 1] + len(first_piece):] if i + 1 < len(parts) else ""
            if (
                bare and not _range_only_next([rest], band_amounts, next_sentence)
                and not approved_later([[rest], *later], band_amounts)
            ):
                return True
    return False


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
    # At least one of these, for a fact the model can phrase several ways.
    mention_any = q.get("answer_must_mention_any_of", [])
    if mention_any and not any(mentions(answer, m) for m in mention_any):
        unmentioned.append(" | ".join(mention_any))
    # A second fact about the first one's amount (f2: that amount needs no approval).
    mention_any2 = q.get("answer_must_mention_any_of_2", [])
    if mention_any2 and not states_together(answer, mention_any, mention_any2):
        unmentioned.append(" | ".join(mention_any2) + " (about the amount)")
    # Statements that make an answer wrong however well it cites ("still open").
    unmentioned += [f"not: {m}" for m in q.get("answer_must_not_mention", []) if mentions(answer, m)]
    if expect == "none":
        # A failed run proves nothing about access, so it is not a pass.
        if answer.startswith("ERROR:"):
            return False, f"FAIL ({answer})"
        leaked = [x for x in q.get("restricted", must) if x in cited_ids]
        leaked += [f for f in q.get("restricted_facts", []) if f.lower() in answer.lower()]
        return (not leaked), ("PASS" if not leaked else f"FAIL (leaked restricted: {leaked})")
    ok = enough and any_ok and not forbidden and not unmentioned
    full = "full" if not missing else f"{len(must)-len(missing)}/{len(must)}"
    verdict = f"PASS ({full})" if ok else f"FAIL (missing={missing} any_of_ok={any_ok} forbidden={forbidden} unmentioned={unmentioned})"
    return ok, verdict


# The chat landing asks in "agent" mode by default; "internal_search" is the
# plain retrieval path. Both are scored, because they choose sources differently.
CHAT_MODES = ("internal_search", "agent")


def ask_body(question: str, chat_mode: str, kb_ids: list[str] | None = None) -> dict:
    """The stream request. `kb_ids` limits it to the knowledge bases this run
    loaded, so records another persona's run uploaded can't answer it."""
    body: dict = {"query": question, "chatMode": chat_mode}
    if kb_ids:
        body["filters"] = {"kb": list(kb_ids)}
    return body


def ask(
    origin: str, jwt: str, question: str, chat_mode: str = "internal_search", kb_ids: list[str] | None = None
) -> tuple[str, list[str]]:
    """Ask via the raw SSE endpoint; the generated SDK's stream parser mis-types `data` (spec bug)."""
    answer, cited = [], []
    # Agent mode can take a few minutes on a question it has to search around.
    with httpx.Client(base_url=origin, timeout=300) as c, c.stream(
        "POST", "/api/v1/conversations/stream",
        json=ask_body(question, chat_mode, kb_ids),
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
    ap.add_argument("--skip-restricted", action="store_true",
                    help="model Alice: load only the restricted groups she is in (default models Bob)")
    ap.add_argument("--skip-shared", action="store_true", help="shared KB already uploaded in an earlier run")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--persona", choices=["alice", "bob", "installer"],
                    help="connector mode: ask as this person through the synced Demo connector; no uploads")
    ap.add_argument("--chat-mode", choices=CHAT_MODES, default="internal_search",
                    help="how to ask: agent is what the chat landing uses")
    ap.add_argument("--min-pass", type=int,
                    help="acceptance mode: exit 1 unless every question passes at least this many runs "
                         "(restricted questions must pass every run)")
    args = ap.parse_args()

    env = load_env(args.env)
    origin = env["PIPESHUB_ORIGIN"].rstrip("/")
    fx = yaml.safe_load(open(args.fixture))
    # Checked before any upload, so a typo fails in seconds rather than after indexing.
    questions = select_questions(fx, set(args.only.split(",")) if args.only else None)
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
    # Upload mode asks only its own knowledge bases, also when re-asking with --skip-upload.
    using_kbs = not args.persona
    if using_kbs:
        from pipeshub_sdk import Pipeshub, models  # noqa: PLC0415 - only the KB-upload path needs the SDK

        sdk = Pipeshub(server_url=f"{origin}/api/v1", security=models.Security(bearer_auth=jwt))
    else:
        sdk = contextlib.nullcontext()

    # Connector mode asks through the Demo connector's own permissions, unscoped.
    kb_ids: list[str] | None = None
    with sdk as ph:
        if uploading:
            # Knowledge bases stand in for groups: everything the installer can read
            # goes in one shared KB, each restricted group gets its own, and only the
            # groups the modelled persona is in are loaded.
            readable = upload_groups(fx, "alice" if args.skip_restricted else "bob")
            names = {g["id"]: g["name"] for g in fx["groups"]}
            shared, restricted = upload_plan(fx)
            if args.skip_shared:
                kb_shared = find_kb(ph, "Acme Corp (shared)") or sys.exit(
                    "--skip-shared: no knowledge base named 'Acme Corp (shared)'; run once without --skip-shared"
                )
            else:
                kb_shared = ensure_kb(ph, "Acme Corp (shared)", fresh=True)
                print(f"== uploading {len(shared)} shared records")
                upload(ph, kb_shared, shared)
            kb_ids = [kb_shared]
            expected = [len(shared)]
            for group in sorted(readable & set(restricted)):
                print(f"== uploading {len(restricted[group])} records for {names[group]}")
                kb_ids.append(ensure_kb(ph, f"Acme Corp ({names[group].lower()})", fresh=True))
                upload(ph, kb_ids[-1], restricted[group])
                expected.append(len(restricted[group]))
            print("== waiting for indexing")
            for kb_id, n in zip(kb_ids, expected, strict=True):
                wait_kb_indexed(origin, jwt, kb_id, n)
        elif using_kbs:
            kb_ids = existing_kb_ids(ph, fx, "alice" if args.skip_restricted else "bob")

        persona = args.persona or ("alice" if args.skip_restricted else "bob")
        summary = []
        for q in questions:
            expect = expectation(q, persona, fx)
            passes = 0
            print(f"\n== {q['id']} [{persona}] {q['ask']}")
            for i in range(args.runs):
                t0 = time.time()
                answer, cited_names = ask(origin, jwt, q["ask"], args.chat_mode, kb_ids)
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
            q = next(x for x in all_questions(fx) if x["id"] == qid)
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
