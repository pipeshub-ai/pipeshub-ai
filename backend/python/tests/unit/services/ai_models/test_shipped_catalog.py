"""The file discovery ships is the generated catalog, not the unit-test fixture."""

from app.services.ai_models.discovery.catalog import load_catalog, lookup_catalog


def test_shipped_catalog_includes_current_models():
    load_catalog.cache_clear()
    catalog = load_catalog()
    assert len(catalog) > 1000
    luna = lookup_catalog("openAI", "gpt-5.6-luna")
    assert luna is not None
    assert luna["max_input_tokens"] >= 1024
    assert luna["supports_vision"] is True
    load_catalog.cache_clear()
