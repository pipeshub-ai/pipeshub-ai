"""Catalog search by what a server does, run against the real catalog entries."""
import pytest

from app.agents.mcp.catalog import CATEGORY_LABELS, rank_templates, search_templates
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPCategory,
    MCPServerTemplate,
    MCPTransport,
)
from app.agents.mcp.registry import MCPRegistry


@pytest.fixture(scope="module")
def catalog() -> list[MCPServerTemplate]:
    registry = MCPRegistry()
    registry.auto_discover_templates()
    return registry.list_templates()


def _ids(matches) -> list[str]:
    return [m.template.type_id for m in matches]


class TestEveryEntryCanBeFound:
    def test_every_entry_in_use_has_a_category_and_what_it_does(self, catalog: list[MCPServerTemplate]) -> None:
        for t in catalog:
            if t.replaced_by:
                continue
            assert t.category is not None, f"{t.type_id} has no category"
            assert len(t.capabilities) >= 2, f"{t.type_id} says too little about what it does"

    def test_capabilities_read_as_short_jobs(self, catalog: list[MCPServerTemplate]) -> None:
        for t in catalog:
            for cap in t.capabilities:
                assert cap[:1].isupper() and not cap.endswith("."), f"{t.type_id}: {cap!r}"
                assert len(cap) <= 60, f"{t.type_id}: {cap!r}"

    def test_every_category_has_a_label(self) -> None:
        assert set(CATEGORY_LABELS) == set(MCPCategory)


class TestRanking:
    @pytest.mark.parametrize(
        ("query", "first", "why"),
        [
            ("schedule meetings", "google_calendar", "Schedule meetings"),
            ("support tickets", "intercom", "Manage support tickets"),
            ("sign a contract", "docusign", "Send a document for signature"),
            ("review pull requests", "github", "Review pull requests"),
        ],
    )
    def test_a_job_finds_the_server_that_does_it_and_says_why(
        self, catalog: list[MCPServerTemplate], query: str, first: str, why: str,
    ) -> None:
        top = rank_templates(catalog, query)[0]
        assert (top.template.type_id, top.capability) == (first, why)

    def test_a_name_outranks_a_job(self, catalog: list[MCPServerTemplate]) -> None:
        matches = rank_templates(catalog, "linear")
        assert _ids(matches)[0] == "linear"
        assert matches[0].capability is None

    def test_a_category_name_finds_its_servers(self, catalog: list[MCPServerTemplate]) -> None:
        found = set(_ids(rank_templates(catalog, "customer support")))
        assert "intercom" in found

    def test_words_match_at_their_start_only(self, catalog: list[MCPServerTemplate]) -> None:
        # "sign" is inside "assign" (ClickUp's "Create and assign tasks").
        assert "clickup" not in _ids(rank_templates(catalog, "sign a contract"))

    def test_a_synonym_counts_for_less_than_the_word(self, catalog: list[MCPServerTemplate]) -> None:
        ids = _ids(rank_templates(catalog, "schedule meetings"))
        assert ids.index("google_calendar") < ids.index("fathom")

    def test_filler_words_and_noise_find_nothing(self, catalog: list[MCPServerTemplate]) -> None:
        assert rank_templates(catalog, "the mcp server for me") == []
        assert rank_templates(catalog, "xylophone") == []

    def test_no_search_keeps_the_registry_order(self, catalog: list[MCPServerTemplate]) -> None:
        assert [m.template for m in rank_templates(catalog, "  ")] == catalog

    def test_a_category_narrows_with_or_without_a_search(self, catalog: list[MCPServerTemplate]) -> None:
        meetings = rank_templates(catalog, None, MCPCategory.MEETINGS.value)
        assert meetings and all(m.template.category is MCPCategory.MEETINGS for m in meetings)
        assert _ids(rank_templates(catalog, "issues", MCPCategory.DEVELOPER.value))
        assert all(
            m.template.category is MCPCategory.DEVELOPER
            for m in rank_templates(catalog, "issues", MCPCategory.DEVELOPER.value)
        )

    def test_search_templates_keeps_the_ranked_order(self, catalog: list[MCPServerTemplate]) -> None:
        assert [t.type_id for t in search_templates(catalog, "schedule meetings")] == _ids(
            rank_templates(catalog, "schedule meetings")
        )

    def test_an_entry_without_the_new_fields_is_still_found_by_name_and_description(self) -> None:
        old = MCPServerTemplate(
            type_id="acme", display_name="Acme", description="Tracks warehouse stock levels",
            transport=MCPTransport.STREAMABLE_HTTP, default_auth_mode=MCPAuthMode.NONE,
        )
        assert _ids(rank_templates([old], "acme")) == ["acme"]
        assert _ids(rank_templates([old], "warehouse stock")) == ["acme"]
        assert rank_templates([old], None, MCPCategory.DATA.value) == []
