"""Tests for app.modules.parsers.code_parser.lang_config."""

import pytest

from app.modules.parsers.code_parser.lang_config import (
    COMMENT_NODE_TYPES,
    LANGUAGES,
    SUPPORTED_CODE_EXTENSIONS,
    LanguageConfig,
    config_for_extension,
    config_for_language,
    detect_language,
)


class TestLanguageConfig:
    def test_all_expected_languages_present(self):
        expected = {
            "python", "javascript", "typescript", "tsx",
            "c", "cpp", "csharp", "java", "kotlin", "scala", "groovy",
            "go", "rust", "ruby", "php", "swift", "dart", "lua",
        }
        assert expected == set(LANGUAGES.keys())

    def test_each_language_has_at_least_one_extension(self):
        for name, cfg in LANGUAGES.items():
            assert len(cfg.extensions) > 0, f"{name} has no extensions"

    def test_no_extension_overlap(self):
        seen: dict[str, str] = {}
        for name, cfg in LANGUAGES.items():
            for ext in cfg.extensions:
                assert ext not in seen, (
                    f"Extension {ext!r} claimed by both {seen[ext]!r} and {name!r}"
                )
                seen[ext] = name

    def test_python_config(self):
        py = LANGUAGES["python"]
        assert "py" in py.extensions
        assert "pyi" in py.extensions
        assert py.ts_module == "tree_sitter_python"
        assert py.docstring_style == "python"

    def test_javascript_config(self):
        js = LANGUAGES["javascript"]
        assert "js" in js.extensions
        assert "jsx" in js.extensions
        assert "mjs" in js.extensions

    def test_typescript_vs_tsx_extensions_disjoint(self):
        ts_exts = LANGUAGES["typescript"].extensions
        tsx_exts = LANGUAGES["tsx"].extensions
        assert ts_exts.isdisjoint(tsx_exts)


class TestCommentNodeTypes:
    def test_contains_standard_types(self):
        assert "comment" in COMMENT_NODE_TYPES
        assert "line_comment" in COMMENT_NODE_TYPES
        assert "block_comment" in COMMENT_NODE_TYPES
        assert "doc_comment" in COMMENT_NODE_TYPES

    def test_is_frozenset(self):
        assert isinstance(COMMENT_NODE_TYPES, frozenset)


class TestSupportedCodeExtensions:
    def test_contains_common_extensions(self):
        common = {"py", "js", "ts", "tsx", "java", "go", "rs", "rb", "c", "cpp"}
        assert common.issubset(SUPPORTED_CODE_EXTENSIONS)

    def test_is_frozenset(self):
        assert isinstance(SUPPORTED_CODE_EXTENSIONS, frozenset)


class TestConfigForExtension:
    def test_python(self):
        cfg = config_for_extension("py")
        assert cfg is not None
        assert cfg.name == "python"

    def test_with_dot_prefix(self):
        cfg = config_for_extension(".py")
        assert cfg is not None
        assert cfg.name == "python"

    def test_case_insensitive(self):
        cfg = config_for_extension("PY")
        assert cfg is not None
        assert cfg.name == "python"

    def test_unknown_extension(self):
        assert config_for_extension("xyz123") is None

    def test_empty_string(self):
        assert config_for_extension("") is None

    def test_none(self):
        assert config_for_extension(None) is None

    @pytest.mark.parametrize("ext,expected_lang", [
        ("py", "python"),
        ("js", "javascript"),
        ("ts", "typescript"),
        ("tsx", "tsx"),
        ("java", "java"),
        ("go", "go"),
        ("rs", "rust"),
        ("rb", "ruby"),
        ("php", "php"),
        ("swift", "swift"),
        ("dart", "dart"),
        ("lua", "lua"),
        ("c", "c"),
        ("cpp", "cpp"),
        ("cs", "csharp"),
        ("kt", "kotlin"),
        ("scala", "scala"),
        ("groovy", "groovy"),
    ])
    def test_all_languages_reachable(self, ext, expected_lang):
        cfg = config_for_extension(ext)
        assert cfg is not None
        assert cfg.name == expected_lang


class TestConfigForLanguage:
    def test_known(self):
        cfg = config_for_language("python")
        assert cfg is not None
        assert cfg.name == "python"

    def test_unknown(self):
        assert config_for_language("brainfuck") is None

    def test_empty(self):
        assert config_for_language("") is None

    def test_none(self):
        assert config_for_language(None) is None


class TestDetectLanguage:
    def test_python_file(self):
        assert detect_language("main.py") == "python"

    def test_typescript_file(self):
        assert detect_language("app.ts") == "typescript"

    def test_nested_path(self):
        assert detect_language("src/components/app.tsx") == "tsx"

    def test_no_extension(self):
        assert detect_language("Makefile") is None

    def test_unknown_extension(self):
        assert detect_language("data.csv") is None

    def test_empty_string(self):
        assert detect_language("") is None

    def test_none(self):
        assert detect_language(None) is None

    def test_dot_only(self):
        assert detect_language(".") is None

    def test_hidden_file_with_known_ext(self):
        assert detect_language(".hidden.py") == "python"
