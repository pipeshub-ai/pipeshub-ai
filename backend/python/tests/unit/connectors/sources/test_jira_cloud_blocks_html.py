"""Golden + unit tests for the Jira Cloud HTML block-parsing path.

These cover the ADF->HTML migration: `_parse_issue_to_blocks` now builds BlockGroups from
Jira's *rendered* HTML (renderedFields) rather than converting ADF to markdown, and
`adf_to_plain_text` replaces the old `adf_to_text` for a record's searchable description.

The golden case is issue PT-14 from pipeshub.atlassian.net, whose description exercises
every attachment routing case in one issue:
  - an image inside a table cell         (14062 dk.png)             -> base64-inlined
  - a non-image file linked in a table    (14061 ...pdf)            -> description child
  - an image inside an ordered-list item  (14063 Screenshot.png)   -> base64-inlined
  - a non-image file linked in a list     (14164 html_utils.py)    -> description child
  - two attachments referenced nowhere    (14263 out.json, 14028)  -> standalone children
Plus Jira UI chrome (`rendericon` link icons) that must never surface as attachments.
"""
import logging

import pytest
from bs4 import BeautifulSoup

from app.connectors.sources.atlassian.core.html_utils import simplify_user_mention_links
from app.connectors.sources.atlassian.jira_cloud.connector import (
    JiraConnector,
    adf_to_plain_text,
)
from app.models.blocks import ChildRecord, ChildType, DataFormat

# PT-14 real renderedFields.description (attachment-bearing structure preserved).
# Triple-quoted so the real Jira HTML (mixed single/double quotes) needs no escaping.
PT14_DESCRIPTION_HTML = """<p>Consolidate and lead the work required to improve the Pipeshub Test project workflow.</p>
<p>Objectives:</p>
<ul class="alternate" type="square">
	<li>Drive the end-to-end execution of the workflow improvement initiative</li>
</ul>
<div class='table-wrap'>
<table class='confluenceTable'><tbody>
<tr><th class='confluenceTh'><b>head-1</b></th><th class='confluenceTh'><b>head-2</b></th><th class='confluenceTh'><b>head-3</b></th></tr>
<tr><td class='confluenceTd'>1</td><td class='confluenceTd'>dash</td><td class='confluenceTd'>1000</td></tr>
</tbody></table>
</div>
<div class='table-wrap'>
<table class='confluenceTable'><tbody>
<tr><th class='confluenceTh'>&nbsp;</th><th class='confluenceTh'><b>images</b></th><th class='confluenceTh'>&nbsp;</th></tr>
<tr><td class='confluenceTd'>img-1</td><td class='confluenceTd'>test-pre<br/>
<span class="image-wrap" style=""><img src="https://pipeshub.atlassian.net/rest/api/3/attachment/content/14062" alt="dk.png" width="214" style="border: 0px solid black" /></span><br/>
test-post</td><td class='confluenceTd'>dashboard image</td></tr>
<tr><td class='confluenceTd'>im-2</td><td class='confluenceTd'><span class="nobr"><a href="/rest/api/3/attachment/content/14061" title="Pipeshub_Test_Platform (53a7d884).pdf attached to PT-14" data-attachment-type="file" data-attachment-name="Pipeshub_Test_Platform (53a7d884).pdf" data-media-services-type="file" data-media-services-id="53a7d884-6bbd-4eb3-bdd6-61447a8d7e57" rel="noreferrer">Pipeshub_Test_Platform (53a7d884).pdf<sup><img class="rendericon" src="/images/icons/link_attachment_7.gif" height="7" width="7" align="absmiddle" alt="" border="0"/></sup></a></span><br/>
test-inline</td><td class='confluenceTd'>pdf</td></tr>
</tbody></table>
</div>
<ol>
	<li>list item 1 for testing</li>
	<li>list item 2 for testing</li>
	<li><span class="image-wrap" style=""><img src="https://pipeshub.atlassian.net/rest/api/3/attachment/content/14063" alt="Screenshot 2026-06-20 094938.png" width="490" style="border: 0px solid black" /></span><br/>
list item 3 for testing </li>
	<li>list item 4 for testing</li>
</ol>
<ul>
	<li>bullet-1</li>
	<li>bullet-2</li>
	<li><span class="nobr"><a href="/rest/api/3/attachment/content/14164" title="html_utils.py attached to PT-14" data-attachment-type="file" data-attachment-name="html_utils.py" data-media-services-type="file" data-media-services-id="e9ee292b-5550-4e46-b3c6-20c5a5932c86" rel="noreferrer">html_utils.py<sup><img class="rendericon" src="/images/icons/link_attachment_7.gif" height="7" width="7" align="absmiddle" alt="" border="0"/></sup></a></span></li>
	<li>bullet-3</li>
</ul>
<p>Task List</p>
<ul>
	<li><del>task -1 mark</del></li>
	<li>task-2 mark</li>
</ul>
"""

PT14_ATTACHMENTS = [
    {"id": "14062", "filename": "dk.png", "mimeType": "image/png", "size": 182937},
    {"id": "14164", "filename": "html_utils.py", "mimeType": "text/plain", "size": 2769},
    {"id": "14263", "filename": "out.json", "mimeType": "text/plain", "size": 229415},
    {"id": "14028", "filename": "Pipeshub_Test_Platform.pdf", "mimeType": "application/pdf", "size": 2877},
    {"id": "14061", "filename": "Pipeshub_Test_Platform (53a7d884).pdf", "mimeType": "application/pdf", "size": 2877},
    {"id": "14063", "filename": "Screenshot 2026-06-20 094938.png", "mimeType": "image/png", "size": 106863},
]


def _make_bare_connector() -> JiraConnector:
    """A JiraConnector with only what `_parse_issue_to_blocks` touches — no real init/network."""
    connector = JiraConnector.__new__(JiraConnector)
    connector.site_url = "https://pipeshub.atlassian.net"
    connector.logger = logging.getLogger("test-jira-blocks")

    async def fake_fetch(attachment, cache):  # only images ever reach here
        return f"data:{attachment['mimeType']};base64,B64_{attachment['id']}"

    connector._fetch_attachment_as_base64 = fake_fetch
    return connector


@pytest.mark.asyncio
async def test_pt14_golden_blocks():
    connector = _make_bare_connector()
    children_map = {
        a["id"]: ChildRecord(
            child_type=ChildType.RECORD, child_id=f"rec_{a['id']}", child_name=a["filename"]
        )
        for a in PT14_ATTACHMENTS
    }

    result = await connector._parse_issue_to_blocks(
        issue_data={
            "id": "26525",
            "key": "PT-14",
            "fields": {
                "summary": "Pipeshub Test Platform Improvement Initiative",
                "attachment": PT14_ATTACHMENTS,
            },
        },
        issue_key="PT-14",
        weburl="https://pipeshub.atlassian.net/browse/PT-14",
        rendered_fields={"description": PT14_DESCRIPTION_HTML, "comment": {"comments": []}},
        comments_data=[],
        attachment_children_map=children_map,
    )

    # PT-14 has no comments -> a single description BlockGroup.
    assert len(result.block_groups) == 1
    desc = result.block_groups[0]

    assert desc.format == DataFormat.HTML
    assert desc.data.startswith("<h1>[PT-14] Pipeshub Test Platform Improvement Initiative</h1>")
    assert desc.name == "[PT-14] Pipeshub Test Platform Improvement Initiative"

    # Both images inlined as base64; their attachment URLs are gone from the block.
    assert "data:image/png;base64,B64_14062" in desc.data
    assert "data:image/png;base64,B64_14063" in desc.data
    assert "/rest/api/3/attachment/content/14062" not in desc.data
    assert "/rest/api/3/attachment/content/14063" not in desc.data

    # Non-image file links are preserved in the HTML (they become children, not base64).
    assert "/rest/api/3/attachment/content/14061" in desc.data
    assert "/rest/api/3/attachment/content/14164" in desc.data

    # Jira UI chrome must not leak.
    assert "rendericon" not in desc.data
    assert "link_attachment_7.gif" not in desc.data

    # Children = linked files (14061, 14164) + standalone attachments (14263, 14028).
    # Images (14062, 14063) are inlined, never children.
    assert desc.children_records is not None
    child_ids = {c.child_id for c in desc.children_records}
    assert child_ids == {"rec_14061", "rec_14164", "rec_14263", "rec_14028"}
    assert "rec_14062" not in child_ids
    assert "rec_14063" not in child_ids


@pytest.mark.asyncio
async def test_empty_description_is_just_title():
    connector = _make_bare_connector()
    result = await connector._parse_issue_to_blocks(
        issue_data={"id": "1", "key": "PT-25", "fields": {"summary": "New agent loop", "attachment": []}},
        issue_key="PT-25",
        weburl="https://pipeshub.atlassian.net/browse/PT-25",
        rendered_fields={"description": "", "comment": {"comments": []}},
        comments_data=[],
    )
    assert len(result.block_groups) == 1
    assert result.block_groups[0].data == "<h1>[PT-25] New agent loop</h1>"
    assert result.block_groups[0].format == DataFormat.HTML


@pytest.mark.asyncio
async def test_comment_uses_rendered_html_and_routes_children():
    connector = _make_bare_connector()
    # A non-image file linked only inside a comment -> child of the comment, not the description.
    children_map = {
        "500": ChildRecord(child_type=ChildType.RECORD, child_id="rec_500", child_name="log.txt"),
    }
    comment_html = '<p>see <a href="/rest/api/3/attachment/content/500">log.txt</a></p>'
    result = await connector._parse_issue_to_blocks(
        issue_data={
            "id": "1", "key": "PT-1",
            "fields": {"summary": "T", "attachment": [{"id": "500", "mimeType": "text/plain", "size": 10}]},
        },
        issue_key="PT-1",
        weburl="https://pipeshub.atlassian.net/browse/PT-1",
        rendered_fields={"description": "", "comment": {"comments": [{"id": "c1", "body": comment_html}]}},
        comments_data=[{"id": "c1", "parent": {}, "author": {"displayName": "Dev"}, "created": "2026-01-01T00:00:00.000Z"}],
        attachment_children_map=children_map,
    )
    # description + thread + comment
    assert len(result.block_groups) == 3
    desc, _thread, comment = result.block_groups
    assert desc.children_records is None            # comment file must NOT be a description child
    assert comment.format == DataFormat.HTML
    assert comment.children_records is not None
    assert {c.child_id for c in comment.children_records} == {"rec_500"}


@pytest.mark.asyncio
async def test_weburl_required():
    connector = _make_bare_connector()
    with pytest.raises(ValueError, match="weburl is required"):
        await connector._parse_issue_to_blocks(
            issue_data={"id": "1", "key": "PT-1", "fields": {"summary": "T", "attachment": []}},
            issue_key="PT-1",
            weburl=None,
            rendered_fields={"description": "<p>x</p>"},
            comments_data=[],
        )


def test_adf_to_plain_text():
    adf = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Consolidate and lead the work."}]},
            {"type": "table", "content": [{"type": "tableRow", "content": [
                {"type": "tableCell", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "img-1"}]},
                    {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "u", "alt": "dk.png"}}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "test-post"}]},
                ]},
            ]}]},
        ],
    }
    assert adf_to_plain_text(adf) == "Consolidate and lead the work. img-1 test-post"


def test_adf_to_plain_text_edge_cases():
    assert adf_to_plain_text(None) == ""
    assert adf_to_plain_text("string") == ""
    assert adf_to_plain_text({}) == ""
    assert adf_to_plain_text({"type": "doc", "content": []}) == ""


class TestSimplifyUserMentionLinks:
    def test_view_profile_link_replaced_with_at_name(self):
        html = '<p><a href="https://pipeshub.atlassian.net/secure/ViewProfile.jspa?accountId=712020%3A55194b33">Darshan Godase</a> ok that is good</p>'
        soup = BeautifulSoup(html, "html.parser")
        simplify_user_mention_links(soup)
        assert str(soup) == "<p>@Darshan Godase ok that is good</p>"

    def test_relative_profile_link(self):
        html = '<p><a href="/secure/ViewProfile.jspa?accountId=abc123">Alice</a> check this</p>'
        soup = BeautifulSoup(html, "html.parser")
        simplify_user_mention_links(soup)
        assert "@Alice check this" in str(soup)
        assert "ViewProfile" not in str(soup)

    def test_jira_people_link(self):
        html = '<p><a href="https://example.atlassian.net/jira/people/abc123">Bob</a> please review</p>'
        soup = BeautifulSoup(html, "html.parser")
        simplify_user_mention_links(soup)
        assert "@Bob please review" in str(soup)
        assert "jira/people" not in str(soup)

    def test_non_mention_links_preserved(self):
        html = '<p><a href="https://google.com">Google</a> and <a href="/browse/PT-14">PT-14</a></p>'
        soup = BeautifulSoup(html, "html.parser")
        simplify_user_mention_links(soup)
        result = str(soup)
        assert "https://google.com" in result
        assert "/browse/PT-14" in result

    def test_multiple_mentions(self):
        html = (
            '<p><a href="/secure/ViewProfile.jspa?accountId=a1">Alice</a> and '
            '<a href="/secure/ViewProfile.jspa?accountId=b2">Bob</a> check this</p>'
        )
        soup = BeautifulSoup(html, "html.parser")
        simplify_user_mention_links(soup)
        result = str(soup)
        assert "@Alice" in result
        assert "@Bob" in result
        assert "ViewProfile" not in result

    def test_empty_name_skipped(self):
        html = '<p><a href="/secure/ViewProfile.jspa?accountId=x"></a> text</p>'
        soup = BeautifulSoup(html, "html.parser")
        simplify_user_mention_links(soup)
        assert "ViewProfile" in str(soup)

    def test_no_links_is_noop(self):
        html = "<p>plain text without links</p>"
        soup = BeautifulSoup(html, "html.parser")
        simplify_user_mention_links(soup)
        assert str(soup) == html


@pytest.mark.asyncio
async def test_comment_mention_links_cleaned():
    """User @mentions in comment HTML should become @Name, not full profile links."""
    connector = _make_bare_connector()
    mention_html = (
        '<p><a href="https://pipeshub.atlassian.net/secure/ViewProfile.jspa'
        '?accountId=712020%3A55194b33-3140-4d90-9e52-5fe272669d3b">'
        'Darshan Godase</a> ok that is good as you attached json file</p>'
    )
    result = await connector._parse_issue_to_blocks(
        issue_data={
            "id": "1", "key": "PT-1",
            "fields": {"summary": "T", "attachment": []},
        },
        issue_key="PT-1",
        weburl="https://pipeshub.atlassian.net/browse/PT-1",
        rendered_fields={"description": "", "comment": {"comments": [{"id": "c1", "body": mention_html}]}},
        comments_data=[{"id": "c1", "author": {"displayName": "Dev"}, "created": "2026-01-01T00:00:00.000Z"}],
    )
    comment_bg = result.block_groups[2]
    assert "@Darshan Godase" in comment_bg.data
    assert "ViewProfile" not in comment_bg.data
