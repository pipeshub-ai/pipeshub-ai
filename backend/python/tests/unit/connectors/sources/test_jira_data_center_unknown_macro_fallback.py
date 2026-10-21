"""Jira Data Center: fall back to the raw wiki markup when the rendered HTML lost text to "Unknown macro".

Jira renders ``renderedFields`` through its wiki renderer. Plain text containing a ``{`` (here, a
JSON payload pasted into a description) is read as a macro, and everything from the ``{`` to the
matching ``}`` is replaced with ``<span class="error">Unknown macro: {...}</span>``.

Unlike Jira Cloud (API v3, ADF), Data Center's REST v2 returns ``fields.description`` and a
comment's ``body`` as wiki-markup strings. That raw text still holds the payload, so it is what the
connector indexes when the placeholder shows up. Values are illustrative.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.models.blocks import BlocksContainer, DataFormat

SITE = "https://jira.example.com"

# renderedFields.description as the wiki renderer returns it: both payloads are gone.
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
<b>appEnabled</b>:
<div class="error"><span class="error">Unknown macro: {    &quot;eventType&quot;}</span> </div>
<p>,<br/>
<b>appDisabled</b>: </p>
<div class="error"><span class="error">Unknown macro: {    &quot;eventType&quot;}</span> </div>
<p>,</p></li>
	</ul>
	</li>
</ul>
"""

# fields.description (wiki markup) for the same issue: the payloads are plain text.
RAW_DESCRIPTION_WIKI = """* Kafka Event
** Topic : *entity-events*
* Body
** eventType: ‘appEnabled’
** apps: ['drive', ‘gmail’]
** syncAction: enum['immediate', ‘none’]
*appEnabled*: {
    "eventType": "appEnabledEvent",
    "payload": {
      "orgId": "org_example",
      "appGroupId": "group_example",
      "syncAction": "immediate"
    }
  },
*appDisabled*: {
    "eventType": "appDisabledEvent",
    "payload": {
      "orgId": "org_example",
      "appGroupId": "group_example"
    }
  },
"""

PAYLOAD_FACTS = (
    '"appEnabledEvent"',
    '"appDisabledEvent"',
    '"orgId": "org_example"',
    '"appGroupId": "group_example"',
    '"syncAction": "immediate"',
)


def _make_connector() -> JiraDataCenterConnector:
    logger = logging.getLogger("test-jira-dc-unknown-macro")
    logger.setLevel(logging.CRITICAL)
    dep = MagicMock()
    dep.org_id = "org-dc-macro"
    cs = MagicMock()
    cs.get_config = AsyncMock()
    connector = JiraDataCenterConnector(logger, dep, MagicMock(), cs, "conn-dc-macro", "team", "u1")
    connector.site_url = SITE

    async def fake_fetch(issue_id: str, attachment_id: str, filename: str) -> str:
        return f"data:image/png;base64,B64_{attachment_id}"

    connector._fetch_media_as_base64 = fake_fetch
    return connector


async def _parse(
    connector: JiraDataCenterConnector,
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
            "comments": comments_data or [],
        },
        issue_key="EX-12",
        weburl=f"{SITE}/browse/EX-12",
        rendered_fields={
            "description": description_html,
            "comment": {"comments": comments_rendered or []},
        },
    )


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_description_with_unknown_macro_falls_back_to_raw_wiki() -> None:
    result = await _parse(_make_connector(), RENDERED_WITH_PLACEHOLDERS, RAW_DESCRIPTION_WIKI)

    desc = result.block_groups[0]
    assert desc.format == DataFormat.MARKDOWN
    assert desc.data.startswith("# [EX-12] App group enable/disable events\n\n")
    assert "Unknown macro" not in desc.data
    # The payload text that the rendered HTML lost is now in the indexed body.
    for fact in PAYLOAD_FACTS:
        assert fact in desc.data
    # Text the rendered HTML did keep is still there, as the raw wiki markup has it.
    assert "Topic : *entity-events*" in desc.data
    assert "apps: ['drive', ‘gmail’]" in desc.data


def test_rendered_description_really_lost_the_payload() -> None:
    # Guards the fixture: without the fallback, none of the payload would be indexed.
    for fact in PAYLOAD_FACTS:
        assert fact not in RENDERED_WITH_PLACEHOLDERS.replace("&quot;", '"')


@pytest.mark.asyncio
async def test_normal_description_is_unchanged() -> None:
    html = "<p>Steps to reproduce:</p><ul><li>open the page</li></ul>"
    result = await _parse(_make_connector(), html, "Steps to reproduce:\n* open the page")

    desc = result.block_groups[0]
    assert desc.format == DataFormat.HTML
    assert desc.data == f"<h1>[EX-12] App group enable/disable events</h1>\n{html}"


@pytest.mark.asyncio
async def test_unresolved_link_error_span_does_not_trigger_fallback() -> None:
    # The wiki renderer wraps an unresolvable [link] in span.error but keeps its text.
    html = '<p>apps: <span class="error">&#91;Drive, Gmail&#93;</span></p>'
    result = await _parse(_make_connector(), html, "apps: [Drive, Gmail]")

    desc = result.block_groups[0]
    assert desc.format == DataFormat.HTML
    # The connector re-serialises the HTML (entities decoded); the link text is still there.
    assert '<span class="error">[Drive, Gmail]</span>' in desc.data


@pytest.mark.asyncio
async def test_placeholder_without_raw_description_keeps_rendered_html() -> None:
    for raw in (None, "", "   "):
        result = await _parse(_make_connector(), RENDERED_WITH_PLACEHOLDERS, raw)

        desc = result.block_groups[0]
        assert desc.format == DataFormat.HTML
        assert desc.data.startswith("<h1>[EX-12] App group enable/disable events</h1>")
        assert "Unknown macro" in desc.data


@pytest.mark.asyncio
async def test_fallback_keeps_inlined_images_and_names_of_the_rest() -> None:
    html = (
        '<p><span class="image-wrap"><img src="http://jira.example.com/secure/attachment/700/shot.png" /></span></p>'
        '<p><span class="image-wrap"><img src="http://jira.example.com/secure/attachment/701/trace.png" /></span></p>'
        + RENDERED_WITH_PLACEHOLDERS
    )
    raw = "!shot.png!\n!trace.png!\n" + RAW_DESCRIPTION_WIKI
    attachments = [
        {"id": "700", "filename": "shot.png", "mimeType": "image/png", "size": 10},
        {"id": "701", "filename": "trace.png", "mimeType": "image/png", "size": 10},
    ]
    connector = _make_connector()

    async def fetch_only_700(issue_id: str, attachment_id: str, filename: str) -> str | None:
        return "data:image/png;base64,B64_700" if attachment_id == "700" else None

    connector._fetch_media_as_base64 = fetch_only_700
    result = await _parse(connector, html, raw, attachments=attachments)

    desc = result.block_groups[0]
    assert desc.format == DataFormat.MARKDOWN
    # The inlined image is carried over as a markdown image.
    assert "![Image_1](data:image/png;base64,B64_700)" in desc.data
    # The one that could not be inlined keeps its name (from the wiki text) but not its
    # private attachment URL, which needs the connector's credentials.
    assert "!trace.png!" in desc.data
    assert "/secure/attachment/" not in desc.data


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

COMMENT_META = {"author": {"displayName": "Dev"}, "created": "2026-01-01T00:00:00.000+0000"}
COMMENT_RENDERED = (
    '<p>payload: </p><div class="error"><span class="error">Unknown macro: {  &quot;orgId&quot;}</span> </div>'
)
COMMENT_RAW = 'payload: {\n  "orgId": "org_example",\n  "appGroupId": "group_example"\n}'


@pytest.mark.asyncio
async def test_comment_with_unknown_macro_falls_back_to_raw_wiki() -> None:
    # Comments embedded in the issue: raw body in fields.comment, HTML in renderedFields.comment.
    result = await _parse(
        _make_connector(), "<p>Plain description.</p>", "Plain description.",
        comments_rendered=[{"id": "c1", "body": COMMENT_RENDERED}],
        comments_data=[{"id": "c1", "body": COMMENT_RAW, **COMMENT_META}],
    )

    desc, _thread, comment = result.block_groups
    assert desc.format == DataFormat.HTML  # the description itself rendered fine
    assert comment.format == DataFormat.MARKDOWN
    assert comment.data == COMMENT_RAW
    assert '"appGroupId": "group_example"' in comment.data


@pytest.mark.asyncio
async def test_paginated_comment_with_unknown_macro_falls_back_to_raw_wiki() -> None:
    # Comments paged in from /comment?expand=renderedBody carry both body and renderedBody.
    result = await _parse(
        _make_connector(), "", None,
        comments_data=[{"id": "c9", "body": COMMENT_RAW, "renderedBody": COMMENT_RENDERED, **COMMENT_META}],
    )

    comment = result.block_groups[2]
    assert comment.format == DataFormat.MARKDOWN
    assert '"orgId": "org_example"' in comment.data
    assert "Unknown macro" not in comment.data


@pytest.mark.asyncio
async def test_normal_comment_is_unchanged() -> None:
    rendered = "<p>Looks good to me.</p>"
    result = await _parse(
        _make_connector(), "", None,
        comments_rendered=[{"id": "c1", "body": rendered}],
        comments_data=[{"id": "c1", "body": "Looks good to me.", **COMMENT_META}],
    )

    comment = result.block_groups[2]
    assert comment.format == DataFormat.HTML
    assert comment.data == rendered


@pytest.mark.asyncio
async def test_link_error_in_comment_does_not_trigger_fallback() -> None:
    rendered = '<p>see <span class="error">&#91;Drive&#93;</span></p>'
    result = await _parse(
        _make_connector(), "", None,
        comments_rendered=[{"id": "c1", "body": rendered}],
        comments_data=[{"id": "c1", "body": "see [Drive]", **COMMENT_META}],
    )

    comment = result.block_groups[2]
    assert comment.format == DataFormat.HTML
    assert comment.data == '<p>see <span class="error">[Drive]</span></p>'


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


class TestMarkdownFromRawWiki:
    def test_non_string_or_empty_returns_none(self) -> None:
        connector = _make_connector()
        assert connector._markdown_from_raw_wiki(None, "") is None
        assert connector._markdown_from_raw_wiki({"type": "doc"}, "") is None
        assert connector._markdown_from_raw_wiki("  \n ", "") is None

    def test_keeps_external_images_and_drops_private_attachment_urls(self) -> None:
        connector = _make_connector()
        html = (
            '<img src="data:image/png;base64,AAA" alt="Image_1"/>'
            '<img src="https://cdn.example.com/x.png"/>'
            '<img src="https://jira.example.com/secure/thumbnail/9/x.png"/>'
        )
        assert connector._markdown_from_raw_wiki("text {", html) == (
            "text {\n\n![Image_1](data:image/png;base64,AAA)\n\n![image](https://cdn.example.com/x.png)"
        )
