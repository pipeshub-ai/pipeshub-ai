# pyright: ignore-file

"""
Jira Data Center on the simulator – the text a stream carries
=============================================================

The simulator holds each description and comment as wiki markup and as the HTML Jira's renderer makes of it. A streamed
issue must carry, block by block, the words of that HTML (headings, paragraphs, list items, table cells).

  TC-JDSIM-TEXT-001  An issue's streamed description and unrestricted comments carry the text the simulator's HTML holds
  TC-JDSIM-TEXT-002  A description or comment with a pasted JSON payload (the wiki reads it as an unknown macro): the stream
                     carries the payload, never the renderer's error text
  TC-JDSIM-TEXT-003  A comment restricted to a group or role is not in the issue's stream: every reader of the issue reads
                     the stream (strict expected failure: open pull request #6212 makes such a comment a record of its own)
"""

import json
import re
from html.parser import HTMLParser

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded

class _Blocks(HTMLParser):
    """The text of each block element (paragraph, list item, heading, table cell, preformatted text) of rendered HTML."""

    BLOCKS = frozenset({"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "pre", "div", "blockquote"})

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._cur: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        self._cur.append(data)

    def _flush(self) -> None:
        if self._cur:
            self.blocks.append("".join(self._cur))
            self._cur = []

    def close(self) -> None:
        super().close()
        self._flush()


def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def html_blocks_raw(rendered: str) -> list[str]:
    """The text of each block of an HTML string, unfiltered."""
    parser = _Blocks()
    parser.feed(rendered or "")
    parser.close()
    return parser.blocks or [rendered]


def html_blocks(rendered: str) -> list[str]:
    parser = _Blocks()
    parser.feed(rendered or "")
    parser.close()
    return [b for b in (words(x) for x in parser.blocks) if len(b.split()) >= 5]


def stream_words(blob: bytes) -> str:
    """Every string of the streamed JSON, as words."""
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            # a block may hold the HTML Jira rendered (links to issues, for one): its text is what a reader gets
            out.append(" ".join(html_blocks_raw(node)) if "<" in node and ">" in node else node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        walk(json.loads(blob))
    except ValueError:
        out.append(blob.decode("utf-8", "replace"))
    return words(" ".join(out))


def _issue_text(w: JiraDcWorld, tid: str) -> tuple[list[str], list[str], list[str]]:
    """(description blocks, unrestricted comment blocks, restricted comment blocks) as Jira renders them."""
    issue = w.sim.vendor_get(f"/rest/api/2/issue/{tid}", fields="description", expand="renderedFields").json()
    description = html_blocks((issue.get("renderedFields") or {}).get("description") or "")
    open_, closed = [], []
    for c in w.sim.vendor_get(f"/rest/api/2/issue/{tid}/comment", expand="renderedBody").json()["comments"]:
        (closed if c.get("visibility") else open_).extend(html_blocks(c.get("renderedBody") or ""))
    return description, open_, closed


def _stream(w: JiraDcWorld, record_id: str) -> bytes:
    return w.client.stream_record(record_id).content


async def _streams(w: JiraDcWorld) -> dict[str, str]:
    """ticket id -> the words of its stream, for the first tickets the PipesHub test user may open."""
    by_ext = {r["externalRecordId"]: r for r in await w.creator_records()}
    index = w.sim.expected()["ticket_index"]
    ids = [t for t in sorted(index, key=int) if t in by_ext][:15]
    assert ids, "the test user may open no ticket"
    return {tid: stream_words(_stream(w, by_ext[tid]["id"])) for tid in ids}


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimText:

    async def test_tc_jdsim_text_001_description_and_comments(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        missing = []
        for tid, got in (await _streams(w)).items():
            description, comments, _restricted = _issue_text(w, tid)
            # a pasted payload shows the renderer's error text in Jira's HTML; the stream carries the payload (TEXT-002)
            missing += [(tid, b) for b in [*description, *comments] if "unknown macro" not in b and b not in got]
        assert not missing, f"{len(missing)} blocks of Jira's text are not in the streams: {missing[:5]}"

    async def test_tc_jdsim_text_002_json_paste_is_not_an_unknown_macro(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"json_paste_share": 0.6}))
        pasted = 0
        for tid, got in (await _streams(w)).items():
            issue = w.sim.vendor_get(f"/rest/api/2/issue/{tid}", fields="description", expand="renderedFields").json()
            if "Unknown macro" not in str((issue.get("renderedFields") or {}).get("description") or ""):
                continue
            pasted += 1
            payload = re.search(r"\{[^{}]*\}", issue["fields"].get("description") or "")
            assert payload, f"{tid}: the description has no pasted payload"
            assert words(payload.group(0)) in got, f"{tid}: the pasted payload is not in the stream"
            assert "unknown macro" not in got, f"{tid}: the renderer's error text is in the stream"
        assert pasted, "no issue of the site has a pasted payload at this share"

    @pytest.mark.xfail(strict=True, reason="open pull request #6212: restricted comments are records read by their group "
                                           "or role; today they are in the issue's own content")
    async def test_tc_jdsim_text_003_restricted_comment_is_not_in_the_issue(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        checked = 0
        for tid, got in (await _streams(w)).items():
            _d, _open, restricted = _issue_text(w, tid)
            for block in restricted:
                checked += 1
                assert block not in got, f"{tid}: a restricted comment is in the issue's stream: {block[:60]!r}"
        assert checked, "no restricted comment among the issues streamed"
