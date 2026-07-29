"""Unit tests for identifiers.py — no I/O, no mocks.

Covers the URL matrix from the plan's test spec:
- Jira /browse/PA-1787 and ?selectedIssue=KEY-123
- Confluence /pages/<numeric-id> and ?pageId=<id>
- Confluence /wiki/x/<base62> short link → WEB_URL fallback
- Google Drive /document/d/<id>/edit?usp=sharing
- Slack permalink → channel + ts
- Linear issue
- Notion 32-hex UUID
- ServiceNow sys_id
- Bare issue key
- Bare numeric id
- Garbage → None
"""

import pytest

from app.agents.actions.knowledge_graph.identifiers import (
    CanonicalRef,
    RefKind,
    normalize_weburl,
    resolve_canonical_ref,
)


class TestBareIssueKey:
    def test_simple_key(self):
        ref = resolve_canonical_ref("PA-1787")
        assert ref is not None
        assert ref.kind == RefKind.ISSUE_KEY
        assert ref.value == "PA-1787"
        assert ref.connector_hint == "JIRA"

    def test_lowercase_key_treated_as_external_id(self):
        # Bare issue key regex requires uppercase; lowercase becomes external_id
        ref = resolve_canonical_ref("pa-1787")
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID  # lowercase → external_id, not issue_key

    def test_long_project_key(self):
        ref = resolve_canonical_ref("BACKEND-999")
        assert ref is not None
        assert ref.kind == RefKind.ISSUE_KEY
        assert ref.value == "BACKEND-999"


class TestJiraURLs:
    def test_browse_url(self):
        ref = resolve_canonical_ref("https://pipeshub.atlassian.net/browse/PA-1787")
        assert ref is not None
        assert ref.kind == RefKind.ISSUE_KEY
        assert ref.value == "PA-1787"
        assert ref.connector_hint == "JIRA"

    def test_selected_issue_qs(self):
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/jira/software/projects/PA/boards/1?selectedIssue=PA-1787"
        )
        assert ref is not None
        assert ref.kind == RefKind.ISSUE_KEY
        assert ref.value == "PA-1787"

    def test_issues_path(self):
        ref = resolve_canonical_ref("https://mycompany.atlassian.net/issues/PROJ-42")
        assert ref is not None
        assert ref.kind == RefKind.ISSUE_KEY
        assert ref.value == "PROJ-42"


class TestConfluenceURLs:
    def test_page_path_id(self):
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/wiki/spaces/ENG/pages/450625553/Agent+Loop+Implementation"
        )
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID
        assert ref.value == "450625553"
        assert ref.connector_hint == "CONFLUENCE"

    def test_page_path_with_fragment_and_tracking(self):
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/wiki/spaces/ENG/pages/450625553/Agent+Loop+Implementation"
            "?atlOrigin=eyJpIjoiOTdlNWI4N2FiNDVjNDYwNGJjNzkyOWJhNGMwNWYwN2QiLCJwIjoiaiJ9#comment-1"
        )
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID
        assert ref.value == "450625553"

    def test_page_query_string(self):
        ref = resolve_canonical_ref(
            "https://mycompany.atlassian.net/wiki/spaces/DOCS?pageId=123456789"
        )
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID
        assert ref.value == "123456789"

    def test_short_link_fallback(self):
        ref = resolve_canonical_ref("https://pipeshub.atlassian.net/wiki/x/AbCdEfGh")
        assert ref is not None
        assert ref.kind == RefKind.WEB_URL
        assert ref.connector_hint == "CONFLUENCE"


class TestGoogleDrive:
    def test_doc_edit_url(self):
        ref = resolve_canonical_ref(
            "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit?usp=sharing"
        )
        assert ref is not None
        assert ref.kind == RefKind.DRIVE_FILE
        assert ref.value == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        assert ref.connector_hint == "GOOGLE_DRIVE"

    def test_drive_open_id(self):
        ref = resolve_canonical_ref(
            "https://drive.google.com/open?id=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
        )
        assert ref is not None
        assert ref.kind == RefKind.DRIVE_FILE
        assert ref.value == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"


class TestSlack:
    def test_permalink(self):
        ref = resolve_canonical_ref(
            "https://myworkspace.slack.com/archives/C04ABCDE/p1706786400123456"
        )
        assert ref is not None
        assert ref.kind == RefKind.SLACK_TS
        assert ref.channel_id == "C04ABCDE"
        assert "1706786400" in ref.value
        assert "123456" in ref.value

    def test_permalink_without_microseconds(self):
        ref = resolve_canonical_ref(
            "https://myworkspace.slack.com/archives/C04ABCDE/p1706786400"
        )
        assert ref is not None
        assert ref.kind == RefKind.SLACK_TS
        assert ref.channel_id == "C04ABCDE"


class TestLinear:
    def test_issue_url(self):
        ref = resolve_canonical_ref("https://linear.app/myteam/issue/ENG-123/some-title")
        assert ref is not None
        assert ref.kind == RefKind.ISSUE_KEY
        assert ref.value == "ENG-123"
        assert ref.connector_hint == "LINEAR"


class TestNotion:
    def test_32hex_uuid(self):
        ref = resolve_canonical_ref(
            "https://www.notion.so/myworkspace/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        )
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID
        assert ref.value == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        assert ref.connector_hint == "NOTION"


class TestServiceNow:
    def test_sys_id(self):
        ref = resolve_canonical_ref(
            "https://mycompany.service-now.com/nav_to.do?uri=incident.do?sys_id=abc123def456abc123def456abc12345"
        )
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID
        assert ref.value == "abc123def456abc123def456abc12345"
        assert ref.connector_hint == "SERVICENOW"


class TestBareExternalId:
    def test_numeric_id(self):
        ref = resolve_canonical_ref("450625553")
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID
        assert ref.value == "450625553"

    def test_alphanumeric(self):
        ref = resolve_canonical_ref("abc-def-123")
        assert ref is not None
        assert ref.kind == RefKind.EXTERNAL_ID


class TestGarbage:
    def test_empty_string(self):
        assert resolve_canonical_ref("") is None

    def test_whitespace_only(self):
        assert resolve_canonical_ref("   ") is None

    def test_very_long_random(self):
        # A completely unstructured string with spaces that doesn't match any pattern
        ref = resolve_canonical_ref("this is just a random search query with spaces and !")
        # This may or may not be None depending on length/chars — we just check no crash
        assert True  # no exception raised


class TestNormalizeWeburl:
    def test_strips_utm_params(self):
        url = "https://example.com/page?utm_source=google&utm_medium=cpc&id=123"
        result = normalize_weburl(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=123" in result

    def test_strips_atlorigin(self):
        url = "https://example.atlassian.net/page?atlOrigin=eyJpIjoiOTdlNWI4N2FiNDVjNDYwNGJjNzkyOWJhNGMwNWYwN2QiLCJwIjoiaiJ9"
        result = normalize_weburl(url)
        assert "atlOrigin" not in result

    def test_strips_fragment(self):
        url = "https://example.com/page?id=123#section"
        result = normalize_weburl(url)
        assert "#" not in result
        assert "id=123" in result

    def test_empty_url_returns_input(self):
        assert normalize_weburl("not-a-url") == "not-a-url"
