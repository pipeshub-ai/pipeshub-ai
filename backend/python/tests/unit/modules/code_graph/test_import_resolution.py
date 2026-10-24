import pytest

from app.modules.code_graph.import_resolution import resolve_import_specifier


@pytest.mark.parametrize(
    ("specifier", "files", "expected"),
    [
        ("./foo.js", {"src/foo.ts"}, "src/foo.ts"),
        ("./foo.js", {"src/foo.tsx"}, "src/foo.tsx"),
        ("./foo.mjs", {"src/foo.mts"}, "src/foo.mts"),
        ("./foo.js", {"src/foo.js", "src/foo.ts"}, "src/foo.js"),
    ],
    ids=["js names ts", "js names tsx", "mjs names mts", "a real js file wins"],
)
def test_esm_specifiers_resolve_to_their_typescript_source(
    specifier: str, files: set[str], expected: str
) -> None:
    assert resolve_import_specifier(specifier, "src/main.ts", "js", files) == expected
