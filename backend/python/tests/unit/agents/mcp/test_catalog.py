"""Unit tests for app.agents.mcp.catalog."""


class TestMCPCatalog:
    """Tests for BUILTIN_CATALOG and catalog helpers."""

    def test_builtin_catalog_not_empty(self):
        from app.agents.mcp.catalog import BUILTIN_CATALOG

        assert len(BUILTIN_CATALOG) > 0

    def test_builtin_catalog_has_github(self):
        from app.agents.mcp.catalog import BUILTIN_CATALOG

        tpl = BUILTIN_CATALOG["github"]
        assert tpl.display_name == "GitHub"

    def test_list_templates_returns_all(self):
        from app.agents.mcp.catalog import BUILTIN_CATALOG, list_templates

        all_tpls = list_templates()
        assert isinstance(all_tpls, list)
        assert len(all_tpls) == len(BUILTIN_CATALOG)

    def test_get_template_found(self):
        from app.agents.mcp.catalog import get_template
        from app.agents.mcp.models import MCPServerTemplate

        tpl = get_template("github")
        assert tpl is not None
        assert isinstance(tpl, MCPServerTemplate)
        assert tpl.type_id == "github"

    def test_get_template_not_found(self):
        from app.agents.mcp.catalog import get_template

        assert get_template("nonexistent") is None

    def test_search_templates_by_name(self):
        from app.agents.mcp.catalog import search_templates

        results = search_templates("git")
        type_ids = {t.type_id for t in results}
        assert "github" in type_ids

    def test_search_templates_by_tag(self):
        from app.agents.mcp.catalog import search_templates

        results = search_templates("database")
        type_ids = {t.type_id for t in results}
        assert "postgres" in type_ids

    def test_search_templates_no_match(self):
        from app.agents.mcp.catalog import search_templates

        assert search_templates("zzz_nonexistent") == []

    def test_all_templates_have_required_fields(self):
        from app.agents.mcp.catalog import BUILTIN_CATALOG

        for tpl in BUILTIN_CATALOG.values():
            assert tpl.type_id
            assert tpl.display_name
            assert tpl.description
