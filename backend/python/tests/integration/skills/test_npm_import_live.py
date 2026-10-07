"""Live end-to-end verification of the npm skill-import pipeline
(`npm_command_parser.parse_npm_command` -> `SkillPackageImporter.preview_npm`)
against the REAL npm registry — no mocks.

Every other npm-import test (`tests/unit/services/skills/test_package_importer.py`)
stubs `PublicUrlFetcher`; this file exists because "the parser + extractor
work against a mocked tarball" does not prove "a real npm-published skill
package installs correctly" — registry response shape, real gzip/tar framing,
and real third-party SKILL.md content are exactly the things a mock can't
catch. Pinned to specific package@version so a future republish can't shift
these assertions; if a package is unpublished this suite will start failing
loudly rather than silently skip (a deliberate signal to swap the fixture).

That loudness is about the *packages*. A registry these tests cannot reach is
a different condition, and reporting it as a failed assertion says something
untrue about the code — so a transport failure skips, carrying the underlying
error in its reason, while every other outcome still fails.

Requires: network access to registry.npmjs.org.
"""

from __future__ import annotations

import re
from dataclasses import replace

import httpx
import pytest

from app.services.skills.npm_command_parser import PackageSpec, parse_npm_command
from app.services.skills.package_importer import (
    ImportPreview,
    PackageImportError,
    SkillPackageImporter,
)
from app.utils.public_http import (
    PublicFetchLimits,
    PublicFetchResponse,
    PublicUrlFetcher,
)

# The suite-wide default is 30s (pytest.ini). These tests pull real tarballs
# over the public internet, so they carry their own budget rather than
# depending on the runner being invoked with an override.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.timeout(180),
]

_REGISTRY_URL = "https://registry.npmjs.org"

# Real, published agentskills.io-format packages discovered on the public npm
# registry, pinned to the exact version verified when this test was written.
_ROOT_SKILL_PACKAGE = "nsauditor-ai-agent-skill"
_ROOT_SKILL_VERSION = "0.2.29"
_ROOT_SKILL_REFERENCES = (
    "references/plugins.md",
    "references/schemas.md",
    "references/workflows.md",
)
_NESTED_SKILL_PACKAGE = "@velinussage/locus-agent-skill"
_NESTED_SKILL_VERSION = "0.1.9"

# A real, stable npm package with no SKILL.md — proves the "not a skill
# package" rejection path against a real tarball, not a synthetic one.
_NON_SKILL_PACKAGE = "left-pad"
_NON_SKILL_VERSION = "1.3.0"

# `SkillPackageImporter` fetches with a 15s budget, sized for a user waiting
# on a request. A live test fetching tarballs while the rest of the suite runs
# needs headroom instead; without it the suite's own load is enough to trip it.
_TEST_NETWORK_TIMEOUT_SECONDS = 60.0

# The registry answered, but only to say it cannot serve right now.
_UNAVAILABLE_STATUS = re.compile(r"\(HTTP (429|5\d\d)\)")


class _PatientFetcher(PublicUrlFetcher):
    """The importer's SSRF-safe fetcher, with the test's longer timeout."""

    async def get(self, url: str, limits: PublicFetchLimits) -> PublicFetchResponse:
        return await super().get(url, replace(limits, timeout_s=_TEST_NETWORK_TIMEOUT_SECONDS))


@pytest.fixture(scope="module", autouse=True)
def _require_npm_registry_reachable() -> None:
    """Skip the module up front when the registry is plainly unreachable.

    A first approximation only: it proves DNS and TLS work at collection time,
    not that a multi-megabyte tarball will arrive before the timeout. The
    per-test guard below is what catches a failure partway through a run.
    """
    try:
        resp = httpx.get(f"{_REGISTRY_URL}/left-pad/1.3.0", timeout=5.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        pytest.skip(f"registry.npmjs.org is not reachable from this environment: {e}")


@pytest.fixture
def importer() -> SkillPackageImporter:
    """The real importer, with a budget suited to a loaded test runner."""
    return SkillPackageImporter(fetcher=_PatientFetcher())


def _skip_if_transport_failed(exc: PackageImportError) -> None:
    """Turn a network failure into a skip; leave every other error alone.

    Without this a timeout surfaces as "Regex pattern did not match", which
    reads as though the importer raised the wrong error — a claim about the
    code, drawn from an event that says nothing about it. The importer shows
    users fixed text, so the network failure is read from the causes it chains.
    A failed DNS lookup arrives as an "unsafe URL" refusal, so the type of the
    importer's own error cannot tell the two apart.
    """
    cause = exc.__cause__
    while cause is not None:
        if isinstance(cause, (httpx.TransportError, OSError)):
            pytest.skip(f"npm registry became unreachable mid-test: {cause}")
        cause = cause.__cause__
    if _UNAVAILABLE_STATUS.search(str(exc)):
        pytest.skip(f"npm registry is unavailable: {exc}")


async def _preview(importer: SkillPackageImporter, spec: PackageSpec) -> ImportPreview:
    """Import, skipping on transport failure and failing on anything else.

    A package that was unpublished, or a tarball whose shape changed, still
    raises — which is the point of pinning the versions.
    """
    try:
        return await importer.preview_npm(spec)
    except PackageImportError as e:
        _skip_if_transport_failed(e)
        raise


async def _expect_import_error(
    importer: SkillPackageImporter, spec: PackageSpec, match: str
) -> None:
    """Assert the importer rejects ``spec`` with a message matching ``match``.

    Deliberately not ``pytest.raises(..., match=...)``: that cannot tell "the
    importer returned the wrong error" from "the network died", and those two
    need opposite outcomes.
    """
    try:
        await importer.preview_npm(spec)
    except PackageImportError as e:
        _skip_if_transport_failed(e)
        assert re.search(match, str(e)), (
            f"expected a PackageImportError matching {match!r}, got: {e}"
        )
        return
    pytest.fail(f"expected PackageImportError matching {match!r}, but none was raised")


class TestNpmImportRootSkillMd:
    """Package with `SKILL.md` at the tarball root (`package/SKILL.md`) —
    the common case, mirrors most real agentskills.io npm packages."""

    async def test_parses_install_command_and_imports_real_package(
        self, importer: SkillPackageImporter
    ) -> None:
        spec = parse_npm_command(f"npm install {_ROOT_SKILL_PACKAGE}@{_ROOT_SKILL_VERSION}")
        assert spec.name == _ROOT_SKILL_PACKAGE
        assert spec.version == _ROOT_SKILL_VERSION

        preview = await _preview(importer, spec)

        assert preview.name  # SKILL.md frontmatter `name` parsed successfully
        assert preview.description
        assert preview.content.startswith("---")  # raw SKILL.md, frontmatter intact
        assert preview.source_label == f"npm:{_ROOT_SKILL_PACKAGE}@{_ROOT_SKILL_VERSION}"
        # This package bundles real markdown reference files alongside SKILL.md.
        # Not only references/: any file in the skill directory is a resource,
        # as the agentskills.io spec allows.
        assert set(_ROOT_SKILL_REFERENCES) <= set(preview.resources)
        assert not preview.skipped_binary_resources

    async def test_bare_package_name_resolves_to_latest(
        self, importer: SkillPackageImporter
    ) -> None:
        """No @version suffix — parser defaults to 'latest', importer must
        still resolve a real, current tarball from the registry."""
        spec = parse_npm_command(f"npx {_ROOT_SKILL_PACKAGE}")
        assert spec.version == "latest"

        preview = await _preview(importer, spec)

        assert preview.name
        assert preview.source_label.startswith(f"npm:{_ROOT_SKILL_PACKAGE}@")


class TestNpmImportNestedSkillMd:
    """Scoped package with `SKILL.md` nested below the tarball root
    (`package/skill/<name>/SKILL.md`) — exercises `_strip_common_prefix` +
    the skill-dir-prefix resource-path resolution against a real archive."""

    async def test_scoped_package_with_nested_skill_md(
        self, importer: SkillPackageImporter
    ) -> None:
        spec = parse_npm_command(f"npm i {_NESTED_SKILL_PACKAGE}@{_NESTED_SKILL_VERSION}")
        assert spec.name == _NESTED_SKILL_PACKAGE

        preview = await _preview(importer, spec)

        assert preview.name
        assert preview.content.strip()
        assert preview.source_label == f"npm:{_NESTED_SKILL_PACKAGE}@{_NESTED_SKILL_VERSION}"


class TestNpmImportErrorPaths:
    """The importer must fail loudly and safely on real (not mocked)
    negative cases: a real published package that isn't a skill, and a
    package name that has never existed on the registry."""

    async def test_real_package_without_skill_md_is_rejected(
        self, importer: SkillPackageImporter
    ) -> None:
        spec = parse_npm_command(f"npm install {_NON_SKILL_PACKAGE}@{_NON_SKILL_VERSION}")

        await _expect_import_error(importer, spec, "No SKILL.md found")

    async def test_nonexistent_package_returns_not_found_error(
        self, importer: SkillPackageImporter
    ) -> None:
        spec = parse_npm_command("npm install this-package-definitely-does-not-exist-pipeshub-xyz")

        await _expect_import_error(importer, spec, "not found on the npm registry")
