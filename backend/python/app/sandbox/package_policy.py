"""Package safety policy for the sandbox.

Every ``requirements`` / ``packages`` value that reaches the sandbox executors
passes through :func:`enforce_package_allowlist`. Only names that are on the
curated allowlist (or an operator-supplied extension) are installed. Everything
else is rejected with a :class:`PackageNotAllowedError`.

The Python allowlist is seeded from Anthropic's published Claude
code-execution tool pre-installed library list
(https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/code-execution-tool)
plus a small set of pipeshub extras chosen because they are useful for
offline document generation. The npm allowlist has no authoritative upstream
reference (Claude's sandbox has no Node runtime), so it is a minimal curated
set focused on document / data / chart output.

Packages that are intentionally NOT on the allowlist:

- Network clients: ``requests``, ``httpx``, ``urllib3``, ``aiohttp``,
  ``paramiko``, ``selenium``, ``playwright``, ``boto3``, ``google-cloud-*``.
  The sandbox run container has no network, so these only invite exfil
  patterns.
- Arbitrary-code evaluators: anything whose purpose is to dynamically load
  arbitrary code from the network.

Extension for operators:

- ``SANDBOX_EXTRA_ALLOWED_PY_PACKAGES`` — comma-separated list of additional
  PyPI package names to allow.
- ``SANDBOX_EXTRA_ALLOWED_NPM_PACKAGES`` — comma-separated list of additional
  npm package names to allow.

Known upstream issues (docstring note, not enforced here):

- ``pdfplumber`` -> ``charset_normalizer`` ships mypyc artifacts that can
  fail to load on slim base images; see
  https://github.com/anthropics/anthropic-sdk-python/issues/1340. If that
  bites, pin ``charset-normalizer<3.4`` in the sandbox image.
- ``pdfkit`` needs ``wkhtmltopdf`` installed at the OS level.
- ``tabula-py`` needs a JRE (``default-jre-headless``).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable

from app.sandbox.models import SandboxLanguage

logger = logging.getLogger(__name__)


class PackageNotAllowedError(ValueError):
    """Raised when a requested package is not on the sandbox allowlist.

    Carries the offending package name and the full allowlist so that the
    caller (usually a tool handler) can surface a structured, retryable error
    to the agent.
    """

    def __init__(
        self,
        package: str,
        language: SandboxLanguage,
        allowed: Iterable[str],
    ) -> None:
        self.package = package
        self.language = language
        self.allowed = sorted(allowed)
        super().__init__(
            f"Package {package!r} is not on the {language.value} sandbox allowlist. "
            f"Allowed: {', '.join(self.allowed)}"
        )


# ---------------------------------------------------------------------------
# Curated allowlists
# ---------------------------------------------------------------------------

# Anthropic's published Claude code-execution sandbox pre-installed pip list.
# Source: https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/code-execution-tool
_ANTHROPIC_PUBLISHED_PYTHON: frozenset[str] = frozenset({
    # Data Science
    "pandas", "numpy", "scipy", "scikit-learn", "statsmodels",
    # Visualization
    "matplotlib", "seaborn",
    # File Processing
    "pyarrow", "openpyxl", "xlsxwriter", "xlrd", "pillow",
    "python-pptx", "python-docx", "pypdf", "pdfplumber", "pypdfium2",
    "pdf2image", "pdfkit", "tabula-py", "reportlab", "pycairo", "img2pdf",
    # Math & Computing
    "sympy", "mpmath",
    # Utilities
    "tqdm", "python-dateutil", "pytz", "joblib",
})

# pipeshub-specific extras. Offline-safe and useful for document generation.
_PIPESHUB_PYTHON_EXTRAS: frozenset[str] = frozenset({
    "beautifulsoup4",
    "lxml",
    "pydantic",
    "networkx",
    "jinja2",
    "fpdf2",
    "cairosvg",
    "tabulate",
    "plotly",
    "kaleido",
})

#: Default Python allowlist (before operator env extension).
PYTHON_PACKAGE_ALLOWLIST: frozenset[str] = (
    _ANTHROPIC_PUBLISHED_PYTHON | _PIPESHUB_PYTHON_EXTRAS
)

#: Default npm allowlist (no authoritative Claude reference; curated locally).
#: Includes document-generation stack: pptxgenjs + docx (docx-js) for new
#: .pptx / .docx from scratch, react + react-dom + react-icons to rasterise
#: icons into slides, and sharp for image processing. pipeshub-slides /
#: pipeshub-docs are shipped as internal modules inside the sandbox image and
#: therefore do not need to be on the installable allowlist.
NPM_PACKAGE_ALLOWLIST: frozenset[str] = frozenset({
    "fs-extra",
    "sharp",
    "@types/node",
    "csv-stringify",
    "json2csv",
    "chart.js",
    "docx",
    "pdfkit",
    "jsdom",
    "xlsx",
    "papaparse",
    "pptxgenjs",
    "react",
    "react-dom",
    "react-icons",
})


# ---------------------------------------------------------------------------
# Canonicalization + enforcement
# ---------------------------------------------------------------------------

# Match a pip-style version specifier trailing a package name:
#   "pandas>=1.5", "numpy==1.26.*", "pillow<11"
_VERSION_SPEC_RE = re.compile(r"[<>=!~].+$")


def _strip_version(name: str) -> str:
    """Return *name* with any trailing version specifier removed."""
    return _VERSION_SPEC_RE.sub("", name).strip()


def canonicalize(name: str, language: SandboxLanguage) -> str:
    """Return the canonical allowlist key for *name*.

    - Strips trailing version specifiers (``pandas>=1.5`` -> ``pandas``).
    - Lower-cases.
    - For Python, replaces ``_`` with ``-`` (PyPI normalization).
    - For npm, preserves ``@scope/package`` scoping.
    """
    base = _strip_version(name).lower()
    if language == SandboxLanguage.PYTHON:
        return base.replace("_", "-")
    return base


def _parse_env_extra(var_name: str, language: SandboxLanguage) -> set[str]:
    raw = os.environ.get(var_name, "")
    if not raw:
        return set()
    return {
        canonicalize(piece, language)
        for piece in raw.split(",")
        if piece.strip()
    }


def get_allowlist(language: SandboxLanguage) -> frozenset[str]:
    """Return the effective allowlist for *language* (base + env extension)."""
    if language == SandboxLanguage.PYTHON:
        base = PYTHON_PACKAGE_ALLOWLIST
        extras = _parse_env_extra("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", language)
    elif language == SandboxLanguage.TYPESCRIPT:
        base = NPM_PACKAGE_ALLOWLIST
        extras = _parse_env_extra("SANDBOX_EXTRA_ALLOWED_NPM_PACKAGES", language)
    else:
        return frozenset()
    if not extras:
        return base
    return frozenset(base | extras)


def enforce_package_allowlist(
    packages: list[str] | None,
    language: SandboxLanguage,
) -> list[str]:
    """Validate *packages* against the allowlist for *language*.

    Returns the original ``packages`` list unchanged (callers may rely on the
    exact strings, including any version specifier, for the actual install
    command). Raises :class:`PackageNotAllowedError` for the first offending
    entry. Empty / ``None`` input returns an empty list.

    Languages with no Node/PyPI-style package ecosystem (sqlite, postgresql)
    short-circuit: empty input is allowed, any non-empty input is rejected
    since passing packages makes no sense for those languages.
    """
    if not packages:
        return []

    if language not in (SandboxLanguage.PYTHON, SandboxLanguage.TYPESCRIPT):
        raise PackageNotAllowedError(packages[0], language, allowed=[])

    allowed = get_allowlist(language)
    for pkg in packages:
        key = canonicalize(pkg, language)
        if key not in allowed:
            logger.warning(
                "Sandbox package rejected by allowlist: language=%s requested=%r key=%r",
                language.value, pkg, key,
            )
            raise PackageNotAllowedError(pkg, language, allowed)
    return list(packages)
