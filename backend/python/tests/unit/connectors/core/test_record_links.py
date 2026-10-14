"""The forms a pasted web address can take, and the record addresses they name."""

import pytest

from app.config.constants.arangodb import RecordRelations
from app.connectors.core.base.data_processor.record_links import (
    MAX_ISSUE_KEYS,
    MAX_LINKED_URLS,
    addresses_linking_to,
    canonical_address,
    link_target,
    mentions_in,
    related_for_urls,
)
from app.models.entities import RecordType

PR = "https://github.com/acme/api/pull/3081"
JIRA = "https://acme.atlassian.net/browse/PA-1718"
PAGE = "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345/Release+plan"


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


class TestLinkTarget:
    @pytest.mark.parametrize("pasted", [
        PR,
        PR + "/",
        PR + "/files",
        PR + "/files#diff-abc",
        PR + "#issuecomment-123",
        PR + "?w=1",
        "<" + PR + ">",
        PR + ").",
    ])
    def test_pull_request_forms_name_the_pull_request(self, pasted) -> None:
        target = link_target(pasted.strip("<>"))
        assert target.url == PR
        assert target.candidates[0] == PR
        assert target.record_type is RecordType.PULL_REQUEST

    def test_issue_address_also_looks_up_the_pull_request_form(self) -> None:
        # GitHub serves a pull request at /issues/N too.
        target = link_target("https://github.com/acme/api/issues/3081")
        assert target.candidates == (
            "https://github.com/acme/api/issues/3081",
            "https://github.com/acme/api/pull/3081",
        )
        assert target.record_type is RecordType.TICKET

    @pytest.mark.parametrize("pasted", [
        JIRA,
        JIRA + "?focusedCommentId=10001",
        JIRA + "?atlOrigin=eyJpIjoi",
        JIRA + "/",
        JIRA + "#comment",
    ])
    def test_jira_forms_name_the_issue(self, pasted) -> None:
        target = link_target(pasted)
        assert target.url == JIRA
        assert target.candidates == (JIRA,)
        assert target.record_type is RecordType.TICKET

    def test_jira_server_under_a_path_keeps_the_path(self) -> None:
        target = link_target("https://jira.corp.example/jira/browse/OPS-9?x=1")
        assert target.url == "https://jira.corp.example/jira/browse/OPS-9"

    def test_confluence_page_is_looked_up_with_and_without_its_title(self) -> None:
        target = link_target(PAGE + "?focusedCommentId=1")
        assert target.url == "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345"
        assert target.candidates == (PAGE, target.url)
        assert target.record_type is RecordType.CONFLUENCE_PAGE

    def test_other_address_drops_fragment_and_tracking(self) -> None:
        target = link_target("https://docs.example.com/guide?utm_source=slack#install")
        assert target.url == "https://docs.example.com/guide"
        assert "https://docs.example.com/guide?utm_source=slack" in target.candidates
        assert "https://docs.example.com/guide/" in target.candidates

    def test_credentials_in_the_address_are_dropped(self) -> None:
        target = link_target("https://user:secret@github.com/acme/api/pull/1")
        assert "secret" not in target.url
        assert all("secret" not in c for c in target.candidates)

    @pytest.mark.parametrize("value", ["", "not a url", "mailto:a@b.com", "ftp://x.example/f"])
    def test_not_a_web_address(self, value) -> None:
        assert link_target(value) is None


class TestGitHubShapeOnlyOnGitHub:
    def test_another_sites_issues_path_is_an_ordinary_address(self) -> None:
        target = link_target("https://docs.example.com/api/v1/issues/42/details")
        assert target.url == "https://docs.example.com/api/v1/issues/42/details"
        assert target.record_type is RecordType.WEBPAGE

    def test_enterprise_server_named_for_github(self) -> None:
        target = link_target("https://github.corp.example/acme/api/pull/7/files")
        assert target.url == "https://github.corp.example/acme/api/pull/7"


class TestAddressesLinkingTo:
    @pytest.mark.parametrize("pasted", [
        JIRA + "?focusedCommentId=10001",
        JIRA + "?atlOrigin=eyJpIjoi",
        JIRA + "#comment",
    ])
    def test_a_link_record_for_any_paste_of_the_issue_is_found(self, pasted: str) -> None:
        # A link record stores the canonical form of its paste.
        assert canonical_address(pasted) in addresses_linking_to(JIRA)

    @pytest.mark.parametrize("pasted", [PR + "/files#diff-1", PR + "?w=1", "https://github.com/acme/api/issues/3081"])
    def test_a_link_record_for_any_paste_of_the_pull_request_is_found(self, pasted: str) -> None:
        assert canonical_address(pasted) in addresses_linking_to(PR)

    def test_page_retitled_since_the_paste(self) -> None:
        pasted = "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345/Old+title"
        assert canonical_address(pasted) in addresses_linking_to(PAGE)


    def test_pull_request_includes_its_tabs(self) -> None:
        forms = addresses_linking_to(PR)
        assert PR in forms
        assert PR + "/" in forms
        assert PR + "/files" in forms

    def test_page_includes_the_stored_title_form_and_the_bare_page(self) -> None:
        forms = addresses_linking_to(PAGE)
        assert PAGE in forms
        assert "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345" in forms

    def test_no_address(self) -> None:
        assert addresses_linking_to(None) == ()
        assert addresses_linking_to("") == ()


class TestRelatedForUrls:
    def test_one_entry_per_address_named_by_address(self) -> None:
        related = related_for_urls([PR, PR + "/files", JIRA, "not a url"])
        assert [r.external_record_id for r in related] == [PR, JIRA]
        assert related[0].weburl == PR
        assert related[0].relation_type is RecordRelations.LINKED_TO

    def test_capped(self) -> None:
        urls = [f"https://github.com/acme/api/pull/{n}" for n in range(MAX_LINKED_URLS + 10)]
        assert len(related_for_urls(urls)) == MAX_LINKED_URLS


class TestMentionsIn:
    def test_pull_request_title_and_markdown_body(self) -> None:
        related = mentions_in(
            "PA-1844: fix the export",
            "Fixes [PA-1718](https://acme.atlassian.net/browse/PA-1718).\n"
            "Design: https://acme.atlassian.net/wiki/spaces/ENG/pages/12345/Plan\n"
            "Also touches PA-1844 and OPS-7.",
        )
        assert [r.weburl for r in related if r.weburl] == [
            "https://acme.atlassian.net/browse/PA-1718",
            "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345/Plan",
        ]
        # The link text "PA-1718" is a key too; both resolve to the same issue.
        assert [r.issue_key for r in related if r.issue_key] == ["PA-1844", "PA-1718", "OPS-7"]

    def test_key_inside_an_address_is_not_read_again(self) -> None:
        related = mentions_in("see https://acme.atlassian.net/browse/PA-1718")
        assert [r.issue_key for r in related if r.issue_key] == []

    def test_jira_description_document(self) -> None:
        adf = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Blocked by OPS-12, see "},
                {"type": "text", "text": "the PR", "marks": [
                    {"type": "link", "attrs": {"href": "https://github.com/acme/api/pull/3081"}},
                ]},
                {"type": "inlineCard", "attrs": {"url": "https://github.com/acme/api/pull/3100"}},
            ]},
        ]}
        related = mentions_in("Ship the export", adf)
        assert [r.weburl for r in related if r.weburl] == [
            "https://github.com/acme/api/pull/3081",
            "https://github.com/acme/api/pull/3100",
        ]
        assert [r.issue_key for r in related if r.issue_key] == ["OPS-12"]

    @pytest.mark.parametrize("text", ["utf-8", "UTF8", "A-1", "x-PA-1", "PA-0", "PA-1x"])
    def test_not_issue_keys(self, text: str) -> None:
        assert [r.issue_key for r in mentions_in(text) if r.issue_key] == []

    def test_nothing_in_empty_or_missing_bodies(self) -> None:
        assert mentions_in(None, "", {}, []) == []

    def test_keys_are_capped(self) -> None:
        text = " ".join(f"PA-{n}" for n in range(1, MAX_ISSUE_KEYS + 20))
        assert len(mentions_in(text)) == MAX_ISSUE_KEYS

