"""Unit tests for ``app.utils.url_resolver`` — the ``lookup_record`` URL resolver
registry and ``normalize_weburl`` fallback cleaner.
"""
import pytest

from app.utils.url_resolver import (
    CanonicalRef,
    normalize_weburl,
    resolve_canonical_ref,
)


class TestResolveCanonicalRefJira:
    def test_browse_url_extracts_issue_key(self) -> None:
        ref = resolve_canonical_ref("https://pipeshub.atlassian.net/browse/PA-1787")
        assert ref == CanonicalRef(kind="issue_key", value="PA-1787", connector_family="JIRA")

    def test_browse_url_with_tracking_params_and_fragment_same_result(self) -> None:
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/browse/PA-1787?atlOrigin=abc#comment-1"
        )
        assert ref == CanonicalRef(kind="issue_key", value="PA-1787", connector_family="JIRA")

    def test_lowercase_issue_key_is_uppercased(self) -> None:
        ref = resolve_canonical_ref("https://pipeshub.atlassian.net/browse/pa-1787")
        assert ref.value == "PA-1787"

    def test_board_url_selected_issue_query_param(self) -> None:
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/jira/software/projects/PA/boards/1?selectedIssue=PA-1787"
        )
        assert ref == CanonicalRef(kind="issue_key", value="PA-1787", connector_family="JIRA")


class TestResolveCanonicalRefConfluence:
    def test_space_page_url_extracts_content_id_ignoring_slug(self) -> None:
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/wiki/spaces/SD/pages/450625553/Agent+Loop+Implementation"
        )
        assert ref == CanonicalRef(kind="external_id", value="450625553", connector_family="CONFLUENCE")

    def test_renamed_slug_still_resolves_to_same_content_id(self) -> None:
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/wiki/spaces/SD/pages/450625553/A+Totally+Different+Title"
        )
        assert ref.value == "450625553"

    def test_legacy_viewpage_action_url(self) -> None:
        ref = resolve_canonical_ref(
            "https://pipeshub.atlassian.net/wiki/pages/viewpage.action?pageId=450625553"
        )
        assert ref == CanonicalRef(kind="external_id", value="450625553", connector_family="CONFLUENCE")

    def test_short_link_is_not_decodable_returns_none(self) -> None:
        ref = resolve_canonical_ref("https://pipeshub.atlassian.net/wiki/x/AbCdEf")
        assert ref is None


class TestResolveCanonicalRefDrive:
    @pytest.mark.parametrize("path_kind", ["file", "document", "spreadsheets", "presentation"])
    def test_drive_docs_urls_extract_file_id(self, path_kind: str) -> None:
        ref = resolve_canonical_ref(
            f"https://docs.google.com/{path_kind}/d/1AbCdEfGhIjKlMnOp/edit?usp=sharing"
        )
        assert ref == CanonicalRef(kind="external_id", value="1AbCdEfGhIjKlMnOp", connector_family="DRIVE")

    def test_open_id_query_param_form(self) -> None:
        ref = resolve_canonical_ref("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp")
        assert ref == CanonicalRef(kind="external_id", value="1AbCdEfGhIjKlMnOp", connector_family="DRIVE")


class TestResolveCanonicalRefSlack:
    def test_permalink_extracts_channel_and_ts(self) -> None:
        ref = resolve_canonical_ref("https://acme.slack.com/archives/C0123/p1720000000000100")
        assert ref.kind == "slack_ts"
        assert ref.connector_family == "SLACK"
        assert ref.extra["channel_id"] == "C0123"
        assert ref.extra["ts"] == "1720000000.000100"

    def test_permalink_with_thread_ts_prefers_thread_ts_as_value(self) -> None:
        ref = resolve_canonical_ref(
            "https://acme.slack.com/archives/C0123/p1720000000000100?thread_ts=1719999999.000000"
        )
        assert ref.value == "1719999999.000000"


class TestResolveCanonicalRefLinear:
    def test_linear_issue_url_extracts_key(self) -> None:
        ref = resolve_canonical_ref("https://linear.app/acme/issue/ENG-42/fix-the-thing")
        assert ref == CanonicalRef(kind="issue_key", value="ENG-42", connector_family="LINEAR")


class TestResolveCanonicalRefNotion:
    def test_trailing_hex32_slug_extracts_dashed_page_id(self) -> None:
        ref = resolve_canonical_ref(
            "https://www.notion.so/Some-Page-Title-1234567890abcdef1234567890abcdef"
        )
        assert ref is not None
        assert ref.kind == "external_id"
        assert ref.connector_family == "NOTION"
        assert ref.value == "12345678-90ab-cdef-1234-567890abcdef"


class TestResolveCanonicalRefMiscAndFallback:
    def test_unrecognized_host_returns_none(self) -> None:
        assert resolve_canonical_ref("https://example.com/some/random/path") is None

    def test_malformed_url_returns_none(self) -> None:
        assert resolve_canonical_ref("not a url at all") is None

    def test_significant_query_param_hosts_are_not_extractor_matched(self) -> None:
        """ServiceNow/SharePoint have no dedicated extractor — they rely on
        normalize_weburl preserving their significant params instead."""
        assert resolve_canonical_ref("https://acme.service-now.com/nav_to.do?sys_id=abc123") is None


class TestNormalizeWeburl:
    def test_strips_fragment(self) -> None:
        assert normalize_weburl("https://example.com/page#section") == "https://example.com/page"

    def test_strips_tracking_params(self) -> None:
        result = normalize_weburl("https://example.com/page?utm_source=x&utm_campaign=y")
        assert result == "https://example.com/page"

    def test_strips_trailing_slash(self) -> None:
        assert normalize_weburl("https://example.com/page/") == "https://example.com/page"

    def test_does_not_strip_root_slash(self) -> None:
        assert normalize_weburl("https://example.com/") == "https://example.com/"

    def test_lowercases_scheme_and_host(self) -> None:
        assert normalize_weburl("HTTPS://Example.COM/Page") == "https://example.com/Page"

    def test_removes_default_https_port(self) -> None:
        assert normalize_weburl("https://example.com:443/page") == "https://example.com/page"

    def test_keeps_non_default_port(self) -> None:
        assert normalize_weburl("https://example.com:8443/page") == "https://example.com:8443/page"

    def test_preserves_servicenow_sys_id(self) -> None:
        result = normalize_weburl("https://acme.service-now.com/nav_to.do?sys_id=abc123&utm_source=x")
        assert "sys_id=abc123" in result
        assert "utm_source" not in result

    def test_preserves_sharepoint_significant_params(self) -> None:
        result = normalize_weburl(
            "https://acme.sharepoint.com/sites/team/doc.aspx?id=%2Fsites%2Fteam%2Ffile.docx&gclid=xyz"
        )
        assert "id=" in result
        assert "gclid" not in result

    def test_confluence_plus_in_slug_untouched_by_normalization(self) -> None:
        """normalize_weburl is only the fallback path — canonical extraction (content ID)
        already handles renamed slugs; this just verifies it doesn't mangle '+' chars."""
        url = "https://pipeshub.atlassian.net/wiki/spaces/SD/pages/450625553/Agent+Loop+Implementation"
        result = normalize_weburl(url)
        assert "Agent+Loop+Implementation" in result

    def test_same_url_with_and_without_tracking_normalizes_identically(self) -> None:
        base = "https://example.com/page"
        tracked = "https://example.com/page?utm_source=newsletter&ref=email#comment-1"
        assert normalize_weburl(base) == normalize_weburl(tracked)

    def test_empty_string_returns_empty(self) -> None:
        assert normalize_weburl("") == ""

    def test_non_url_string_does_not_raise(self) -> None:
        """normalize_weburl is only ever called on strings pre-validated as
        ``^https?://`` by lookup_record.py; for anything else it degrades
        gracefully (no exception) rather than guaranteeing a no-op."""
        normalize_weburl("not a url")  # must not raise
