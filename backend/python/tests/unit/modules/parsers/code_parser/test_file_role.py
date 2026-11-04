"""File selection and role classification.

The cascade is pure path logic, so it is cheap to test exhaustively -- and
expensive to get wrong silently, since a misclassification only shows up as a
subtly worse graph.
"""
import pytest

from app.modules.parsers.code_parser.file_role import (
    FileRole,
    classify_file_role,
    is_generated_content,
    is_generated_file_name,
    is_ignored_path,
    should_index_code_file,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        # generated wins over every other rule
        ("tests/fixtures/generated/foo_pb2.py", FileRole.GENERATED),
        ("src/api/service.pb.go", FileRole.GENERATED),
        ("lib/model.g.dart", FileRole.GENERATED),
        ("src/schema.generated.ts", FileRole.GENERATED),
        # test
        ("tests/test_client.py", FileRole.TEST),
        ("src/__tests__/widget.js", FileRole.TEST),
        ("src/widget.test.ts", FileRole.TEST),
        ("app/service_test.go", FileRole.TEST),
        ("tests/conftest.py", FileRole.TEST),
        # migration
        ("db/migrations/0001_init.py", FileRole.MIGRATION),
        ("alembic/versions/abc_add_col.py", FileRole.MIGRATION),
        ("sql/V2__add_index.sql", FileRole.MIGRATION),
        # type definitions
        ("types/index.d.ts", FileRole.TYPE_DEFINITION),
        ("stubs/client.pyi", FileRole.TYPE_DEFINITION),
        # build beats config for files that match both
        ("build.gradle", FileRole.BUILD),
        ("Dockerfile", FileRole.BUILD),
        ("Makefile", FileRole.BUILD),
        (".github/workflows/ci.yml", FileRole.BUILD),
        # config
        ("pyproject.toml", FileRole.CONFIG),
        ("tsconfig.json", FileRole.CONFIG),
        ("webpack.config.js", FileRole.CONFIG),
        ("infra/main.tf", FileRole.CONFIG),
        # script
        ("scripts/deploy.sh", FileRole.SCRIPT),
        ("tools/release.ps1", FileRole.SCRIPT),
        # default
        ("src/api/client.py", FileRole.SOURCE),
        ("src/components/Widget.tsx", FileRole.SOURCE),
    ],
)
def test_role_cascade(path, expected):
    assert classify_file_role(path) is expected


@pytest.mark.parametrize(
    "path",
    [
        # Substring matching would call all of these tests.
        "contest/runner.py",
        "latest/index.ts",
        "src/protest.py",
        "src/greatest_hits.py",
        # A plain "workflows" dir is source, not build — only .github/workflows is CI.
        "src/workflows/engine.py",
        "backend/workflows/pipeline.ts",
    ],
)
def test_segment_matching_not_substring(path):
    assert classify_file_role(path) is FileRole.SOURCE


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/left-pad/index.js",
        "frontend/node_modules/react/index.js",
        "backend/__pycache__/mod.cpython-312.pyc",
        "dist/bundle.js",
        "target/classes/App.class",
        "vendor/github.com/pkg/errors/errors.go",
        "static/app.min.js",
        "package-lock.json",
        "uv.lock",
        "services/gateway-go/go.sum",
        "web/bun.lockb",
    ],
)
def test_ignored_paths(path):
    assert is_ignored_path(path) is True


@pytest.mark.parametrize("path", ["src/client.py", "app/main.ts", "lib/util.js"])
def test_not_ignored(path):
    assert is_ignored_path(path) is False


@pytest.mark.parametrize(
    "path, ignored",
    [
        # Output names inside a source root are packages, not build output (P-7).
        ("services/gateway-go/internal/build/build.go", False),
        ("services/payments-java/src/main/java/com/acme/build/Builder.java", False),
        ("src/out/render.ts", False),
        ("pkg/target/select.go", False),
        # Elsewhere they are still output, at any depth.
        ("build/app.js", True),
        ("packages/web/dist/index.js", True),
        ("services/payments-java/target/classes/App.java", True),
        ("frontend/build/static/main.js", True),
        ("env/lib/python3.12/os.py", True),
        # Unambiguous dependency trees are skipped even inside a source root.
        ("src/node_modules/x/index.js", True),
    ],
)
def test_output_dir_names_inside_source_roots(path: str, ignored: bool) -> None:
    assert is_ignored_path(path) is ignored


def test_should_index_rejects_ignored_and_generated():
    assert should_index_code_file("node_modules/x/index.js")[0] is False
    assert should_index_code_file("api/service.pb.go")[0] is False

    ok, role = should_index_code_file("src/client.py")
    assert ok is True
    assert role is FileRole.SOURCE


def test_a_directory_named_build_is_ignored_not_merely_labelled():
    # `build/` is dependency output; the whole tree is skipped rather than
    # indexed with role=build.
    assert is_ignored_path("build/generated/app.js") is True


@pytest.mark.parametrize(
    "name",
    ["package-lock.json", "yarn.lock", "Cargo.lock", "app.min.js", "site.min.css",
     "bundle.js.map", "Button.test.tsx.snap", "web/static/app.min.js"],
)
def test_generated_file_names(name: str) -> None:
    assert is_generated_file_name(name) is True


@pytest.mark.parametrize(
    "name",
    # The directory rules of is_ignored_path are deliberately not applied.
    ["bin/deploy.sh", "vendor/lib.py", "build/app.js", "package.json", "locksmith.py", "minimal.js"],
)
def test_names_that_are_not_generated(name: str) -> None:
    assert is_generated_file_name(name) is False


# -- generated content (P-4) ---------------------------------------------------

_BUNDLE = b"!function(w){" + b"function a(t){return t}r.k=a;" * 400 + b"}(window);"


@pytest.mark.parametrize(
    "content",
    [
        _BUNDLE,
        b"// Code generated by protoc-gen-go. DO NOT EDIT.\npackage pb\n",
        b"# Generated by the protocol buffer compiler.  DO NOT EDIT!\n",
        b"/* eslint-disable */\n// This file was automatically generated by graphql-codegen. DO NOT EDIT.\n",
        b"/**\n * @generated SignedSource<<abc>>\n */\n",
        b"// <auto-generated>\n//     This code was generated by a tool.\n",
    ],
)
def test_generated_content(content: bytes) -> None:
    assert is_generated_content(content) is True


@pytest.mark.parametrize(
    "content",
    [
        b"def main():\n    return 1\n" * 2000,
        # A short file with one long line costs nothing to parse.
        b"x = '" + b"a" * 4000 + b"'\n",
        # The phrase deep in a file is a mention, not a header.
        b"x = 1\n" * 400 + b"# Code generated files are DO NOT EDIT in our docs\n",
        b"# DO NOT EDIT this list by hand without telling ops\n",
        b"def generated_ids():\n    return []\n",
    ],
)
def test_content_that_is_not_generated(content: bytes) -> None:
    assert is_generated_content(content) is False
