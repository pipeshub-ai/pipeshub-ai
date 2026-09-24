"""File selection and role classification.

The cascade is pure path logic, so it is cheap to test exhaustively -- and
expensive to get wrong silently, since a misclassification only shows up as a
subtly worse graph.
"""
import pytest

from app.modules.parsers.code_parser.file_role import (
    FileRole,
    classify_file_role,
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
    ],
)
def test_ignored_paths(path):
    assert is_ignored_path(path) is True


@pytest.mark.parametrize("path", ["src/client.py", "app/main.ts", "lib/util.js"])
def test_not_ignored(path):
    assert is_ignored_path(path) is False


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
