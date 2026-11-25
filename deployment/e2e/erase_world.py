"""QAFINAL-FU-ERASE: erased content never comes back, in search or in an answer, on the default stack (MongoDB, Neo4j,
Qdrant, indexing and engine runs: run.sh durable). No live model call: every turn runs on the stack's scripted model.

    python3 erase_world.py <run dir>      e.g. ~/.cache/pxh-e2e/<run>

One conversation with the engine agent holds a unique word in each place a conversation's content goes: the person's
message, the answer, an uploaded file (its blobs and its record in the graph), a memory the job proposed, a file the
job wrote in its workspace, and the engine's own transcript (the journal and the engine's session folder on the
broker's volume). Each place is first shown to hold its word, so no check below passes on a probe that cannot see.
The conversation is then deleted and every place read again.

EW-1  before: the file reached the model, the workspace file, the memory and the journal hold their words, the
      volume, MongoDB and Neo4j hold some; and a new chat that sends the file again, searches for it and is given the
      person's memories brings a word to the model (the scripted model's canaries).
EW-2  the conversations' erasure jobs complete.
EW-3  the same new chat, after the deletion, sends no word to the model, so no answer can carry one; the product's
      search finds none.
EW-4  memories, the workspace files, the run's activity and the journal: none of the words, through the product's
      routes.
EW-5  the broker's volume and Node's blob storage: the conversation's folder is gone and no file holds a word.
EW-6  the stores: no MongoDB document of the stack's database, Neo4j node or Qdrant point holds a word or the file's
      record id.
Prints one line per check; exits non-zero when one fails.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ERASURE_WAIT_S = 180
INDEX_WAIT_S = 240
Json = dict[str, Any]
results: list[bool] = []


def check(name: str, ok: bool, detail: str) -> bool:
    results.append(ok)
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {detail}")
    return ok


def pdf(lines: list[str]) -> bytes:
    """A one-page PDF whose text layer holds ``lines`` (the attachment route takes PDF and images only)."""
    text = " ".join(f"({line.replace('(', '').replace(')', '')}) Tj T*" for line in lines)
    stream = f"BT /F1 12 Tf 14 TL 72 720 Td {text} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


class World:
    """The stack's routes and stores, read as an operator would (the containers' own clients, never a vendor SDK)."""

    def __init__(self, run_dir: Path) -> None:
        os.environ["E2E_STATE_DIR"] = str(run_dir.parent)
        sys.path[:0] = [str(HERE), str(HERE.parents[1] / "integration-tests/harness/e2e")]
        from lane import Stack, multipart

        self._multipart = multipart
        self.stack = Stack.attach(run_dir.name, "durable")
        self.api = self.stack.api
        self.org = self.stack.org_id
        self.engine_agent = str(self.stack.seed["engineAgentKey"])
        self.project = (run_dir / "project").read_text().strip()

    # --- the product's routes ---------------------------------------------------------------------------------

    def upload_attachment(self, filename: str, content: bytes) -> Json:
        body, content_type = self._multipart("files", filename, content, "application/pdf")
        path = f"/api/v1/agents/{self.engine_agent}/conversations/attachments/upload"
        request = urllib.request.Request(f"{self.api.base}{path}", data=body, method="POST")
        request.add_header("Content-Type", content_type)
        request.add_header("Authorization", f"Bearer {self.api.token()}")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                attachments = json.loads(response.read())["attachments"]
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"upload -> {error.code}: {error.read()[:300]!r}") from None
        return dict(attachments[0])

    def get(self, path: str) -> tuple[int, Any]:
        answer = self.api.request("GET", path)
        return answer.status, answer.body

    def search(self, query: str) -> str:
        """The product's search; the search it keeps in the person's history is deleted again, so a query naming a
        word is not counted as the word coming back (EW-6 reads every collection)."""
        answer = self.api.request("POST", "/api/v1/search", {"query": query, "limit": 20})
        if answer.status >= 500:
            return f"<search {answer.status}>"
        search_id = answer.body.get("searchId") if isinstance(answer.body, dict) else None
        if search_id:
            self.api.ok("DELETE", f"/api/v1/search/{search_id}")
        return json.dumps(answer.body)

    def watch(self, words: list[str]) -> None:
        self.stack.model.ok("POST", "/__control/canaries", {"values": words})

    def canary_hits(self) -> int:
        return len(self.stack.model.ok("GET", "/__control/canaries").get("hits") or [])

    # --- the stores ---------------------------------------------------------------------------------------------

    def mongo_holders(self, words: list[str]) -> list[str]:
        """``collection:count`` of every document of the stack's database whose JSON holds a word."""
        script = (
            f"const words = {json.dumps(words)}; const out = [];"
            " d.getCollectionNames().forEach(c => { let n = 0;"
            "  d.getCollection(c).find().forEach(doc => { const s = EJSON.stringify(doc);"
            "   if (words.some(w => s.includes(w))) n += 1; });"
            "  if (n) out.push(c + ':' + n); });"
            " print(out.join(' '));"
        )
        return self.stack.mongo(script).split()

    def neo4j_holders(self, words: list[str]) -> int:
        """Nodes with a property (or an item of a list property) that holds a word, read in the Neo4j container."""
        query = ("MATCH (n) UNWIND [k IN keys(n) | n[k]] AS v"
                 " WITH n, CASE WHEN v IS :: LIST<ANY> THEN reduce(s = '', x IN v | s + ' ' + toString(x))"
                 " ELSE toString(v) END AS text"
                 " WHERE any(w IN $words WHERE text CONTAINS w) RETURN count(DISTINCT n)")
        # The container's own NEO4J_AUTH (user/password), so the password never leaves it.
        done = subprocess.run(
            ["docker", "exec", self.stack.runtime.container("neo4j"), "sh", "-c",
             'cypher-shell -u "${NEO4J_AUTH%%/*}" -p "${NEO4J_AUTH#*/}" --format plain -P "$0" "$1"',
             f"words => {json.dumps(words)}", query],
            check=False, capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError(f"cypher-shell: {done.stderr[-300:]}")
        return int(done.stdout.split()[-1])

    def qdrant_holders(self, words: list[str]) -> int:
        """Points of every collection whose payload holds a word, scrolled from the app container (Qdrant's REST)."""
        program = (
            "import json, os, sys, urllib.request\n"
            "base = 'http://' + os.environ.get('QDRANT_HOST', 'qdrant') + ':' + os.environ.get('QDRANT_PORT', '6333')\n"
            "key = os.environ.get('QDRANT_API_KEY', '')\n"
            "def call(path, body=None):\n"
            "    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),"
            " method='GET' if body is None else 'POST', headers={'api-key': key, 'Content-Type': 'application/json'})\n"
            "    return json.loads(urllib.request.urlopen(req, timeout=30).read())['result']\n"
            "words = json.loads(sys.argv[1]); n = 0\n"
            "for c in call('/collections')['collections']:\n"
            "    offset = None\n"
            "    while True:\n"
            "        page = call('/collections/' + c['name'] + '/points/scroll', {'limit': 256, 'with_payload': True,"
            " 'with_vector': False, **({'offset': offset} if offset is not None else {})})\n"
            "        n += sum(1 for p in page['points'] if any(w in json.dumps(p.get('payload')) for w in words))\n"
            "        offset = page.get('next_page_offset')\n"
            "        if offset is None: break\n"
            "print(n)\n"
        )
        return int(self.stack.exec_app("python3", "-c", program, json.dumps(words)).split()[-1])

    def volume(self, script: str) -> str:
        """A one-shot reader on the broker's data volume (run.sh's data_volume), labelled as this run's."""
        done = subprocess.run(
            ["docker", "run", "--rm", "--label", "pipeshub.harness.owner=x-lead",
             "--label", f"io.pipeshub.e2e.run={self.stack.run}", "-v", "pipeshub_sandbox_data:/d:ro",
             "python:3.12-slim", "sh", "-c", script],
            check=False, capture_output=True, text=True, timeout=300)
        return done.stdout

    def volume_holders(self, words: list[str]) -> list[str]:
        root = f"/d/{self.project}/{self.org}"
        pattern = " ".join(f"-e {w}" for w in words)
        return self.volume(f"[ -d {root} ] && grep -rlF {pattern} {root} 2>/dev/null; true").split()

    def blob_holders(self, words: list[str]) -> list[str]:
        """Files of Node's local blob storage (the app container's ``$HOME/.local``) that hold a word."""
        pattern = " ".join(f"-e {w}" for w in words)
        return self.stack.exec_app("sh", "-c", f"grep -rlF {pattern} /tmp/.local 2>/dev/null; true").split()

    def journal_text(self, session: str) -> str:
        """The session's journal as the product unseals it; an erased conversation's is refused by the reader."""
        try:
            return json.dumps(self.stack.journal(session))
        except Exception as error:
            if "deleted and its data erased" in str(error):
                return ""
            raise

    def folder(self, session: str) -> bool:
        return self.volume(f"[ -e /d/{self.project}/{self.org}/{session} ] && echo yes; true").strip() == "yes"


def main() -> int:
    from smoke_human import (  # after World put the lane's modules on the path
        TERMINAL,
        turn,
    )

    run_dir = Path(sys.argv[1]).resolve()
    world = World(run_dir)
    tag = secrets.token_hex(3)
    # One word per place; each is unique to this run, so a hit anywhere is this check's own content.
    words = {
        "file": f"ewfile{tag}", "message": f"ewmsg{tag}", "answer": f"ewans{tag}",
        "memory": f"ewmem{tag}", "workspace": f"ewwks{tag}",
    }
    every = list(words.values())
    file_query = "harbour tide ledger eastern pier"
    # A new chat that would bring the content back: it sends the erased file again, searches for it, and its run is
    # given the person's memories (each person's own memories are recalled in every chat).
    probe = "\n".join([(f'tool search_internal_knowledge {json.dumps({"query": file_query})}'
                       f' || knowledgegraph__search {json.dumps({"query": file_query})}'), "say probed"])
    try:
        return _world(world, run_dir, turn, TERMINAL, tag, words, every, file_query, probe)
    finally:
        world.watch([])


def _world(world: World, run_dir: Path, turn: Any, terminal: frozenset[str], tag: str,
           words: dict[str, str], every: list[str], file_query: str, probe: str) -> int:
    agent = world.engine_agent
    base = f"/api/v1/agents/{agent}/conversations"

    # EW-1: the content, then each place shown to hold its word.
    ref = world.upload_attachment(f"harbour-{tag}.pdf", pdf([
        "Harbour tide ledger of the eastern pier.",
        f"The eastern pier ledger code is {words['file']}.",
        "The harbour tide ledger closes each quarter.",
    ]))
    record = str(ref["recordId"])
    world.watch([words["file"]])
    script = "\n".join([
        f"Keep the harbour notes {words['message']}.",
        f"shell mkdir -p out && printf 'harbour {words['workspace']}\\n' > out/harbour-{tag}.md",
        "tool remember " + json.dumps({"memories": [{"content": f"Harbour code {words['memory']}.",
                                                    "kind": "preference", "scope": "user"}]}),
        f"say Noted {words['answer']}.",
    ])
    frames, session, run = turn(world.stack, agent, script, decide="approve", attachments=[ref])
    file_read = world.canary_hits()
    (run_dir / "logs" / "erase-world-turn.json").write_text(json.dumps([f.__dict__ for f in frames], default=str))
    ended = frames[-1].type if frames else "none"
    status, body = world.get(f"{base}/{session}/workspace/file?path=out/harbour-{tag}.md")
    in_workspace = words["workspace"] in json.dumps(body)
    memories = json.dumps(world.get("/api/v1/agent-harness/memories")[1])
    journal = world.journal_text(session)
    before = {
        "volume": world.volume_holders(every), "blobs": world.blob_holders(every), "mongo": world.mongo_holders(every),
        "neo4j": world.neo4j_holders([*every, record]), "qdrant": world.qdrant_holders([*every, record]),
    }
    world.watch(every)
    _, probe_session, _ = turn(world.stack, None, probe, attachments=[ref])
    seen_before = world.canary_hits()
    in_journal = {k: words[k] in journal for k in ("message", "answer", "workspace")}
    check("EW-1", ended in terminal and file_read > 0 and in_workspace and words["memory"] in memories
          and all(in_journal.values()) and before["volume"] and before["mongo"] and before["neo4j"]
          and seen_before > 0,
          f"turn {run} ended {ended}; the file reached the model ({file_read} hit(s)); workspace file {status} holds"
          f" its word: {in_workspace}; memory listed: {words['memory'] in memories}; journal holds {in_journal};"
          f" before the deletion: volume files {len(before['volume'])}, blob files {len(before['blobs'])},"
          f" MongoDB {before['mongo']}, Neo4j nodes {before['neo4j']}, Qdrant points {before['qdrant']};"
          f" a new chat with the file and the memories brought {seen_before} hit(s) to the model")

    # EW-2: both conversations deleted (the probe's turn carried the file and the memory too).
    sessions = [session, probe_session]
    world.api.ok("DELETE", f"{base}/{session}")
    world.api.ok("DELETE", f"/api/v1/conversations/{probe_session}")
    done = world_erased(world, sessions)
    check("EW-2", done == len(sessions), f"{done}/{len(sessions)} erasure jobs completed")

    # EW-3: nothing comes back in an answer or a search.
    world.watch(every)
    after_frames, after_session, _ = turn(world.stack, None, probe, attachments=[ref])
    seen_after = world.canary_hits()
    after_end = after_frames[-1].type if after_frames else "none"
    found = [w for w in every if w in world.search(file_query) or w in world.search(w)]
    check("EW-3", seen_after == 0 and not found and after_end in terminal,
          f"a new chat sending the erased file, searching for it and given the memories sent {seen_after} hit(s) to"
          f" the model (before: {seen_before}; its run ended {after_end}); search finds {found or 'no word'}")

    # EW-4: the product's own reads of what the conversation held.
    status_files, files = world.get(f"{base}/{session}/workspace/files")
    status_act, activity = world.get(f"{base}/{session}/runs/{run}/activity")
    memories = json.dumps(world.get("/api/v1/agent-harness/memories")[1])
    journal = world.journal_text(session)
    read = {"memories": memories, "journal": journal, "workspace": json.dumps(files), "activity": json.dumps(activity)}
    left = {where: [w for w in every if w in text] for where, text in read.items()}
    left = {where: hits for where, hits in left.items() if hits}
    check("EW-4", not left, f"words left {left or 'none'}; workspace files {status_files}, activity {status_act},"
                            f" journal {'refused as erased' if not journal else 'readable'}")

    # EW-5 and EW-6, once the last probe's own conversation is erased too.
    world.api.ok("DELETE", f"/api/v1/conversations/{after_session}")
    world_erased(world, [after_session])
    deadline = time.monotonic() + ERASURE_WAIT_S
    while True:
        after = {
            "volume": world.volume_holders(every), "blobs": world.blob_holders(every),
            "mongo": world.mongo_holders(every), "neo4j": world.neo4j_holders([*every, record]),
            "qdrant": world.qdrant_holders([*every, record]),
        }
        if not any(after.values()) or time.monotonic() > deadline:
            break
        time.sleep(3)
    folder = world.folder(session)
    check("EW-5", not folder and not after["volume"] and not after["blobs"],
          f"conversation folder left: {folder}; volume files with a word {after['volume'] or 'none'}"
          f" (before {len(before['volume'])}); blob files {after['blobs'] or 'none'} (before {len(before['blobs'])})")
    check("EW-6", not after["mongo"] and not after["neo4j"] and not after["qdrant"],
          f"MongoDB {after['mongo'] or 'none'} (before {before['mongo']}), Neo4j nodes {after['neo4j']}"
          f" (before {before['neo4j']}), Qdrant points {after['qdrant']} (before {before['qdrant']})")
    print(f"erase_world: {sum(results)}/{len(results)} checks passed (org {world.org})")
    return 0 if all(results) else 1


def world_erased(world: World, sessions: list[str]) -> int:
    """How many of the sessions' erasure jobs completed, waiting up to ERASURE_WAIT_S for all of them."""
    deadline = time.monotonic() + ERASURE_WAIT_S
    while True:
        done = int(world.stack.mongo(
            f"print(d.agentSessionErasures.countDocuments({{orgId: {json.dumps(world.org)},"
            f" sessionId: {{$in: {json.dumps(sessions)}}}, status: 'completed'}}))").strip() or 0)
        if done == len(sessions) or time.monotonic() > deadline:
            return done
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
