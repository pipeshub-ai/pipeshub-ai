"""Data models for sandbox execution results and configuration."""

from __future__ import annotations

import logging
import os
import re
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SandboxLanguage(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


class SandboxMode(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"


class ArtifactOutput(BaseModel):
    """A file produced by sandbox code execution."""

    file_name: str
    file_path: str  # absolute path on host
    mime_type: str
    size_bytes: int


class ExecutionResult(BaseModel):
    """Result of a sandbox code execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    execution_time_ms: int = 0
    artifacts: list[ArtifactOutput] = Field(default_factory=list)
    error: str | None = None


# Default resource limits
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MEMORY_LIMIT_MB = 512
DEFAULT_CPU_LIMIT = 0.5

# Single unified Docker image for all sandbox languages.
# Default tag is for local builds from ``deployment/sandbox/Dockerfile``.
# Override ``SANDBOX_DOCKER_IMAGE`` for a registry image (e.g. production:
# ``pipeshubai/pipeshub-sandbox:${TAG}`` per docker-compose.prod.yml).
SANDBOX_IMAGE = os.environ.get("SANDBOX_DOCKER_IMAGE", "pipeshub/sandbox:latest")

# MIME type detection by extension
EXTENSION_TO_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
}


def detect_mime_type(file_name: str) -> str:
    """Detect MIME type from file extension."""
    import os

    _, ext = os.path.splitext(file_name)
    return EXTENSION_TO_MIME.get(ext.lower(), "application/octet-stream")


# Strict allowlist for package names to prevent shell injection.
# Allows standard PyPI/npm names with optional version specifiers.
_PACKAGE_NAME_RE = re.compile(
    r"^@?[a-zA-Z0-9]"            # must start with alphanum (or @ for npm scoped)
    r"[a-zA-Z0-9._\-/]*"         # body: alphanum, dots, underscores, hyphens, slash (scoped)
    r"([<>=!~]+[a-zA-Z0-9.*]+)?$" # optional version specifier
)

_SHELL_DANGEROUS = re.compile(r"[;&|`$(){}\[\]!#~\\'\"\n\r]")


def validate_packages(
    packages: list[str] | None,
    *,
    language: SandboxLanguage | None = None,
) -> list[str]:
    """Return a sanitised package list.

    Two layers of defence, in order:

    1. Shell-injection guard: rejects any name that contains shell
       metacharacters or that does not match ``_PACKAGE_NAME_RE``.
    2. Allowlist (only when ``language`` is provided): delegates to
       :func:`app.sandbox.package_policy.enforce_package_allowlist` so that
       only curated packages are ever passed to ``pip install`` / ``npm
       install``. Callers that cannot supply a language (for example, legacy
       code paths that validated packages before a language was known) can
       omit the kwarg and receive only the injection guard.

    Raises ``ValueError`` for any package that fails either check. The
    ``PackageNotAllowedError`` raised by the allowlist layer is a subclass
    of ``ValueError``, so existing ``except ValueError`` handlers keep
    working.
    """
    if not packages:
        return []
    clean: list[str] = []
    for pkg in packages:
        pkg = pkg.strip()
        if not pkg:
            continue
        if _SHELL_DANGEROUS.search(pkg) or not _PACKAGE_NAME_RE.match(pkg):
            raise ValueError(
                f"Invalid package name rejected (possible injection): {pkg!r}"
            )
        clean.append(pkg)

    if language is not None and clean:
        # Imported lazily to avoid a circular import at module load time.
        from app.sandbox.package_policy import enforce_package_allowlist
        enforce_package_allowlist(clean, language)

    return clean
