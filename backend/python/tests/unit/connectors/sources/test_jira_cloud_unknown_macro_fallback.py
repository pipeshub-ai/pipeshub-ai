"""Jira Cloud: fall back to the raw ADF when the rendered HTML lost text to "Unknown macro".

Jira Cloud renders ``renderedFields`` through its wiki renderer. Plain text containing a ``{``
(here, a JSON payload pasted into a description) is read as a macro, and everything from the
``{`` to the matching ``}`` is replaced with ``<span class="error">Unknown macro: {...}</span>``.
The fixture below is the real shape of such an issue: the rendered HTML is what Jira returned
(``Unknown macro`` where the payloads were), and the ADF is the raw description, where the
payloads are ordinary paragraph text separated by hard breaks. Values are illustrative.
"""
import logging

import pytest

from app.connectors.sources.atlassian.core.adf_markdown import adf_to_markdown
from app.connectors.sources.atlassian.core.html_utils import (
    has_unknown_macro_placeholder,
    inline_images_as_markdown,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.models.blocks import BlocksContainer, DataFormat

# renderedFields.description as Jira returned it: both payloads are gone.
RENDERED_WITH_PLACEHOLDERS = """<ul>
	<li>Kafka Event
	<ul>
		<li>Topic : <b>entity-events</b></li>
	</ul>
	</li>
	<li>Body
	<ul>
		<li>eventType: ‘appEnabled’</li>
		<li>apps: <span class="error">&#91;&#39;drive&#39;, ‘gmail’&#93;</span></li>
		<li>syncAction: enum<span class="error">&#91;&#39;immediate&#39;, ‘none’&#93;</span><br/>
*<b>appEnabled</b>:
<div class="error"><span class="error">Unknown macro: {    &quot;eventType&quot;}</span> </div>
<p>,<br/>
*<b>appDisabled</b>: </p>
<div class="error"><span class="error">Unknown macro: {    &quot;eventType&quot;}</span> </div>
<p>,</p></li>
	</ul>
	</li>
</ul>
"""


def _text(value: str, marks: list[dict] | None = None) -> dict:
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = marks
    return node


def _para(*nodes: dict) -> dict:
    return {"type": "paragraph", "content": list(nodes)}


def _item(*nodes: dict) -> dict:
    return {"type": "listItem", "content": list(nodes)}


def _bullets(*items: dict) -> dict:
    return {"type": "bulletList", "content": list(items)}


def _lines(*lines: str | list[dict]) -> list[dict]:
    """Paragraph content: each line is a str or a list of nodes, joined by hardBreak."""
    content = []
    for i, line in enumerate(lines):
        if i:
            content.append({"type": "hardBreak"})
        content.extend([_text(line)] if isinstance(line, str) else line)
    return content


STRONG = [{"type": "strong"}]

# fields.description (ADF) for the same issue: the payloads are plain text.
RAW_DESCRIPTION_ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        _bullets(
            _item(_para(_text("Kafka Event")), _bullets(_item(_para(_text("Topic : "), _text("entity-events", STRONG))))),
            _item(
                _para(_text("Body")),
                _bullets(
                    _item(_para(_text("eventType: ‘appEnabled’"))),
                    _item(_para(_text("apps: ['drive', ‘gmail’]"))),
                    _item(_para(*_lines(
                        "syncAction: enum['immediate', ‘none’]",
                        [_text("appEnabled", STRONG), _text(": {")],
                        '    "eventType": "appEnabledEvent",',
                        '    "payload": {',
                        '      "orgId": "org_example",',
                        '      "appGroupId": "group_example",',
                        '      "syncAction": "immediate"',
                        "    }",
                        "  },",
                        [_text("appDisabled", STRONG), _text(": {")],
                        '    "eventType": "appDisabledEvent",',
                        '    "payload": {',
                        '      "orgId": "org_example",',
                        '      "appGroupId": "group_example"',
                        "    }",
                        "  },",
                    ))),
                ),
            ),
        )
    ],
}


def _make_bare_connector() -> JiraConnector:
    """A JiraConnector with only what ``_parse_issue_to_blocks`` touches — no init/network."""
    connector = JiraConnector.__new__(JiraConnector)
    connector.site_url = "https://example.atlassian.net"
    connector.logger = logging.getLogger("test-jira-unknown-macro")

    async def fake_fetch(attachment: dict, cache: dict) -> str:  # only images ever reach here
        return f"data:{attachment['mimeType']};base64,B64_{attachment['id']}"

    connector._fetch_attachment_as_base64 = fake_fetch
    return connector


async def _parse(
    connector: JiraConnector,
    description_html: str,
    raw_description: object,
    comments_rendered: list[dict] | None = None,
    comments_data: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> BlocksContainer:
    return await connector._parse_issue_to_blocks(
        issue_data={
            "id": "10001",
            "key": "EX-12",
            "fields": {
                "summary": "App group enable/disable events",
                "attachment": attachments or [],
                "description": raw_description,
            },
        },
        issue_key="EX-12",
        weburl="https://example.atlassian.net/browse/EX-12",
        rendered_fields={
            "description": description_html,
            "comment": {"comments": comments_rendered or []},
        },
        comments_data=comments_data or [],
    )


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_description_with_unknown_macro_falls_back_to_raw_adf() -> None:
    result = await _parse(_make_bare_connector(), RENDERED_WITH_PLACEHOLDERS, RAW_DESCRIPTION_ADF)

    desc = result.block_groups[0]
    assert desc.format == DataFormat.MARKDOWN
    assert desc.data.startswith("# [EX-12] App group enable/disable events\n\n")
    assert "Unknown macro" not in desc.data
    # The payload text that the rendered HTML lost is now in the indexed body.
    for fact in ('"appEnabledEvent"', '"appDisabledEvent"', '"orgId": "org_example"',
                 '"appGroupId": "group_example"', '"syncAction": "immediate"'):
        assert fact in desc.data
    # Text the rendered HTML did keep is still there.
    assert "Topic : **entity-events**" in desc.data
    assert "**appDisabled**: {" in desc.data


@pytest.mark.asyncio
async def test_normal_description_is_unchanged() -> None:
    html = "<p>Steps to reproduce:</p><ul><li>open the page</li></ul>"
    raw = {"type": "doc", "version": 1, "content": [_para(_text("Steps to reproduce:"))]}
    result = await _parse(_make_bare_connector(), html, raw)

    desc = result.block_groups[0]
    assert desc.format == DataFormat.HTML
    assert desc.data == f"<h1>[EX-12] App group enable/disable events</h1>\n{html}"


@pytest.mark.asyncio
async def test_unresolved_link_error_span_does_not_trigger_fallback() -> None:
    # Jira wraps an unresolvable [link] in span.error but keeps its text — nothing is lost.
    html = '<p>apps: <span class="error">&#91;Drive, Gmail&#93;</span></p>'
    raw = {"type": "doc", "version": 1, "content": [_para(_text("apps: [Drive, Gmail]"))]}
    result = await _parse(_make_bare_connector(), html, raw)

    assert result.block_groups[0].format == DataFormat.HTML


@pytest.mark.asyncio
async def test_placeholder_without_raw_description_keeps_rendered_html() -> None:
    result = await _parse(_make_bare_connector(), RENDERED_WITH_PLACEHOLDERS, None)

    desc = result.block_groups[0]
    assert desc.format == DataFormat.HTML
    assert "Unknown macro" in desc.data


@pytest.mark.asyncio
async def test_fallback_keeps_inlined_images() -> None:
    html = (
        '<p><span class="image-wrap"><img src="/rest/api/3/attachment/content/700" alt="shot.png" /></span></p>'
        + RENDERED_WITH_PLACEHOLDERS
    )
    attachments = [{"id": "700", "filename": "shot.png", "mimeType": "image/png", "size": 10}]
    result = await _parse(_make_bare_connector(), html, RAW_DESCRIPTION_ADF, attachments=attachments)

    desc = result.block_groups[0]
    assert desc.format == DataFormat.MARKDOWN
    assert "![shot.png](data:image/png;base64,B64_700)" in desc.data
    assert "/rest/api/3/attachment/content/700" not in desc.data


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

COMMENT_META = {"author": {"displayName": "Dev"}, "created": "2026-01-01T00:00:00.000Z"}


@pytest.mark.asyncio
async def test_comment_with_unknown_macro_falls_back_to_raw_adf() -> None:
    rendered = '<p>payload: </p><div class="error"><span class="error">Unknown macro: {  &quot;orgId&quot;}</span> </div>'
    raw_body = {"type": "doc", "version": 1, "content": [_para(*_lines(
        "payload: {", '  "orgId": "org_example",', '  "appGroupId": "group_example"', "}",
    ))]}
    result = await _parse(
        _make_bare_connector(), "<p>Plain description.</p>", RAW_DESCRIPTION_ADF,
        comments_rendered=[{"id": "c1", "body": rendered}],
        comments_data=[{"id": "c1", "body": raw_body, **COMMENT_META}],
    )

    desc, _thread, comment = result.block_groups
    assert desc.format == DataFormat.HTML  # the description itself rendered fine
    assert comment.format == DataFormat.MARKDOWN
    assert '"appGroupId": "group_example"' in comment.data
    assert "Unknown macro" not in comment.data


@pytest.mark.asyncio
async def test_normal_comment_is_unchanged() -> None:
    rendered = "<p>Looks good to me.</p>"
    raw_body = {"type": "doc", "version": 1, "content": [_para(_text("Looks good to me."))]}
    result = await _parse(
        _make_bare_connector(), "", None,
        comments_rendered=[{"id": "c1", "body": rendered}],
        comments_data=[{"id": "c1", "body": raw_body, **COMMENT_META}],
    )

    comment = result.block_groups[2]
    assert comment.format == DataFormat.HTML
    assert comment.data == rendered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHasUnknownMacroPlaceholder:
    def test_detects_real_jira_placeholder(self) -> None:
        assert has_unknown_macro_placeholder(RENDERED_WITH_PLACEHOLDERS)

    def test_detects_localised_label(self) -> None:
        assert has_unknown_macro_placeholder('<span class="error">Unbekanntes Makro: {  &quot;a&quot;}</span>')

    def test_ignores_link_error_and_plain_html(self) -> None:
        assert not has_unknown_macro_placeholder('<span class="error">&#91;Drive&#93;</span>')
        assert not has_unknown_macro_placeholder("<p>Unknown macro: {x} written as plain text</p>")
        assert not has_unknown_macro_placeholder("")


class TestInlineImagesAsMarkdown:
    def test_data_uri_and_public_url_kept_others_skipped(self) -> None:
        html = (
            '<img src="data:image/png;base64,AAA" alt="a [1].png"/>'
            '<img src="https://example.com/x.png"/>'
            '<img src="/rest/api/3/attachment/content/1" alt="private"/>'
            '<img src="https://example.com/has (paren).png"/>'
        )
        assert inline_images_as_markdown(html) == [
            "![a 1.png](data:image/png;base64,AAA)",
            "![image](https://example.com/x.png)",
        ]


class TestAdfToMarkdown:
    def test_code_block_text_is_kept(self) -> None:
        adf = {"type": "doc", "content": [
            {"type": "codeBlock", "attrs": {"language": "json"}, "content": [_text('{"orgId": "x"}')]},
            {"type": "codeBlock", "attrs": {"language": None}, "content": [_text("plain")]},
        ]}
        assert adf_to_markdown(adf) == '```json\n{"orgId": "x"}\n```\n\n```\nplain\n```'

    def test_inline_nodes_keep_their_text(self) -> None:
        adf = {"type": "doc", "content": [_para(
            {"type": "mention", "attrs": {"id": "account-id-not-indexed", "text": "@Alice"}},
            _text(" see "),
            {"type": "inlineCard", "attrs": {"url": "https://example.atlassian.net/browse/EX-1"}},
            _text(" "),
            {"type": "status", "attrs": {"text": "IN PROGRESS"}},
            _text(" "),
            {"type": "emoji", "attrs": {"shortName": ":tada:", "text": "🎉"}},
            _text(" due "),
            {"type": "date", "attrs": {"timestamp": "1767225600000"}},
        )]}
        md = adf_to_markdown(adf)
        assert md == "@Alice see https://example.atlassian.net/browse/EX-1 IN PROGRESS 🎉 due 2026-01-01"
        assert "account-id-not-indexed" not in md

    def test_non_document_returns_empty(self) -> None:
        assert adf_to_markdown(None) == ""
        assert adf_to_markdown("wiki text") == ""
