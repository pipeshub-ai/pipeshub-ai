"""Per-language handler modules plug into the engine's registries without editing it."""
import pytest

from app.modules.parsers.code_parser import engine, languages


def test_the_engine_registries_include_every_language_module_handler() -> None:
    for registered, merged in ((languages.IMPORT_HANDLERS, engine._IMPORT_HANDLERS),
                               (languages.HERITAGE_HANDLERS, engine._HERITAGE_HANDLERS),
                               (languages.TYPE_TABLE_HANDLERS, engine._TYPE_TABLE_HANDLERS)):
        assert all(merged[name] is handler for name, handler in registered.items())


def test_a_language_module_cannot_replace_a_core_handler() -> None:
    core = {"python": len, "js": len}
    with pytest.raises(ValueError, match=r"reuses core handler names: \['python'\]"):
        engine._with_languages(core, {"python": str, "csharp": str}, "IMPORT_HANDLERS")
    assert engine._with_languages(core, {"csharp": str}, "IMPORT_HANDLERS") == {"python": len, "js": len, "csharp": str}
    assert engine._with_languages(core, {}, "IMPORT_HANDLERS") == core


def test_the_core_handlers_are_still_registered() -> None:
    assert {"python", "js", "java", "go"} <= set(engine._IMPORT_HANDLERS)
    assert {"python", "js", "ts", "java"} <= set(engine._HERITAGE_HANDLERS)
    assert {"python", "js", "ts", "java", "go"} <= set(engine._TYPE_TABLE_HANDLERS)
