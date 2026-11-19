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


def test_a_family_without_a_branch_goes_to_its_registered_resolver(monkeypatch) -> None:
    from app.modules.code_graph import resolvers

    files = {"src/a.rs", "src/b.rs"}
    assert resolve_import_specifier("crate::b", "src/a.rs", "rust", files) is None   # no resolver: nothing
    seen = []
    monkeypatch.setitem(resolvers.RESOLVERS, "rust",
                        lambda spec, src, paths, index: seen.append((spec, src, paths, index)) or "src/b.rs")
    assert resolve_import_specifier("crate::b", "src/a.rs", "rust", files) == "src/b.rs"
    assert seen == [("crate::b", "src/a.rs", files, None)]   # outside a build there is no index
    # a family with its own branch never reaches the registry
    monkeypatch.setitem(resolvers.RESOLVERS, "py", lambda *a: "src/b.rs")
    assert resolve_import_specifier("os", "src/a.py", "py", {"src/a.py"}) is None


def test_a_registered_resolver_gets_the_build_index_and_the_built_in_branches_ignore_it(monkeypatch) -> None:
    from app.modules.code_graph import resolvers
    from app.modules.code_graph.import_resolution import ImportResolution

    index = object()
    files = {"pkg/__init__.py", "pkg/mod.py", "src/a.ts", "src/b.ts", "src/x.rs"}
    with_index, without = ImportResolution(files, index=index), ImportResolution(files)
    for spec, src, family in (("pkg.mod", "pkg/__init__.py", "py"), ("./b", "src/a.ts", "js")):
        assert with_index.resolve(spec, src, family) == without.resolve(spec, src, family) is not None
    got = []
    monkeypatch.setitem(resolvers.RESOLVERS, "rust", lambda spec, src, paths, idx: got.append(idx))
    with_index.resolve("crate::x", "src/x.rs", "rust")
    assert got == [index]
