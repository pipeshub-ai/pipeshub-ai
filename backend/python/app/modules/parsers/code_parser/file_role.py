"""Path-based file selection and role classification for code files.

Pure functions -- no I/O -- so the whole cascade is table-testable. Called from
the GitLab connector while building a blob record, where only the git tree
listing is available (path + name, no content). A file rejected here is never
fetched, never queued, never streamed.

Ignore list ported from graphify ``detect.py``, whose rationale for the build
and cache directories is that they are "generated, never architecturally
meaningful".
"""
from __future__ import annotations

import re
from enum import Enum

__all__ = [
    "FileRole",
    "SKIPPED_ROLES",
    "classify_file_role",
    "is_ignored_path",
    "should_index_code_file",
]


class FileRole(str, Enum):
    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    BUILD = "build"
    MIGRATION = "migration"
    SCRIPT = "script"
    TYPE_DEFINITION = "type_definition"
    GENERATED = "generated"


# Roles whose files carry no architectural signal worth parsing, embedding or
# storing. Generated code is a machine restatement of a schema that is itself
# indexed.
SKIPPED_ROLES = frozenset({FileRole.GENERATED})

_IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "bower_components", "jspm_packages",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "venv", ".venv", "env", ".env.d", "site-packages",
    "dist", "build", "target", "out", "bin", "obj",
    ".next", ".nuxt", ".svelte-kit", ".parcel-cache", ".turbo",
    "vendor", "third_party", "thirdparty",
    "coverage", "htmlcov", ".nyc_output",
    ".idea", ".vscode", ".gradle",
})

_IGNORED_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".snap")

_IGNORED_FILENAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock", "uv.lock",
})

# --- role signals -----------------------------------------------------------

_GENERATED_DIRS = frozenset({"generated", "__generated__", "gen", ".generated"})
_GENERATED_SUFFIXES = (
    ".pb.go", ".pb.cc", ".pb.h", "_pb2.py", "_pb2_grpc.py", ".pb.ts",
    ".g.dart", ".freezed.dart", ".g.cs", ".designer.cs",
)
_GENERATED_RE = re.compile(r"\.generated\.[A-Za-z0-9]+$")

_TEST_DIRS = frozenset({
    "test", "tests", "spec", "specs", "__tests__", "__mocks__",
    "testing", "e2e", "integration-tests", "integration_tests",
})
_TEST_FILENAMES = frozenset({"conftest.py", "setup.cfg.test"})
_TEST_PATTERNS = (
    re.compile(r"^test_.+\.py$"),
    re.compile(r".+_test\.(py|go|rb|ts|js)$"),
    re.compile(r".+\.(test|spec)\.(ts|tsx|js|jsx|mts|cts|mjs|cjs)$"),
    re.compile(r".+Tests?\.(java|cs|kt)$"),
    re.compile(r".+_spec\.rb$"),
)

_MIGRATION_DIRS = frozenset({"migrations", "migrate", "versions"})
_MIGRATION_PATTERNS = (
    re.compile(r"^V\d+__.+\.sql$"),           # Flyway
    re.compile(r"^\d{8,}_.+\.(rb|py|js|ts|sql)$"),  # Rails / Django-ish timestamps
)

_TYPE_DEF_SUFFIXES = (".d.ts", ".d.mts", ".d.cts", ".pyi")

_BUILD_FILENAMES = frozenset({
    "Makefile", "makefile", "GNUmakefile", "CMakeLists.txt", "Dockerfile",
    "BUILD", "BUILD.bazel", "WORKSPACE", "WORKSPACE.bazel",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "Rakefile", "SConstruct", "meson.build",
})
_BUILD_SUFFIXES = (".mk", ".bazel", ".bzl", ".csproj", ".vbproj", ".fsproj", ".sln")

_CONFIG_FILENAMES = frozenset({
    "package.json", "tsconfig.json", "jsconfig.json", "pyproject.toml",
    "setup.py", "setup.cfg", "Cargo.toml", "go.mod", "go.sum", "pom.xml",
    "Pipfile", "requirements.txt", "environment.yml", "manifest.json",
})
_CONFIG_SUFFIXES = (".toml", ".ini", ".cfg", ".properties", ".tf", ".tfvars", ".hcl")
_CONFIG_PATTERNS = (
    re.compile(r".+\.config\.(js|ts|mjs|cjs|json)$"),
    re.compile(r"^\.?eslintrc.*$"),
    re.compile(r"^\.?prettierrc.*$"),
    re.compile(r"^\.?babelrc.*$"),
    re.compile(r"^(webpack|vite|rollup|jest|vitest|karma|tailwind|postcss)\..+$"),
    re.compile(r"^docker-compose.*\.(yml|yaml)$"),
)

_SCRIPT_SUFFIXES = (".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd")
_SCRIPT_DIRS = frozenset({"scripts", "bin", "tools"})


def _segments(file_path: str) -> list[str]:
    """Split a path into whole segments.

    Substring matching would classify ``contest/``, ``latest/`` and
    ``protest.py`` as tests, which is why every directory check below compares
    complete segments.
    """
    return [seg for seg in (file_path or "").replace("\\", "/").split("/") if seg]


def _basename(file_path: str) -> str:
    segs = _segments(file_path)
    return segs[-1] if segs else ""


def _is_ci_workflow(segs: list[str]) -> bool:
    """True when the path is under a CI provider's workflows directory."""
    for i, seg in enumerate(segs[:-1]):
        if seg == "workflows" and i > 0 and segs[i - 1] in (".github", ".gitlab"):
            return True
    return False


def is_ignored_path(file_path: str) -> bool:
    """True when the file lives in a dependency, build-output or cache tree."""
    segs = _segments(file_path)
    if not segs:
        return False
    if any(seg in _IGNORED_DIRS for seg in segs[:-1]):
        return True
    name = segs[-1]
    if name in _IGNORED_FILENAMES:
        return True
    return name.endswith(_IGNORED_SUFFIXES)


def classify_file_role(file_path: str, file_name: str | None = None) -> FileRole:
    """Classify *file_path* into a role.

    An ordered cascade: the first matching rule wins. Order is the design --
    files legitimately match several rules, and
    ``tests/fixtures/generated/foo_pb2.py`` must land on exactly one.
    """
    name = file_name or _basename(file_path)
    segs = _segments(file_path)
    dirs = set(segs[:-1]) if segs else set()

    if dirs & _GENERATED_DIRS or name.endswith(_GENERATED_SUFFIXES) or _GENERATED_RE.search(name):
        return FileRole.GENERATED

    if dirs & _TEST_DIRS or name in _TEST_FILENAMES or any(p.match(name) for p in _TEST_PATTERNS):
        return FileRole.TEST

    if dirs & _MIGRATION_DIRS or any(p.match(name) for p in _MIGRATION_PATTERNS):
        return FileRole.MIGRATION

    if name.endswith(_TYPE_DEF_SUFFIXES) or "@types" in dirs:
        return FileRole.TYPE_DEFINITION

    # build before config: build.gradle and Dockerfile match both, and the build
    # reading is the more specific one.
    if name in _BUILD_FILENAMES or name.endswith(_BUILD_SUFFIXES) or _is_ci_workflow(segs):
        return FileRole.BUILD

    if (
        name in _CONFIG_FILENAMES
        or name.endswith(_CONFIG_SUFFIXES)
        or any(p.match(name) for p in _CONFIG_PATTERNS)
        or "config" in dirs
    ):
        return FileRole.CONFIG

    if name.endswith(_SCRIPT_SUFFIXES) or dirs & _SCRIPT_DIRS:
        return FileRole.SCRIPT

    return FileRole.SOURCE


def should_index_code_file(file_path: str, file_name: str | None = None) -> tuple[bool, FileRole]:
    """Return ``(should_index, role)`` for a repository blob."""
    role = classify_file_role(file_path, file_name)
    if is_ignored_path(file_path) or role in SKIPPED_ROLES:
        return False, role
    return True, role
