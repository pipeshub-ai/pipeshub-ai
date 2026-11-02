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
    "is_generated_content",
    "is_generated_file_name",
    "is_ignored_path",
    "is_sensitive_path",
    "should_index_code_file",
    "should_index_code_folder",
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
    "venv", ".venv", ".env.d", "site-packages",
    ".next", ".nuxt", ".svelte-kit", ".parcel-cache", ".turbo",
    "vendor", "third_party", "thirdparty",
    "coverage", "htmlcov", ".nyc_output",
    ".idea", ".vscode", ".gradle",
})

# Build-output names that are also ordinary package names (Go
# `internal/build`, Java `com/acme/build`). One counts as output at the top of
# the repo; deeper, only with no source root above it and only for what a
# build emits (bundles, objects, classes), so `packages/web/dist/app.js` is
# skipped while `com/acme/build/Builder.java` and `pkg/env/settings.py` are
# indexed (P-7). `bin/` holds scripts as often as binaries, and `env/` is a
# virtualenv only at the top, so neither is output deeper down on name alone.
_OUTPUT_DIRS = frozenset({"dist", "build", "target", "out", "bin", "obj", "env"})
_TOP_LEVEL_ONLY_OUTPUT = frozenset({"bin", "env"})
# Also everyday package names, so deeper down only built artifacts in them are output.
_PACKAGE_LIKE_OUTPUT = frozenset({"build", "out"})
_BUILD_ARTIFACT_SUFFIXES = (
    ".js", ".mjs", ".cjs", ".css", ".map", ".html", ".class", ".jar", ".war",
    ".o", ".obj", ".a", ".so", ".dll", ".pyc", ".d.ts", ".wasm",
)
_SOURCE_ROOTS = frozenset({
    "src", "source", "sources", "internal", "pkg", "cmd", "app", "lib", "main",
    "java", "kotlin", "scala",
})

_IGNORED_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".snap")

_IGNORED_FILENAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock", "uv.lock",
    "go.sum", "bun.lockb", "npm-shrinkwrap.json", "packages.lock.json",
})

# Credential and key material: never fetched, so never embedded or sent to an
# LLM. `.env.example`-style templates stay, being documentation.
_SENSITIVE_FILENAMES = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc", ".pgpass", ".htpasswd", ".git-credentials",
    "credentials", "credentials.json", "credentials.yml", "credentials.yaml", "credentials.xml",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "terraform.tfstate", "terraform.tfstate.backup",
})
_SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk",
    ".tfvars", ".tfstate", ".tfstate.backup",
)
_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults")

# --- role signals -----------------------------------------------------------

_GENERATED_DIRS = frozenset({"generated", "__generated__", "gen", ".generated"})
_GENERATED_SUFFIXES = (
    ".pb.go", ".pb.cc", ".pb.h", "_pb2.py", "_pb2_grpc.py", ".pb.ts",
    ".g.dart", ".freezed.dart", ".g.cs", ".designer.cs",
)
_GENERATED_RE = re.compile(r"\.generated\.[A-Za-z0-9]+$")

# Matched case-insensitively: `Tests/`, `UnitTests/` and `__Tests__` are as
# common as their lower-case spellings (P-6).
_TEST_DIRS = frozenset({
    "test", "tests", "spec", "specs", "__tests__", "__mocks__",
    "testing", "e2e", "integration-tests", "integration_tests",
    "unittests", "unit_tests", "unit-tests", "testdata", "fixtures",
})
_TEST_FILENAMES = frozenset({"conftest.py", "setup.cfg.test", "tests.py"})
_TEST_PATTERNS = (
    re.compile(r"^test_.+\.pyi?$"),
    re.compile(r".+_test\.(py|go|rb|ts|js|dart|cc|cpp|cxx|c|exs|ex|rs|lua)$"),
    re.compile(r".+\.(test|spec|e2e-spec|e2e)\.(ts|tsx|js|jsx|mts|cts|mjs|cjs)$"),
    re.compile(r".+Tests?\.(java|cs|kt|kts|swift|php|scala|groovy)$"),
    re.compile(r".+(Spec|Suite|IT)\.(scala|groovy|kt|java)$"),
    re.compile(r".+_spec\.rb$"),
    re.compile(r"^test.+\.php$", re.IGNORECASE),
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


def is_generated_file_name(file_name: str) -> bool:
    """True for lock files, minified bundles, source maps and snapshots.

    The name-only half of ``is_ignored_path``, for callers that must not apply
    its directory rules: the indexer sees files from connectors that sync
    ``bin/`` and ``vendor/`` on purpose.
    """
    name = _basename(file_name)
    return name in _IGNORED_FILENAMES or name.endswith(_IGNORED_SUFFIXES)


# Minified code: hand-written source averages well under 100 characters a line.
# Small files are left alone -- a one-line config costs nothing to parse.
_MINIFIED_MIN_BYTES = 8 * 1024
_MINIFIED_AVG_LINE_CHARS = 500
# Generators announce themselves in the first lines: Go's `// Code generated
# ... DO NOT EDIT.`, protoc and graphql-codegen's `DO NOT EDIT`, Facebook's
# `@generated`, .NET's `<auto-generated>`.
_GENERATED_HEADER_BYTES = 1024
_GENERATED_HEADER = re.compile(
    rb"@generated\b|<auto-generated|(?i:\bgenerated\b)[^\n]*\bDO NOT EDIT\b"
    rb"|\bDO NOT EDIT\b[^\n]*(?i:\bgenerated\b)"
)


def is_generated_content(content: bytes) -> bool:
    """True for minified bundles and files a generator marked as its output.

    The content half of ``is_generated_file_name``: a bundle committed as
    ``public/embed.js`` has no telling name, and parsing one yields a block
    for every one-letter function in it.
    """
    if len(content) >= _MINIFIED_MIN_BYTES:
        lines = content.count(b"\n") + 1
        if len(content) / lines > _MINIFIED_AVG_LINE_CHARS:
            return True
    return bool(_GENERATED_HEADER.search(content[:_GENERATED_HEADER_BYTES]))


def _in_ignored_dir(dirs: list[str], file_name: str | None = None) -> bool:
    """A dependency/cache dir anywhere, or a build-output dir by the rule above.

    ``file_name`` is None for a folder: then a deeper `build/` or `out/` is still
    walked and its files are judged one by one.
    """
    for i, seg in enumerate(dirs):
        if seg in _IGNORED_DIRS:
            return True
        if seg not in _OUTPUT_DIRS:
            continue
        if i == 0:
            return True
        if seg in _TOP_LEVEL_ONLY_OUTPUT or any(d in _SOURCE_ROOTS for d in dirs[:i]):
            continue
        if seg not in _PACKAGE_LIKE_OUTPUT:
            return True
        if file_name is not None and file_name.lower().endswith(_BUILD_ARTIFACT_SUFFIXES):
            return True
    return False


def is_ignored_path(file_path: str) -> bool:
    """True when the file lives in a dependency, build-output or cache tree."""
    segs = _segments(file_path)
    if not segs:
        return False
    if _in_ignored_dir(segs[:-1], segs[-1]):
        return True
    return is_generated_file_name(segs[-1])


def is_sensitive_path(file_path: str) -> bool:
    """True for files that hold credentials or keys (S-2)."""
    name = _basename(file_path)
    lower = name.lower()
    if lower.startswith(".env.") or lower.endswith(".env"):
        return not lower.endswith(_ENV_TEMPLATE_SUFFIXES)
    return lower in _SENSITIVE_FILENAMES or lower.endswith(_SENSITIVE_SUFFIXES)


def classify_file_role(file_path: str, file_name: str | None = None) -> FileRole:
    """Classify *file_path* into a role.

    An ordered cascade: the first matching rule wins. Order is the design --
    files legitimately match several rules, and
    ``tests/fixtures/generated/foo_pb2.py`` must land on exactly one.
    """
    name = file_name or _basename(file_path)
    segs = _segments(file_path)
    dirs = set(segs[:-1]) if segs else set()
    lower_dirs = {d.lower() for d in dirs}

    if dirs & _GENERATED_DIRS or name.endswith(_GENERATED_SUFFIXES) or _GENERATED_RE.search(name):
        return FileRole.GENERATED

    if lower_dirs & _TEST_DIRS or name.lower() in _TEST_FILENAMES or any(
            p.match(name) for p in _TEST_PATTERNS):
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
    if is_ignored_path(file_path) or role in SKIPPED_ROLES or is_sensitive_path(file_name or file_path):
        return False, role
    return True, role


def should_index_code_folder(folder_path: str) -> bool:
    """False for a dependency, build-output, cache or generated directory, or
    one inside such a tree -- the folder twin of ``should_index_code_file``."""
    segs = _segments(folder_path)
    return not (_in_ignored_dir(segs) or any(seg in _GENERATED_DIRS for seg in segs))
