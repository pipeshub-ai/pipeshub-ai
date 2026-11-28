# pyright: ignore-file

"""Helpers of the Jira Cloud simulator suite for what a record streams, how it reindexes and what its ticket reads as:
documents of Atlassian Document Format, what the product streams, and the calls a test makes as a reader would.

Nothing here knows the connector's code: a stream is read as the app reads it (the knowledge-base stream route), a
ticket as the agent tool reads it (``/api/v1/connectors/record/{id}/content`` with a budget, which the Node API hands to the
query service), and a body as Jira's own write API takes it.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests
from connectors.jira.cloud_sim_world import FILE_PREFIX, JiraCloudWorld, basic

# What Jira sends for one file and the connector asks for it: the sync account's credentials, ``redirect=false``.
ATTACHMENT_URL = "/rest/api/3/attachment/content/"
ISSUE_URL = r"^/rest/api/3/issue/\d+$"
COMMENTS_URL = r"^/rest/api/3/issue/\d+/comment$"
COMMENT_URL = r"^/rest/api/3/issue/\d+/comment/\d+$"
CONTENT_URL = r"^/rest/api/3/attachment/content/\d+$"
SEARCH_URL = r"^/rest/api/3/search/jql$"


# ---- Atlassian Document Format -------------------------------------------------------------------------------


def doc(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": list(content)}


def text(value: str, *marks: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": value}
    if marks:
        node["marks"] = list(marks)
    return node


def mark(kind: str, **attrs: Any) -> dict[str, Any]:
    return {"type": kind, "attrs": attrs} if attrs else {"type": kind}


def para(*inline: dict[str, Any] | str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [text(i) if isinstance(i, str) else i for i in inline]}


def node(kind: str, *content: dict[str, Any], **attrs: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": kind}
    if attrs:
        out["attrs"] = attrs
    if content:
        out["content"] = list(content)
    return out


def media(filename: str | None = None, *, kind: str = "file", **attrs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "0f0f0f0f-0000-4000-8000-000000000001", "type": kind, "collection": ""}
    if filename is not None:
        base["alt"] = filename
    return node("media", **{**base, **attrs})


def every_node_doc(tag: str) -> dict[str, Any]:
    """A document with every node and mark the connector's markdown renderer knows, a macro-looking text (``{tag}``) that
    makes Jira's HTML renderer drop the paragraph's text, and the words each node must keep."""
    return doc(
        para(f"macro {{{tag}}} kept"),
        node("heading", text("Heading three words"), level=3),
        node("heading", text("Heading with a bad level"), level="x"),
        node("heading"),
        para(text("bold words", mark("strong")), " ", text("italic words", mark("em")), " ",
             text("code words", mark("code")), " ", text("struck words", mark("strike")), " ",
             text("linked words", mark("link", href="https://example.com/linked")), " ",
             text("unlinked words", mark("link")), " ", text("underlined words", mark("underline")), " ",
             text("colored words", mark("textColor", color="#ff0000")), " ", text("colorless words", mark("textColor")),
             " ", text("odd mark words", mark("subsup", type="sub"))),
        node("bulletList", node("listItem", para("first bullet item")), node("listItem", para("second bullet item")),
             node("listItem")),
        node("orderedList", node("listItem", para("first numbered item")), node("listItem", para("second numbered item"))),
        node("bulletList"),
        node("codeBlock", text("print('code body')"), language="python"),
        node("codeBlock", text("plain code body")),
        node("codeBlock"),
        node("blockquote", para("quoted words")),
        node("panel", para("panel words"), panelType="info"),
        node("table", node("tableRow", node("tableHeader", para("header cell")), node("tableCell", para("body cell")))),
        node("rule"),
        para("before break", node("hardBreak"), "after break"),
        para(node("mention", id="5b10a2844c20165700ede21g", text="@Some Person"), " ",
             node("mention", id="5b10a2844c20165700ede21h", text="Bare Name"), " ", node("mention", id="x", text="")),
        para(node("emoji", shortName=":smile:", text="smiley"), node("emoji", shortName=":wave:"), node("emoji")),
        para(node("status", text="In review", color="blue"), " ", node("inlineCard", url="https://example.com/card"),
             " ", node("date", timestamp="1700000000000"), " ", node("date", timestamp="not-a-date")),
        node("blockCard", url="https://example.com/block"),
        node("embedCard", url="https://example.com/embed"),
        node("blockCard"),
        node("mediaSingle", media("single-image.png")),
        node("mediaGroup", media("group-file.pdf"), media("second-group-file.pdf")),
        node("mediaSingle", media(None, kind="external", url="https://example.com/pic (1).png", alt="external pic")),
        node("mediaSingle", media(None, kind="external", url="https://example.com/plain.png")),
        node("mediaSingle", media("")),
        para(node("mediaInline", id="inline-id", type="file", alt="inline-file.txt")),
        node("unknownNode", para("words inside an unknown node")),
        node("unknownLeaf", text="ignored"),
    )


EVERY_NODE_WORDS = [
    "macro {", "kept", "# Heading", "Heading three words", "Heading with a bad level", "**bold words**", "*italic words*",
    "`code words`", "~~struck words~~", "[linked words](https://example.com/linked)", "unlinked words",
    "<u>underlined words</u>", '<span style="color: #ff0000">colored words</span>', "colorless words",
    "- first bullet item", "- second bullet item", "1. first numbered item", "2. second numbered item",
    "```python\nprint('code body')\n```", "```\nplain code body\n```", "quoted words", "panel words", "header cell",
    "body cell", "---", "before break\nafter break", "@Some Person", "@Bare Name", "smiley", ":wave:", "In review",
    "https://example.com/card", "2023-11-14", "https://example.com/block", "https://example.com/embed",
    "single-image.png", "group-file.pdf second-group-file.pdf", "[external pic](<https://example.com/pic (1).png>)",
    "[https://example.com/plain.png](https://example.com/plain.png)", "inline-file.txt", "words inside an unknown node",
]


# ---- the site: issues, comments and files as a person writes them ----------------------------------------------


def new_issue(w: JiraCloudWorld, project: str, summary: str, description: Any = None, *, as_: str | None = None,
              issuetype: str = "Task", **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"project": {"key": project}, "summary": summary, "issuetype": {"name": issuetype}, **fields}
    if description is not None:
        body["description"] = description
    kwargs = {"as_": as_} if as_ else {}
    return w.sim.rest("POST", "/issue", {"fields": body}, **kwargs)


def add_comment(w: JiraCloudWorld, key: str, body: Any, visibility: dict[str, str] | None = None, *,
                as_: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"body": body}
    if visibility:
        payload["visibility"] = visibility
    kwargs = {"as_": as_} if as_ else {}
    return w.sim.rest("POST", f"/issue/{key}/comment", payload, **kwargs)


def comment_text(value: str) -> dict[str, Any]:
    return doc(para(value))


def roles(w: JiraCloudWorld, project: str) -> dict[str, str]:
    """The project's roles, name -> id."""
    return {name: url.rsplit("/", 1)[-1] for name, url in w.sim.rest("GET", f"/project/{project}/role").items()}


def group_names(w: JiraCloudWorld) -> list[str]:
    return sorted(g["name"] for g in w.sim.rest("GET", "/group/bulk", maxResults=50)["values"])


def png(size: int, fill: int = 7) -> bytes:
    """Bytes that claim to be a PNG (the simulator types a file by its name): ``size`` bytes in all."""
    head = b"\x89PNG\r\n\x1a\n"
    return head + bytes([fill]) * max(0, size - len(head))


def count_calls(w: JiraCloudWorld, fragment: str) -> int:
    return sum(n for k, n in w.sim.paths_called().items() if fragment in k)


# ---- what the product streams and reads -----------------------------------------------------------------------


def stream(w: JiraCloudWorld, record_id: str, timeout: float = 120) -> requests.Response:
    """The record's stream as the app asks for it; never raises on a refusal. The body is read in full (a stream that
    dies half-way raises ``requests.RequestException``)."""
    path = w.client._stream_record_path(record_id)
    for attempt in range(2):
        w.client._ensure_access_token()
        resp = requests.get(w.client._url(path), headers=w.client._headers(), timeout=timeout)
        if resp.status_code == 401 and attempt == 0:
            w.client._invalidate_access_token()
            continue
        return resp
    return resp


def blocks(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, f"stream answered {resp.status_code}: {resp.text[:300]}"
    return json.loads(resp.content)


def groups(container: dict[str, Any]) -> list[dict[str, Any]]:
    return container.get("block_groups") or []


def group_data(container: dict[str, Any]) -> str:
    """The content of every block group, in order."""
    return "\n".join(str(g.get("data") or "") for g in groups(container))


def comment_groups(container: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for g in groups(container) if g.get("sub_type") == "comment"]


def children(container: dict[str, Any]) -> set[str]:
    """The ids of the records every block group names as its files."""
    return {c["child_id"] for g in groups(container) for c in (g.get("children_records") or [])}


def read_record(w: JiraCloudWorld, record_id: str, *, query: str | None = None, max_chars: int = 20000,
                timeout: float = 240) -> requests.Response:
    """What the agent tool reads (``/records/{id}/read``) through the Node API: the indexed content, headed by the ticket's
    context. A ticket's live Jira fields are added to that head."""
    params: dict[str, Any] = {"maxChars": max_chars}
    if query:
        params["query"] = query
    return w.client.request("GET", f"/api/v1/connectors/record/{record_id}/content", params=params, timeout=timeout)


def ids_by_external(records: list[dict[str, Any]]) -> dict[str, str]:
    return {r["externalRecordId"]: r["id"] for r in records}


def file_ids(w: JiraCloudWorld, by_ext: dict[str, str], attachment_ids: list[Any]) -> set[str]:
    return {by_ext[f"{FILE_PREFIX}{a}"] for a in attachment_ids if f"{FILE_PREFIX}{a}" in by_ext}


async def wait_for(check, what: str, timeout: float = 150, interval: float = 3):
    """Poll an async ``check`` until it returns something truthy, and return that; fail with ``what`` otherwise."""
    import asyncio

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await check()
        if last:
            return last
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what} (last answer {last!r})")


def jira_get(w: JiraCloudWorld, path: str, **params: Any) -> requests.Response:
    """A raw GET of Jira's API as the sync account (the simulator's own answer, not the connector's)."""
    return w.sim.http.get(f"{w.sim.base_url}{path}", params=params or None, headers=basic(), timeout=60)


def words_of(adf: Any) -> list[str]:
    """Every text node of an ADF document, in order."""
    out: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if n.get("type") == "text" and n.get("text"):
                out.append(n["text"])
            for v in (n.get("content") or []):
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(adf)
    return out


def first_words(adf: Any, n: int = 30) -> str:
    joined = " ".join(words_of(adf))
    return re.sub(r"\s+", " ", joined)[:n]
