"""Smoke tests for the sandbox helper libraries and the Dockerfile recipe.

We can't run TypeScript from pytest, but we can verify the repo layout,
`package.json` metadata, and that the Dockerfile ships everything the
rich-pptx/docx workflow depends on. If any of these regress, agent-authored
code that does ``import { Deck } from "pipeshub-slides"`` will silently
fail in the sandbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _find_sandbox_dir() -> Path | None:
    """Walk up from this file until we find ``deployment/sandbox``.

    The test file lives at ``backend/python/tests/unit/sandbox/`` in-repo,
    but depending on how pytest is invoked (installed wheel, editable,
    monorepo checkout) the relative depth can vary, so we search instead
    of hard-coding a ``parents[N]`` index.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "deployment" / "sandbox"
        if candidate.is_dir():
            return candidate
    return None


SANDBOX_DIR = _find_sandbox_dir()
LIB_DIR = SANDBOX_DIR / "lib" if SANDBOX_DIR else None
DOCKERFILE = SANDBOX_DIR / "Dockerfile" if SANDBOX_DIR else None


def _skip_if_repo_missing() -> None:
    if SANDBOX_DIR is None or not SANDBOX_DIR.is_dir():
        pytest.skip(
            "deployment/sandbox not available (tests running outside a repo checkout)",
        )


class TestHelperLibLayout:
    """Both helper libraries ship as `<name>/src/index.ts` with a package.json."""

    def test_pipeshub_slides_structure(self):
        _skip_if_repo_missing()
        root = LIB_DIR / "pipeshub-slides"
        assert root.is_dir()
        assert (root / "package.json").is_file()
        assert (root / "src" / "index.ts").is_file()
        assert (root / "src" / "deck.ts").is_file()
        assert (root / "src" / "themes.ts").is_file()
        assert (root / "src" / "layout.ts").is_file()

    def test_pipeshub_docs_structure(self):
        _skip_if_repo_missing()
        root = LIB_DIR / "pipeshub-docs"
        assert root.is_dir()
        assert (root / "package.json").is_file()
        assert (root / "src" / "index.ts").is_file()
        assert (root / "src" / "report.ts").is_file()
        assert (root / "src" / "themes.ts").is_file()

    def test_pipeshub_slides_package_json_metadata(self):
        _skip_if_repo_missing()
        pkg = json.loads(
            (LIB_DIR / "pipeshub-slides" / "package.json").read_text(),
        )
        assert pkg["name"] == "pipeshub-slides"
        # Agent scripts run as ESM (`.mts`) and Node's native ESM loader
        # won't execute `.ts` files. The package MUST resolve to compiled
        # `.js` under `dist/` — a regression here reintroduces the
        # "does not provide an export named 'Deck'" failure we hit in
        # Node 25 + tsx 4.x.
        assert pkg["main"].endswith(".js"), (
            f"pipeshub-slides must publish compiled JS, got main={pkg['main']!r}"
        )
        assert pkg.get("type") == "module", (
            "pipeshub-slides must declare type=module so the compiled "
            "dist/*.js is loaded as ESM"
        )
        assert pkg["private"] is True
        assert "pptxgenjs" in pkg.get("peerDependencies", {})

    def test_pipeshub_docs_package_json_metadata(self):
        _skip_if_repo_missing()
        pkg = json.loads(
            (LIB_DIR / "pipeshub-docs" / "package.json").read_text(),
        )
        assert pkg["name"] == "pipeshub-docs"
        assert pkg["main"].endswith(".js")
        assert pkg.get("type") == "module"
        assert pkg["private"] is True
        assert "docx" in pkg.get("peerDependencies", {})


class TestHelperLibPublicApi:
    """Public symbols referenced by the design skills must stay exported."""

    def test_pipeshub_slides_exports_deck_and_themes(self):
        _skip_if_repo_missing()
        index = (LIB_DIR / "pipeshub-slides" / "src" / "index.ts").read_text()
        for symbol in ("Deck", "THEMES", "resolveTheme", "LAYOUTS"):
            assert symbol in index, (
                f"{symbol} must remain exported from pipeshub-slides — "
                f"the design skill examples reference it."
            )

    def test_pipeshub_docs_exports_report_and_themes(self):
        _skip_if_repo_missing()
        index = (LIB_DIR / "pipeshub-docs" / "src" / "index.ts").read_text()
        for symbol in ("Report", "THEMES", "resolveTheme"):
            assert symbol in index

    def test_deck_primitives_are_present(self):
        """Every primitive the PPTX skill documents must exist on Deck."""
        _skip_if_repo_missing()
        deck = (LIB_DIR / "pipeshub-slides" / "src" / "deck.ts").read_text()
        for method in (
            "titleSlide",
            "twoColumn",
            "statGrid",
            "iconRows",
            "timeline",
            "sectionDivider",
            "closing",
            "save",
        ):
            assert f"{method}(" in deck, f"Deck.{method} must exist"

    def test_report_primitives_are_present(self):
        """Every primitive the DOCX skill documents must exist on Report."""
        _skip_if_repo_missing()
        report = (LIB_DIR / "pipeshub-docs" / "src" / "report.ts").read_text()
        for method in (
            "coverPage",
            "tableOfContents",
            "heading1",
            "heading2",
            "paragraph",
            "bulletList",
            "numberedList",
            "calloutBox",
            "table",
            "image",
            "pageBreak",
            "save",
        ):
            assert f"{method}(" in report, f"Report.{method} must exist"


class TestThemePaletteInvariants:
    """Themes are serialised with raw hex (no '#') — pptxgenjs requires it."""

    def test_slides_theme_colors_have_no_hash_prefix(self):
        _skip_if_repo_missing()
        themes = (LIB_DIR / "pipeshub-slides" / "src" / "themes.ts").read_text()
        # A '#' prefix on ANY literal color would corrupt the .pptx. The
        # themes file should never ship one. The skill document explicitly
        # warns about this pitfall.
        for line in themes.splitlines():
            if ":" in line and '"' in line and "#" in line:
                # Comments are fine; color values aren't.
                stripped = line.strip()
                if stripped.startswith("//"):
                    continue
                # Reject any line that looks like a hex value with a '#'.
                assert '"#' not in stripped, (
                    f"themes.ts palette value must not be '#'-prefixed: {stripped!r}"
                )


class TestDockerfileContract:
    """The Dockerfile is the only place where the sandbox image is assembled.
    These tests pin its public contract so an accidental deletion breaks CI
    rather than silently breaking PPTX/DOCX generation in production."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_repo(self):
        if DOCKERFILE is None or not DOCKERFILE.is_file():
            pytest.skip("Dockerfile not available outside a repo checkout")

    def test_installs_libreoffice_and_poppler(self):
        body = DOCKERFILE.read_text()
        for pkg in (
            "libreoffice-core",
            "libreoffice-impress",
            "libreoffice-writer",
            "poppler-utils",
        ):
            assert pkg in body, (
                f"sandbox Dockerfile must install {pkg} for the visual-QA loop"
            )

    def test_preinstalls_document_generation_npm_packages(self):
        body = DOCKERFILE.read_text()
        for pkg in ("pptxgenjs", "docx", "react", "react-dom", "react-icons"):
            assert pkg in body, (
                f"sandbox Dockerfile must pre-install {pkg} (rich .pptx/.docx)"
            )

    def test_ships_helper_libraries_into_image(self):
        body = DOCKERFILE.read_text()
        # COPY must bring both libs into the image. A symlink into the
        # user's node_modules is what makes `import "pipeshub-slides"` work.
        assert "pipeshub-slides" in body
        assert "pipeshub-docs" in body
        assert "node_modules/pipeshub-slides" in body
        assert "node_modules/pipeshub-docs" in body
