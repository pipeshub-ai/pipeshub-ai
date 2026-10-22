"""A grammar wheel that is not installed skips the file instead of failing it."""
from dataclasses import replace

from app.modules.parsers.code_parser import engine


def test_missing_grammar_module_yields_a_skipped_parse(monkeypatch) -> None:
    cfg = replace(engine.LANGUAGES["python"], ts_module="tree_sitter_not_installed_anywhere")
    monkeypatch.setitem(engine.LANGUAGES, "python", cfg)
    monkeypatch.setattr(engine, "_PARSER_CACHE", {})

    parsed = engine.parse_code(b"x = 1\n", "python")

    assert parsed.skipped_reason == "grammar_unavailable"
    assert parsed.language == "python"
    assert parsed.symbols == []
