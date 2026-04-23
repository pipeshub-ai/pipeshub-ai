"""Tests for app.sandbox.package_policy."""

from __future__ import annotations

import pytest

from app.sandbox.models import SandboxLanguage
from app.sandbox.package_policy import (
    NPM_PACKAGE_ALLOWLIST,
    PYTHON_PACKAGE_ALLOWLIST,
    PackageNotAllowedError,
    canonicalize,
    enforce_package_allowlist,
    get_allowlist,
)


class TestCanonicalize:
    """canonicalize() must be tolerant of casing, underscores, and version specs."""

    def test_python_lowercases(self):
        assert canonicalize("Pillow", SandboxLanguage.PYTHON) == "pillow"

    def test_python_underscore_to_hyphen(self):
        assert canonicalize("python_docx", SandboxLanguage.PYTHON) == "python-docx"
        assert canonicalize("Python_Docx", SandboxLanguage.PYTHON) == "python-docx"

    def test_python_strips_version_gte(self):
        assert canonicalize("pandas>=1.5.0", SandboxLanguage.PYTHON) == "pandas"

    def test_python_strips_version_eq(self):
        assert canonicalize("numpy==1.26.4", SandboxLanguage.PYTHON) == "numpy"

    def test_python_strips_version_lt(self):
        assert canonicalize("pillow<11", SandboxLanguage.PYTHON) == "pillow"

    def test_python_strips_version_tilde(self):
        assert canonicalize("scipy~=1.13", SandboxLanguage.PYTHON) == "scipy"

    def test_npm_preserves_scope(self):
        assert canonicalize("@types/node", SandboxLanguage.TYPESCRIPT) == "@types/node"

    def test_npm_preserves_scope_with_version(self):
        assert canonicalize("@types/node@^20", SandboxLanguage.TYPESCRIPT) == "@types/node@^20"

    def test_npm_lowercases(self):
        assert canonicalize("PDFKit", SandboxLanguage.TYPESCRIPT) == "pdfkit"


class TestAllowlistSeeds:
    """The seeded allowlists should match the plan."""

    def test_anthropic_python_core_included(self):
        for name in ["pandas", "numpy", "scipy", "scikit-learn", "matplotlib",
                     "seaborn", "openpyxl", "python-docx", "python-pptx", "pypdf",
                     "pillow", "reportlab", "sympy", "tqdm"]:
            assert name in PYTHON_PACKAGE_ALLOWLIST, f"missing {name} in allowlist"

    def test_pipeshub_extras_included(self):
        for name in ["plotly", "kaleido", "fpdf2", "cairosvg", "beautifulsoup4",
                     "lxml", "pydantic", "jinja2", "tabulate", "networkx"]:
            assert name in PYTHON_PACKAGE_ALLOWLIST

    def test_network_clients_excluded(self):
        for name in ["requests", "httpx", "urllib3", "aiohttp", "paramiko",
                     "boto3", "selenium", "playwright"]:
            assert name not in PYTHON_PACKAGE_ALLOWLIST

    def test_npm_seed(self):
        for name in ["fs-extra", "sharp", "@types/node", "chart.js", "docx", "xlsx"]:
            assert name in NPM_PACKAGE_ALLOWLIST

    def test_npm_excludes_network(self):
        for name in ["axios", "node-fetch", "got", "request"]:
            assert name not in NPM_PACKAGE_ALLOWLIST

    def test_npm_document_generation_stack(self):
        """Rich .pptx / .docx generation requires pptxgenjs + react family."""
        for name in ["pptxgenjs", "docx", "react", "react-dom", "react-icons"]:
            assert name in NPM_PACKAGE_ALLOWLIST, (
                f"{name} must stay on the npm allowlist — the document design "
                f"skill references it and the sandbox Dockerfile pre-installs it."
            )


class TestEnforcePackageAllowlist:
    def test_empty_is_allowed(self):
        assert enforce_package_allowlist([], SandboxLanguage.PYTHON) == []

    def test_none_is_allowed(self):
        assert enforce_package_allowlist(None, SandboxLanguage.PYTHON) == []

    def test_single_allowed_package(self):
        assert enforce_package_allowlist(["pandas"], SandboxLanguage.PYTHON) == ["pandas"]

    def test_preserves_original_string_including_version(self):
        """The install command relies on the caller-provided version specifier."""
        assert enforce_package_allowlist(
            ["pandas>=1.5.0"], SandboxLanguage.PYTHON,
        ) == ["pandas>=1.5.0"]

    def test_preserves_casing(self):
        """Canonicalization is for LOOKUP only; returned string is unchanged."""
        result = enforce_package_allowlist(["Pillow"], SandboxLanguage.PYTHON)
        assert result == ["Pillow"]

    def test_underscore_accepted_via_canonicalization(self):
        result = enforce_package_allowlist(["python_docx"], SandboxLanguage.PYTHON)
        assert result == ["python_docx"]

    def test_rejects_unknown_package(self):
        with pytest.raises(PackageNotAllowedError) as exc_info:
            enforce_package_allowlist(["not-a-real-package"], SandboxLanguage.PYTHON)
        assert exc_info.value.package == "not-a-real-package"
        assert exc_info.value.language == SandboxLanguage.PYTHON

    def test_rejects_network_client(self):
        with pytest.raises(PackageNotAllowedError):
            enforce_package_allowlist(["requests"], SandboxLanguage.PYTHON)

    def test_rejects_typosquat_of_allowed(self):
        """A typosquat of an allowed package name is still rejected."""
        with pytest.raises(PackageNotAllowedError):
            enforce_package_allowlist(["pandaz"], SandboxLanguage.PYTHON)

    def test_rejection_carries_allowed_list(self):
        with pytest.raises(PackageNotAllowedError) as exc_info:
            enforce_package_allowlist(["evil"], SandboxLanguage.PYTHON)
        assert "pandas" in exc_info.value.allowed
        assert "numpy" in exc_info.value.allowed

    def test_npm_allowed(self):
        assert enforce_package_allowlist(
            ["chart.js", "@types/node"], SandboxLanguage.TYPESCRIPT,
        ) == ["chart.js", "@types/node"]

    def test_npm_rejects_unknown(self):
        with pytest.raises(PackageNotAllowedError):
            enforce_package_allowlist(["axios"], SandboxLanguage.TYPESCRIPT)

    def test_rejects_when_language_has_no_allowlist(self):
        with pytest.raises(PackageNotAllowedError):
            enforce_package_allowlist(["whatever"], SandboxLanguage.SQLITE)

    def test_sqlite_with_empty_is_ok(self):
        assert enforce_package_allowlist([], SandboxLanguage.SQLITE) == []


class TestEnvExtension:
    """SANDBOX_EXTRA_ALLOWED_* must widen the allowlist at runtime."""

    def test_env_extension_python(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", "my-internal-lib,another-lib")
        allowed = get_allowlist(SandboxLanguage.PYTHON)
        assert "my-internal-lib" in allowed
        assert "another-lib" in allowed
        # Base allowlist still intact
        assert "pandas" in allowed

    def test_env_extension_python_enforced(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", "my-internal-lib")
        assert enforce_package_allowlist(
            ["my-internal-lib"], SandboxLanguage.PYTHON,
        ) == ["my-internal-lib"]

    def test_env_extension_npm(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_NPM_PACKAGES", "internal-lib,@scope/other")
        allowed = get_allowlist(SandboxLanguage.TYPESCRIPT)
        assert "internal-lib" in allowed
        assert "@scope/other" in allowed

    def test_env_extension_empty_string_is_no_op(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", "")
        allowed = get_allowlist(SandboxLanguage.PYTHON)
        assert allowed == PYTHON_PACKAGE_ALLOWLIST

    def test_env_extension_handles_whitespace(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", "  lib-a  ,  ,lib-b ")
        allowed = get_allowlist(SandboxLanguage.PYTHON)
        assert "lib-a" in allowed
        assert "lib-b" in allowed

    def test_env_extension_canonicalizes(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", "My_Lib")
        # Agent requests with original casing/underscore should still pass.
        assert enforce_package_allowlist(
            ["my-lib"], SandboxLanguage.PYTHON,
        ) == ["my-lib"]
        assert enforce_package_allowlist(
            ["MY_LIB"], SandboxLanguage.PYTHON,
        ) == ["MY_LIB"]

    def test_env_extension_isolated_between_languages(self, monkeypatch):
        """Extending the Python list must not widen the npm list."""
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", "some-lib")
        with pytest.raises(PackageNotAllowedError):
            enforce_package_allowlist(["some-lib"], SandboxLanguage.TYPESCRIPT)
