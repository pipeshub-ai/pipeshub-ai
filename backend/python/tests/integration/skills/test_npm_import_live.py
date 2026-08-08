"""Live end-to-end verification of the npm skill-import pipeline
(`npm_command_parser.parse_npm_command` -> `SkillPackageImporter.preview_npm`)
against the REAL npm registry — no mocks.

Every other npm-import test (`tests/unit/services/skills/test_package_importer.py`)
stubs `httpx.AsyncClient`; this file exists because "the parser + extractor
work against a mocked tarball" does not prove "a real npm-published skill
package installs correctly" — registry response shape, real gzip/tar framing,
and real third-party SKILL.md content are exactly the things a mock can't
catch. Pinned to specific package@version so a future republish can't shift
these assertions; if a package is unpublished this suite will start failing
loudly rather than silently skip (a deliberate signal to swap the fixture).

Requires: network access to registry.npmjs.org.
Run: pytest tests/integration/skills/test_npm_import_live.py -m integration --timeout=60
"""

from __future__ import annotations

import httpx
import pytest

from app.services.skills.npm_command_parser import parse_npm_command
from app.services.skills.package_importer import (
    PackageImportError,
    SkillPackageImporter,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_REGISTRY_URL = "https://registry.npmjs.org"

# Real, published agentskills.io-format packages discovered on the public npm
# registry, pinned to the exact version verified when this test was written.
_ROOT_SKILL_PACKAGE = "nsauditor-ai-agent-skill"
_ROOT_SKILL_VERSION = "0.2.29"
_NESTED_SKILL_PACKAGE = "@velinussage/locus-agent-skill"
_NESTED_SKILL_VERSION = "0.1.9"

# A real, stable npm package with no SKILL.md — proves the "not a skill
# package" rejection path against a real tarball, not a synthetic one.
_NON_SKILL_PACKAGE = "left-pad"
_NON_SKILL_VERSION = "1.3.0"


@pytest.fixture(scope="module", autouse=True)
def _require_npm_registry_reachable() -> None:
    try:
        resp = httpx.get(f"{_REGISTRY_URL}/left-pad/1.3.0", timeout=5.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        pytest.skip(f"registry.npmjs.org is not reachable from this environment: {e}")


class TestNpmImportRootSkillMd:
    """Package with `SKILL.md` at the tarball root (`package/SKILL.md`) —
    the common case, mirrors most real agentskills.io npm packages."""

    async def test_parses_install_command_and_imports_real_package(self) -> None:
        spec = parse_npm_command(f"npm install {_ROOT_SKILL_PACKAGE}@{_ROOT_SKILL_VERSION}")
        assert spec.name == _ROOT_SKILL_PACKAGE
        assert spec.version == _ROOT_SKILL_VERSION

        importer = SkillPackageImporter()
        preview = await importer.preview_npm(spec)

        assert preview.name  # SKILL.md frontmatter `name` parsed successfully
        assert preview.description
        assert preview.content.startswith("---")  # raw SKILL.md, frontmatter intact
        assert preview.source_label == f"npm:{_ROOT_SKILL_PACKAGE}@{_ROOT_SKILL_VERSION}"
        # This package bundles real markdown reference files alongside SKILL.md.
        assert preview.resources
        assert all(path.startswith("references/") for path in preview.resources)
        assert not preview.skipped_binary_resources

    async def test_bare_package_name_resolves_to_latest(self) -> None:
        """No @version suffix — parser defaults to 'latest', importer must
        still resolve a real, current tarball from the registry."""
        spec = parse_npm_command(f"npx {_ROOT_SKILL_PACKAGE}")
        assert spec.version == "latest"

        importer = SkillPackageImporter()
        preview = await importer.preview_npm(spec)

        assert preview.name
        assert preview.source_label.startswith(f"npm:{_ROOT_SKILL_PACKAGE}@")


class TestNpmImportNestedSkillMd:
    """Scoped package with `SKILL.md` nested below the tarball root
    (`package/skill/<name>/SKILL.md`) — exercises `_strip_common_prefix` +
    the skill-dir-prefix resource-path resolution against a real archive."""

    async def test_scoped_package_with_nested_skill_md(self) -> None:
        spec = parse_npm_command(f"npm i {_NESTED_SKILL_PACKAGE}@{_NESTED_SKILL_VERSION}")
        assert spec.name == _NESTED_SKILL_PACKAGE

        importer = SkillPackageImporter()
        preview = await importer.preview_npm(spec)

        assert preview.name
        assert preview.content.strip()
        assert preview.source_label == f"npm:{_NESTED_SKILL_PACKAGE}@{_NESTED_SKILL_VERSION}"


class TestNpmImportErrorPaths:
    """The importer must fail loudly and safely on real (not mocked)
    negative cases: a real published package that isn't a skill, and a
    package name that has never existed on the registry."""

    async def test_real_package_without_skill_md_is_rejected(self) -> None:
        spec = parse_npm_command(f"npm install {_NON_SKILL_PACKAGE}@{_NON_SKILL_VERSION}")

        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="No SKILL.md found"):
            await importer.preview_npm(spec)

    async def test_nonexistent_package_returns_not_found_error(self) -> None:
        spec = parse_npm_command("npm install this-package-definitely-does-not-exist-pipeshub-xyz")

        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="not found on the npm registry"):
            await importer.preview_npm(spec)
