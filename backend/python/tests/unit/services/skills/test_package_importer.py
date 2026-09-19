"""Tests for app.services.skills.package_importer — archive extraction,
zip-slip guards, and the three preview sources (npm / URL / upload).

Network calls go through a fake `PublicUrlFetcher` — these tests never hit
the real npm registry or any external URL.
"""
import io
import json
import tarfile
import zipfile
from unittest.mock import MagicMock

import pytest

from app.services.skills import package_importer
from app.services.skills.npm_command_parser import PackageSpec
from app.services.skills.package_importer import (
    ImportPreview,
    PackageImportError,
    SkillPackageImporter,
)
from app.utils.public_http import (
    PublicFetchError,
    PublicFetchLimits,
    PublicFetchResponse,
    PublicUrlFetcher,
    ResponseTooLargeError,
    TooManyRedirectsError,
    UnsafeUrlError,
)

_VALID_SKILL_MD = """---
name: pdf-extractor
description: Extracts tables from PDF files
---

# PDF extractor

Use this skill when the user asks to pull tabular data out of a PDF.
"""


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, content in files.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestPreviewUpload:
    def test_valid_zip_with_skill_md_only(self) -> None:
        data = _make_zip({"SKILL.md": _VALID_SKILL_MD.encode()})
        importer = SkillPackageImporter()
        preview = importer.preview_upload("pdf-extractor.zip", data)
        assert isinstance(preview, ImportPreview)
        assert preview.name == "pdf-extractor"
        assert preview.description == "Extracts tables from PDF files"
        assert preview.version == "1.0.0"
        assert preview.resources == {}
        assert preview.source_label == "upload:pdf-extractor.zip"

    def test_valid_zip_with_bundled_resources(self) -> None:
        data = _make_zip({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            "scripts/run.py": b"print('hello')",
            "references/notes.md": b"# notes",
        })
        importer = SkillPackageImporter()
        preview = importer.preview_upload("pdf-extractor.zip", data)
        assert preview.resources["scripts/run.py"] == "print('hello')"
        assert preview.resources["references/notes.md"] == "# notes"

    def test_common_prefix_is_stripped(self) -> None:
        data = _make_zip({
            "pdf-extractor-abc123/SKILL.md": _VALID_SKILL_MD.encode(),
            "pdf-extractor-abc123/scripts/run.py": b"print('hi')",
        })
        importer = SkillPackageImporter()
        preview = importer.preview_upload("archive.zip", data)
        assert preview.name == "pdf-extractor"
        assert preview.resources == {"scripts/run.py": "print('hi')"}

    def test_binary_resource_skipped_with_warning(self) -> None:
        data = _make_zip({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff\xfe",
        })
        importer = SkillPackageImporter()
        preview = importer.preview_upload("archive.zip", data)
        assert preview.resources == {}
        assert preview.skipped_binary_resources == ["assets/logo.png"]
        assert any("binary" in w for w in preview.warnings)

    def test_root_level_file_outside_scripts_references_assets_is_kept(self) -> None:
        """agentskills.io allows "any additional files or directories" — a
        root-level reference file (e.g. Anthropic's `pdf` skill ships
        `forms.md`/`reference.md` beside SKILL.md) must not be silently
        dropped just because it isn't under scripts/references/assets."""
        data = _make_zip({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            "forms.md": b"# extra reference doc",
            "ooxml/schema.xsd": b"<xsd/>",
        })
        importer = SkillPackageImporter()
        preview = importer.preview_upload("archive.zip", data)
        assert preview.resources["forms.md"] == "# extra reference doc"
        assert preview.resources["ooxml/schema.xsd"] == "<xsd/>"

    def test_ignores_dotfiles_pycache_and_git_directories(self) -> None:
        data = _make_zip({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            ".gitignore": b"*.pyc",
            ".git/config": b"[core]",
            "scripts/__pycache__/run.cpython-312.pyc": b"\x00binary",
            "scripts/run.py": b"print('hi')",
        })
        importer = SkillPackageImporter()
        preview = importer.preview_upload("archive.zip", data)
        assert preview.resources == {"scripts/run.py": "print('hi')"}

    def test_resource_path_over_the_length_limit_is_skipped_not_fatal(self) -> None:
        """`validate_resource_path` rejects an over-length path; the
        importer's collection loop treats that the same as a binary
        file — skip and log, never fail the whole preview over one bad
        entry from a third-party archive."""
        too_long = "assets/" + ("a" * 300) + ".txt"
        data = _make_zip({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            too_long: b"content",
            "assets/ok.txt": b"kept",
        })
        importer = SkillPackageImporter()
        preview = importer.preview_upload("archive.zip", data)
        assert preview.resources == {"assets/ok.txt": "kept"}

    def test_resources_exceeding_the_total_byte_budget_raise(self) -> None:
        # Each file stays under the per-file cap; the SUM crosses the
        # per-skill cap — proves the total check fires independently of
        # the per-file check.
        chunk = ("x" * (900 * 1024)).encode()
        data = _make_zip({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            "assets/a.bin": chunk,
            "assets/b.bin": chunk,
            "assets/c.bin": chunk,
        })
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="per-skill limit"):
            importer.preview_upload("archive.zip", data)

    def test_missing_skill_md_raises(self) -> None:
        data = _make_zip({"README.md": b"no skill here"})
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="SKILL.md"):
            importer.preview_upload("archive.zip", data)

    def test_empty_archive_raises(self) -> None:
        data = _make_zip({})
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="empty"):
            importer.preview_upload("archive.zip", data)

    def test_invalid_zip_raises(self) -> None:
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="[Nn]ot a valid zip"):
            importer.preview_upload("archive.zip", b"not a real zip file")

    def test_invalid_skill_md_frontmatter_raises(self) -> None:
        bad_md = "---\nname: Not Valid Name!\ndescription: x\n---\nbody"
        data = _make_zip({"SKILL.md": bad_md.encode()})
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="Invalid SKILL.md"):
            importer.preview_upload("archive.zip", data)

    def test_non_utf8_skill_md_raises(self) -> None:
        data = _make_zip({"SKILL.md": b"\xff\xfe\x00\x01not utf8"})
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="UTF-8"):
            importer.preview_upload("archive.zip", data)

    def test_zip_slip_absolute_path_rejected(self) -> None:
        # zipfile.writestr allows crafting an entry with a path traversal name directly.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../etc/passwd", b"pwned")
            zf.writestr("SKILL.md", _VALID_SKILL_MD.encode())
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="unsafe path"):
            importer.preview_upload("archive.zip", buf.getvalue())

    def test_too_large_upload_rejected(self) -> None:
        importer = SkillPackageImporter()
        oversized = b"0" * (25 * 1024 * 1024 + 1)
        with pytest.raises(PackageImportError, match="too large"):
            importer.preview_upload("archive.zip", oversized)

    def test_tar_gz_upload(self) -> None:
        data = _make_tar({"SKILL.md": _VALID_SKILL_MD.encode()})
        importer = SkillPackageImporter()
        preview = importer.preview_upload("pdf-extractor.tar.gz", data)
        assert preview.name == "pdf-extractor"

    def test_content_sniff_when_no_extension_hint(self) -> None:
        # Zip magic bytes should be detected even with a hint-less filename/content-type.
        data = _make_zip({"SKILL.md": _VALID_SKILL_MD.encode()})
        importer = SkillPackageImporter()
        preview = importer.preview_upload("download", data)
        assert preview.name == "pdf-extractor"

_MANIFEST_URL = "https://registry.npmjs.org/pdf-extractor/latest"
_TARBALL_URL = "https://registry.npmjs.org/pdf-extractor/-/pdf-extractor-1.2.0.tgz"
_UNSAFE_URL_MESSAGE = "This URL is not allowed: it must point to a public address."


class _FakeFetcher(PublicUrlFetcher):
    """Serves a canned response, or raises a canned error, per URL."""

    def __init__(self, outcomes: dict[str, PublicFetchResponse | Exception]) -> None:
        super().__init__()
        self._outcomes = outcomes
        self.calls: list[tuple[str, PublicFetchLimits]] = []

    async def get(self, url: str, limits: PublicFetchLimits) -> PublicFetchResponse:
        self.calls.append((url, limits))
        outcome = self._outcomes[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(
    content: bytes = b"", status: int = 200, url: str = "https://example.com/"
) -> PublicFetchResponse:
    return PublicFetchResponse(url=url, status_code=status, headers={}, content=content)


def _manifest(tarball: str | None = _TARBALL_URL) -> PublicFetchResponse:
    dist = {"tarball": tarball} if tarball else {}
    return _response(json.dumps({"version": "1.2.0", "dist": dist}).encode())


def _make_deflated_zip(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, content)
    return buf.getvalue()


class TestExtractionBudget:
    def test_zip_bomb_is_rejected_once_extracted_bytes_exceed_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(package_importer, "_MAX_EXTRACTED_BYTES", 64 * 1024)
        data = _make_deflated_zip("assets/bomb.bin", b"\0" * (1024 * 1024))
        with pytest.raises(PackageImportError, match="too large once extracted"):
            SkillPackageImporter().preview_upload("bomb.zip", data)

    def test_zip_bomb_with_understated_file_size_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(package_importer, "_MAX_EXTRACTED_BYTES", 64 * 1024)
        data = bytearray(_make_deflated_zip("assets/bomb.bin", b"\0" * (1024 * 1024)))
        central_dir = data.rfind(b"PK\x01\x02")
        # Uncompressed size field of the central directory entry: claim 16 bytes.
        data[central_dir + 24 : central_dir + 28] = (16).to_bytes(4, "little")
        with pytest.raises(PackageImportError):
            SkillPackageImporter().preview_upload("bomb.zip", bytes(data))

    def test_tar_total_extracted_size_is_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(package_importer, "_MAX_EXTRACTED_BYTES", 1000)
        data = _make_tar({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            "scripts/a.txt": b"a" * 600,
            "scripts/b.txt": b"b" * 600,
        })
        with pytest.raises(PackageImportError, match="too large once extracted"):
            SkillPackageImporter().preview_upload("pack.tar.gz", data)


class TestPreviewNpm:
    async def test_successful_import(self) -> None:
        tarball = _make_tar({"package/SKILL.md": _VALID_SKILL_MD.encode()})
        fetcher = _FakeFetcher({_MANIFEST_URL: _manifest(), _TARBALL_URL: _response(tarball)})

        preview = await SkillPackageImporter(fetcher).preview_npm(
            PackageSpec(name="pdf-extractor", version="latest")
        )

        assert preview.name == "pdf-extractor"
        assert preview.source_label == "npm:pdf-extractor@1.2.0"
        assert [url for url, _ in fetcher.calls] == [_MANIFEST_URL, _TARBALL_URL]

    async def test_404_raises_not_found(self) -> None:
        url = "https://registry.npmjs.org/does-not-exist/latest"
        fetcher = _FakeFetcher({url: _response(status=404)})
        with pytest.raises(PackageImportError, match="was not found on the npm registry"):
            await SkillPackageImporter(fetcher).preview_npm(PackageSpec(name="does-not-exist"))

    async def test_registry_server_error_reports_status(self) -> None:
        fetcher = _FakeFetcher({_MANIFEST_URL: _response(status=500)})
        with pytest.raises(PackageImportError) as exc_info:
            await SkillPackageImporter(fetcher).preview_npm(PackageSpec(name="pdf-extractor"))
        assert str(exc_info.value) == "Could not download the npm package metadata (HTTP 500)."

    async def test_invalid_manifest_json_raises(self) -> None:
        fetcher = _FakeFetcher({_MANIFEST_URL: _response(b"<html>")})
        with pytest.raises(PackageImportError, match="invalid entry"):
            await SkillPackageImporter(fetcher).preview_npm(PackageSpec(name="pdf-extractor"))

    async def test_missing_tarball_raises(self) -> None:
        fetcher = _FakeFetcher({_MANIFEST_URL: _manifest(tarball=None)})
        with pytest.raises(PackageImportError, match="no downloadable tarball"):
            await SkillPackageImporter(fetcher).preview_npm(PackageSpec(name="pdf-extractor"))

    async def test_network_error_is_generic_and_leaks_no_detail(self) -> None:
        fetcher = _FakeFetcher({_MANIFEST_URL: PublicFetchError("GET failed: ConnectError: 10.0.0.3 refused")})
        with pytest.raises(PackageImportError) as exc_info:
            await SkillPackageImporter(fetcher).preview_npm(PackageSpec(name="pdf-extractor"))
        assert str(exc_info.value) == "Could not download the npm package metadata."

    async def test_oversized_tarball_rejected(self) -> None:
        fetcher = _FakeFetcher({
            _MANIFEST_URL: _manifest(),
            _TARBALL_URL: ResponseTooLargeError("Response body exceeds 26214400 bytes"),
        })
        with pytest.raises(PackageImportError, match="too large"):
            await SkillPackageImporter(fetcher).preview_npm(PackageSpec(name="pdf-extractor"))
        assert fetcher.calls[1][1].max_bytes == 25 * 1024 * 1024


class TestPreviewUrl:
    async def test_rejects_non_http_scheme(self) -> None:
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="Only http"):
            await importer.preview_url("ftp://example.com/skill.zip")

    async def test_successful_zip_import(self) -> None:
        url = "https://example.com/skill.zip"
        data = _make_zip({"SKILL.md": _VALID_SKILL_MD.encode()})
        importer = SkillPackageImporter(_FakeFetcher({url: _response(data)}))
        preview = await importer.preview_url(url)
        assert preview.name == "pdf-extractor"
        assert preview.source_label == "url:https://example.com/skill.zip"

    async def test_unsafe_url_maps_to_generic_message(self) -> None:
        url = "https://internal.corp.example/skill.zip"
        blocked = UnsafeUrlError("Blocked unsafe URL: hostname 'internal.corp.example' resolves to 10.0.0.5")
        importer = SkillPackageImporter(_FakeFetcher({url: blocked}))
        with pytest.raises(PackageImportError) as exc_info:
            await importer.preview_url(url)
        assert str(exc_info.value) == _UNSAFE_URL_MESSAGE

    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1:8088/health", "http://169.254.169.254/latest/meta-data"]
    )
    async def test_private_literal_is_blocked_by_the_real_fetcher(self, url: str) -> None:
        with pytest.raises(PackageImportError) as exc_info:
            await SkillPackageImporter().preview_url(url)
        assert str(exc_info.value) == _UNSAFE_URL_MESSAGE

    async def test_http_error_status_is_reported(self) -> None:
        url = "https://example.com/skill.zip"
        importer = SkillPackageImporter(_FakeFetcher({url: _response(status=500)}))
        with pytest.raises(PackageImportError) as exc_info:
            await importer.preview_url(url)
        assert str(exc_info.value) == "Could not download the archive (HTTP 500)."

    async def test_too_many_redirects_is_generic(self) -> None:
        url = "https://example.com/skill.zip"
        importer = SkillPackageImporter(_FakeFetcher({url: TooManyRedirectsError("More than 3 redirects")}))
        with pytest.raises(PackageImportError) as exc_info:
            await importer.preview_url(url)
        assert str(exc_info.value) == "Could not download the archive."

    @pytest.mark.parametrize(
        "error", [UnsafeUrlError("blocked"), PublicFetchError("GET failed")], ids=["blocked", "failed"]
    )
    async def test_logged_url_carries_no_credentials(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        url = "https://user:hunter2@example.com/skill.zip?X-Amz-Signature=SECRET"
        logger = MagicMock()
        monkeypatch.setattr(package_importer, "logger", logger)
        with pytest.raises(PackageImportError):
            await SkillPackageImporter(_FakeFetcher({url: error})).preview_url(url)
        logged = " ".join(str(arg) for call in logger.method_calls for arg in call.args)
        assert "https://example.com/skill.zip" in logged
        assert "SECRET" not in logged
        assert "hunter2" not in logged


class TestNormalizeGitHubUrl:
    def test_github_repo_url_becomes_api_tarball(self) -> None:
        result = SkillPackageImporter._normalize_url("https://github.com/netresearch/jira-skill")
        assert result == "https://api.github.com/repos/netresearch/jira-skill/tarball"

    def test_github_repo_url_with_trailing_slash(self) -> None:
        result = SkillPackageImporter._normalize_url("https://github.com/acme/my-skill/")
        assert result == "https://api.github.com/repos/acme/my-skill/tarball"

    def test_github_repo_url_with_dot_git(self) -> None:
        result = SkillPackageImporter._normalize_url("https://github.com/acme/my-skill.git")
        assert result == "https://api.github.com/repos/acme/my-skill/tarball"

    def test_non_github_url_unchanged(self) -> None:
        url = "https://example.com/my-skill.tar.gz"
        assert SkillPackageImporter._normalize_url(url) == url

    def test_github_subpath_url_unchanged(self) -> None:
        url = "https://github.com/acme/repo/archive/refs/heads/main.tar.gz"
        assert SkillPackageImporter._normalize_url(url) == url

    async def test_github_url_downloads_from_api(self) -> None:
        api_url = "https://api.github.com/repos/acme/my-skill/tarball"
        data = _make_tar({"SKILL.md": _VALID_SKILL_MD.encode()})
        fetcher = _FakeFetcher({api_url: _response(data)})
        preview = await SkillPackageImporter(fetcher).preview_url("https://github.com/acme/my-skill")
        assert preview.name == "pdf-extractor"
        assert preview.source_label == "url:https://github.com/acme/my-skill"


class TestArchiveFormatComesFromTheBytes:
    async def test_zip_named_url_that_serves_a_tarball(self) -> None:
        url = "https://example.com/skill.zip"
        data = _make_tar({"SKILL.md": _VALID_SKILL_MD.encode()})
        preview = await SkillPackageImporter(_FakeFetcher({url: _response(data)})).preview_url(url)
        assert preview.name == "pdf-extractor"

    def test_upload_with_the_wrong_extension(self) -> None:
        data = _make_zip({"SKILL.md": _VALID_SKILL_MD.encode()})
        assert SkillPackageImporter().preview_upload("skill.tar.gz", data).name == "pdf-extractor"

    def test_uncompressed_tar(self) -> None:
        content = _VALID_SKILL_MD.encode()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name="SKILL.md")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        assert SkillPackageImporter().preview_upload("download", buf.getvalue()).name == "pdf-extractor"

    def test_tar_problems_are_not_masked_by_a_zip_retry(self) -> None:
        data = _make_tar({"../escape.sh": b"x", "SKILL.md": _VALID_SKILL_MD.encode()})
        with pytest.raises(PackageImportError, match="unsafe path"):
            SkillPackageImporter().preview_upload("skill.zip", data)

    def test_neither_zip_nor_tar(self) -> None:
        with pytest.raises(PackageImportError, match="Not a valid zip or tar/tgz archive"):
            SkillPackageImporter().preview_upload("skill.tar.gz", b"\x1f\x8bnot really gzip")
