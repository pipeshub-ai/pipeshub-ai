"""Calls the real-Python journeys share: knowledge bases, scripted model turns and what the model was shown."""

from __future__ import annotations

from typing import Any

import requests

from helper.collab_stack import chats
from helper.collab_stack.client import Api
from helper.collab_stack.fake_backend import FakeBackend, Recorded
from helper.collab_stack.fake_llm import LlmTurn, tool_names
from helper.collab_stack.identity import Actor, Directory

KB = "/api/v1/knowledgeBase"
UI_HEADERS = {"client-name": "web"}
LIST_FILES = "knowledgegraph__list_files"
SAVE_ARTIFACT = "artifacts__save_artifact"


def offers(tool: str):  # noqa: ANN201
    """Predicate for ``llm_turn(when=...)``: the agent loop's own call, the one that offers ``tool``."""

    def accepts(body) -> bool:  # noqa: ANN001
        return tool in tool_names(body)

    return accepts


def offers_ending(suffix: str):  # noqa: ANN201
    """Predicate: some offered tool's name ends with ``suffix``."""

    def accepts(body) -> bool:  # noqa: ANN001
        return any(n.endswith(suffix) for n in tool_names(body))

    return accepts


def lacks_ending(suffix: str):  # noqa: ANN201
    """Predicate: the agent loop's call (it offers ``fetch_tools``) before any tool ending with ``suffix`` is loaded."""

    def accepts(body) -> bool:  # noqa: ANN001
        names = tool_names(body)
        return "fetch_tools" in names and not any(n.endswith(suffix) for n in names)

    return accepts


def create_kb(api: Api, who: Actor, name: str) -> str:
    resp = api.post(f"{KB}/", who, json_body={"kbName": name})
    assert resp.status_code == 200, f"create kb: {resp.status_code} {resp.text[:300]}"
    return resp.json()["id"]


def upload_text(api: Api, who: Actor, kb_id: str, name: str, text: str) -> None:
    """A text file in the person's knowledge base; the record and its permission edge are written by the real connectors service."""
    resp = requests.post(
        f"{api.base_url}{KB}/{kb_id}/upload",
        headers=api.headers(who),
        files={"files": (name, text.encode(), "text/plain")},
        data={"recordName": name, "isVersioned": "true", "lastModified": "1700000000000", "fileSize": str(len(text))},
        timeout=60,
    )
    assert resp.status_code == 200, f"upload {name}: {resp.status_code} {resp.text[:300]}"


def upload_attachment(node_url: str, who: Actor, name: str = "notes.txt", text: str = "Refund policy: 30 days.") -> str:
    """A chat attachment through the real upload route (the query service writes the record and the uploader's OWNER edge)."""
    resp = requests.post(
        f"{node_url}/api/v1/conversations/attachments/upload",
        headers={"Authorization": f"Bearer {Directory.session_token(who)}"},
        files={"files": (name, text.encode(), "text/plain")},
        timeout=60,
    )
    assert resp.status_code == 200, f"attachment upload: {resp.status_code} {resp.text[:300]}"
    return resp.json()["attachments"][0]["recordId"]


def tool_results(call: Recorded) -> list[str]:
    """The tool messages the model was shown in one request."""
    return [m.get("content") or "" for m in call.body["messages"] if m.get("role") == "tool"]


def latest_tool_result(call: Recorded) -> str:
    """The newest tool message of a request: the result of the call the loop just made. Earlier turns' tool results are history
    every participant can read, so they come first; the loop's step footer is dropped."""
    results = tool_results(call)
    assert results, "the request carries no tool result"
    return results[-1].split("\n\n[loop:", 1)[0]


def loop_calls(fake: FakeBackend, mark: int, tool: str) -> list[Recorded]:
    return [c for c in fake.llm_calls(mark) if tool in tool_names(c.body)]


def stream_turn(api: Api, fake: FakeBackend, who: Actor, chat: str, query: str, *turns: LlmTurn, **body: Any):  # noqa: ANN201
    """Script the model, stream one follow-up through Node as the web UI does, and return the finished call.

    The UI names itself in a ``client-name`` header, which Node forwards; without it the query service does not emit the
    interactive ``ask_user_question`` card."""
    fake.script_llm(*turns)
    call = api.stream(chats.stream_path(chat), who, json_body={"query": query, "chatMode": "internal_search", **body}, headers=UI_HEADERS).finish(90)
    assert call.status == 200 and call.result is not None, (call.status, call.text[-600:])
    return call
