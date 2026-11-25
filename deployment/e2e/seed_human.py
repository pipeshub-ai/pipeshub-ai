"""Seeds a stack a person tests by hand (run.sh human-up, the human-test stack), after seed.py seed and durable,
through Node's public API as the org admin would. Idempotent: the ids go to E2E_STATE and a rerun (run.sh rebuild)
adds only what is missing.

    a member of the org who signs in with a password (E2E_MEMBER_FILE, mode 600)
    two knowledge bases with a few Markdown pages each, uploaded and indexed: one shared with the member, one not
    a skill
    one agent per executor (chat loop, opencode, Codex, Claude Code), shared with the org, each with both knowledge
    bases, the scripted SaaS's MCP tools (seed.py durable registered it) and the skill
    the tool id the MCP server's create_ticket asks under; run.sh then writes the rule itself, with every class
    rule at the release default (seed_config.py human, WP97-FU-1)

Standard library only: it runs on the host.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from human_pages import KNOWLEDGE
from seed import API, MCP_NAME, SeedError, call, load_state, save_state, sign_in

MEMBER_FILE = os.environ["E2E_MEMBER_FILE"]
MEMBER_EMAIL = "member@acme-demo.example"
SKILL_NAME = "release-checklist"
ASK_TOOL = "create_ticket"
MCP_TOOLS = ("lookup_note", "create_ticket")
INDEX_WAIT_S = 900

AGENTS: dict[str, tuple[str, str, str | None]] = {
    "agentLoop": ("Chat assistant (chat loop)", "Answers from the handbook and files tickets.", None),
    "agentOpencode": ("Coder on opencode", "Writes and runs code in a sandbox with opencode.", "opencode"),
    "agentCodex": ("Coder on Codex", "Writes and runs code in a sandbox with Codex.", "codex"),
    "agentClaudeCode": ("Coder on Claude Code", "Writes and runs code in a sandbox with Claude Code.", "claude_code"),
}
SYSTEM_PROMPT = ("You help the team. Use the knowledge bases for policy questions and cite them. "
                 "Use the tracker tools to look up notes and file tickets when asked.")


def member(token: str, state: dict[str, Any]) -> None:
    if os.path.exists(MEMBER_FILE) and state.get("memberId"):
        return
    password = f"Member-{secrets.token_hex(6)}!1a"
    answer, _ = call("POST", "/api/v1/users", {"fullName": "Morgan Member", "email": MEMBER_EMAIL, "role": "member",
                                               "password": password}, token=token)
    user = (answer or {}).get("user") or answer or {}
    state["memberId"] = str(user.get("_id") or user.get("id") or "")
    if not state["memberId"]:
        raise SeedError(f"the member create returned no id: {json.dumps(answer)[:300]}")
    fd = os.open(MEMBER_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"email": MEMBER_EMAIL, "password": password, "id": state["memberId"]}, handle)
    save_state(state)


def multipart(token: str, method: str, path: str, field: str, filename: str, text: str) -> str:
    """A file as the web app sends it (multipart); the answer's body as text."""
    boundary = f"pxh{secrets.token_hex(8)}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: text/markdown\r\n\r\n").encode() + text.encode() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(f"{API}{path}", data=body, method=method)
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        raise SeedError(f"{method} {path} ({filename}) -> {error.code}: {error.read()[:400]!r}") from None


def upload(token: str, kb_id: str, filename: str, text: str) -> str:
    """An upload to the knowledge base (field ``files``); the route answers with progress events."""
    raw = multipart(token, "POST", f"/api/v1/knowledgeBase/{kb_id}/upload", "files", filename, text)
    event = None
    for line in raw.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event == "file:succeeded":
            return str(json.loads(line[5:].strip()).get("recordId"))
    raise SeedError(f"the upload of {filename} reported no stored file: {raw[:400]}")


def share(token: str, kb_id: str, member_id: str, wait_s: float = 60) -> None:
    """Shares with the member once the connectors service has the member in the graph: the user-created event is
    asynchronous, so a share right after the create can be refused ("no longer in this workspace") for a moment."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            call("POST", f"/api/v1/knowledgeBase/{kb_id}/permissions",
                 {"userIds": [member_id], "role": "READER"}, token=token)
            return
        except SeedError as error:
            if "-> 400" not in str(error) or time.monotonic() > deadline:
                raise
            time.sleep(2)


def knowledge(token: str, state: dict[str, Any]) -> None:
    for key, (name, shared, pages) in KNOWLEDGE.items():
        if not state.get(key):
            answer, _ = call("POST", "/api/v1/knowledgeBase", {"kbName": name}, token=token)
            kb_id = str((answer or {}).get("id") or (answer or {}).get("_key")
                        or ((answer or {}).get("knowledgeBase") or {}).get("id") or "")
            if not kb_id:
                raise SeedError(f"the knowledge base create returned no id: {json.dumps(answer)[:300]}")
            records = [upload(token, kb_id, filename, text) for filename, text in pages.items()]
            if shared:
                share(token, kb_id, state["memberId"])
            state[key], state[f"{key}Records"] = kb_id, records
            save_state(state)
    wait_indexed(token, [r for key in KNOWLEDGE for r in state[f"{key}Records"]])


def wait_indexed(token: str, pending: list[str]) -> None:
    pending = list(pending)
    deadline = time.monotonic() + INDEX_WAIT_S
    while pending:
        found, _ = call("GET", f"/api/v1/knowledgeBase/record/{pending[0]}", token=token)
        status = str(((found or {}).get("record") or {}).get("indexingStatus"))
        if status == "COMPLETED":
            pending.pop(0)
            continue
        if status in ("FAILED", "FILE_TYPE_NOT_SUPPORTED") or time.monotonic() > deadline:
            raise SeedError(f"record {pending[0]} was not indexed ({status})")
        time.sleep(5)


def restore_pages(token: str, state: dict[str, Any]) -> None:
    """A seeded page whose content no longer reads back (a blob store lost it) gets it again as a new version
    of the same record, as the record's file replace in the web app does, or, when the product refuses that because the
    current file is gone, as a new record in the same knowledge base; either way it is indexed again."""
    for key, (_, _, pages) in KNOWLEDGE.items():
        for record, (filename, text) in zip(state[f"{key}Records"], pages.items(), strict=True):
            request = urllib.request.Request(f"{API}/api/v1/knowledgeBase/stream/record/{record}")
            request.add_header("Authorization", f"Bearer {token}")
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    if response.read().decode(errors="replace").strip() == text.strip():
                        continue
            except urllib.error.HTTPError:
                pass
            try:
                multipart(token, "PUT", f"/api/v1/knowledgeBase/record/{record}", "file", filename, text)
                print(f"seed_human: restored {filename} as a new version of record {record}")
                continue
            except SeedError as error:
                # A new version reads the current file first; with that file gone the record is made again.
                print(f"seed_human: {filename}: a new version was refused ({str(error)[:120]}); re-creating it")
            call("DELETE", f"/api/v1/knowledgeBase/record/{record}", token=token)
            new = upload(token, state[key], filename, text)
            records = state[f"{key}Records"]
            records[records.index(record)] = new
            save_state(state)
            print(f"seed_human: re-created {filename} as record {new} (was {record})")
    wait_indexed(token, [r for key in KNOWLEDGE for r in state[f"{key}Records"]])


def skill(token: str, state: dict[str, Any]) -> None:
    if state.get("skill"):
        return
    try:
        call("GET", f"/api/v1/skills/{SKILL_NAME}", token=token)
    except SeedError:
        call("POST", "/api/v1/skills", {
            "name": SKILL_NAME, "description": "The steps of the team's release checklist.",
            "body": "# Release checklist\n\n1. Freeze the branch.\n2. Tag the release.\n3. Announce it in #releases.\n",
        }, token=token)
    state["skill"] = SKILL_NAME
    save_state(state)


def agent_fields(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "knowledge": [{"connectorId": state[kb]} for kb in KNOWLEDGE],
        "mcpServers": [{"instanceId": state["mcpInstanceId"], "name": MCP_NAME,
                        "tools": [{"name": t, "fullName": f"mcp_{MCP_NAME}_{t}"} for t in MCP_TOOLS]}],
        "skills": [{"name": SKILL_NAME}],
    }


def agents(token: str, state: dict[str, Any]) -> None:
    for key, (name, description, engine) in AGENTS.items():
        if state.get(key):
            # A re-seeded MCP server (seed.py E2E_SEED_HEAL) has a new id: the agent takes it.
            stored, _ = call("GET", f"/api/v1/agents/{state[key]}", token=token)
            granted = [s.get("instanceId") for s in ((stored or {}).get("agent") or {}).get("mcpServers") or []]
            if state["mcpInstanceId"] not in granted:
                call("PUT", f"/api/v1/agents/{state[key]}", agent_fields(state), token=token)
            continue
        body: dict[str, Any] = {"name": name, "description": description, "systemPrompt": SYSTEM_PROMPT,
                                "shareWithOrg": True}
        if engine:
            body["engineProfile"] = {"engines": [engine]}
        answer, _ = call("POST", "/api/v1/agents/create", body, token=token)
        agent_key = str(((answer or {}).get("agent") or answer or {}).get("_key") or "")
        if not agent_key:
            raise SeedError(f"the agent {name!r} create returned no key: {json.dumps(answer)[:300]}")
        call("PUT", f"/api/v1/agents/{agent_key}", agent_fields(state), token=token)
        state[key] = agent_key
        save_state(state)


def approvals(state: dict[str, Any]) -> None:
    """The tool id the seeded server's create_ticket asks under (an MCP tool's is mcp@<instance id>/<tool>). The rule
    itself is the deployment's, which run.sh writes in the app container (seed_config.py human)."""
    state["askToolId"] = f"mcp@{state['mcpInstanceId']}/{ASK_TOOL}"
    state.pop("approvalsSet", None)
    save_state(state)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed a stack a person tests by hand.")
    _ = parser.parse_args(argv)
    try:
        state = load_state()
        if not state.get("mcpInstanceId"):
            raise SeedError("seed.py durable runs first: no MCP instance in the state")
        token = sign_in()
        member(token, state)
        knowledge(token, state)
        restore_pages(token, state)
        skill(token, state)
        agents(token, state)
        approvals(state)
        print(json.dumps({k: v for k, v in state.items() if k != "token"}))
    except SeedError as error:
        print(f"seed_human: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
