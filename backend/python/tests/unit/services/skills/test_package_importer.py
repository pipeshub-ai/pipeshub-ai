"""Tests for app.services.skills.package_importer — archive extraction,
zip-slip guards, and the three preview sources (npm / URL / upload).

Network calls are always mocked (via a stubbed httpx.AsyncClient) — these
tests never hit the real npm registry or any external URL.
"""
import io
import tarfile
import zipfile
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.skills.npm_command_parser import PackageSpec
from app.services.skills.package_importer import (
    ImportPreview,
    PackageImportError,
    SkillPackageImporter,
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

    def test_ignores_files_outside_resource_kinds(self) -> None:
        data = _make_zip({
            "SKILL.md": _VALID_SKILL_MD.encode(),
            "README.md": b"not a resource kind",
        })
        importer = SkillPackageImporter()
        preview = importer.preview_upload("archive.zip", data)
        assert preview.resources == {}

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


class TestPreviewNpm:
    @pytest.mark.asyncio
    async def test_successful_import(self) -> None:
        tarball = _make_tar({"package/SKILL.md": _VALID_SKILL_MD.encode()})
        client = AsyncMock(spec=httpx.AsyncClient)

        meta_resp = MagicMock()
        meta_resp.status_code = 200
        meta_resp.raise_for_status = MagicMock()
        meta_resp.json = MagicMock(return_value={
            "version": "1.2.0",
            "dist": {"tarball": "https://registry.npmjs.org/pdf-extractor/-/pdf-extractor-1.2.0.tgz"},
        })

        tarball_resp = MagicMock()
        tarball_resp.raise_for_status = MagicMock()
        tarball_resp.content = tarball

        client.get = AsyncMock(side_effect=[meta_resp, tarball_resp])

        importer = SkillPackageImporter(http_client=client)
        preview = await importer.preview_npm(PackageSpec(name="pdf-extractor", version="latest"))

        assert preview.name == "pdf-extractor"
        assert preview.source_label == "npm:pdf-extractor@1.2.0"

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.status_code = 404
        client.get = AsyncMock(return_value=resp)

        importer = SkillPackageImporter(http_client=client)
        with pytest.raises(PackageImportError, match="not found"):
            await importer.preview_npm(PackageSpec(name="does-not-exist"))

    @pytest.mark.asyncio
    async def test_missing_tarball_raises(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"version": "1.0.0", "dist": {}})
        client.get = AsyncMock(return_value=resp)

        importer = SkillPackageImporter(http_client=client)
        with pytest.raises(PackageImportError, match="no downloadable tarball"):
            await importer.preview_npm(PackageSpec(name="pdf-extractor"))

    @pytest.mark.asyncio
    async def test_network_error_wrapped(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

        importer = SkillPackageImporter(http_client=client)
        with pytest.raises(PackageImportError, match="Failed to fetch"):
            await importer.preview_npm(PackageSpec(name="pdf-extractor"))

    @pytest.mark.asyncio
    async def test_oversized_tarball_rejected(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        meta_resp = MagicMock()
        meta_resp.status_code = 200
        meta_resp.raise_for_status = MagicMock()
        meta_resp.json = MagicMock(return_value={
            "version": "1.0.0",
            "dist": {"tarball": "https://registry.npmjs.org/pdf-extractor/-/pdf-extractor-1.0.0.tgz"},
        })
        tarball_resp = MagicMock()
        tarball_resp.raise_for_status = MagicMock()
        tarball_resp.content = b"0" * (25 * 1024 * 1024 + 1)
        client.get = AsyncMock(side_effect=[meta_resp, tarball_resp])

        importer = SkillPackageImporter(http_client=client)
        with pytest.raises(PackageImportError, match="too large"):
            await importer.preview_npm(PackageSpec(name="pdf-extractor"))


class TestPreviewUrl:
    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self) -> None:
        importer = SkillPackageImporter()
        with pytest.raises(PackageImportError, match="http"):
            await importer.preview_url("ftp://example.com/skill.zip")

    @pytest.mark.asyncio
    async def test_successful_zip_import(self) -> None:
        data = _make_zip({"SKILL.md": _VALID_SKILL_MD.encode()})
        client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = data
        resp.headers = {"content-type": "application/zip"}
        client.get = AsyncMock(return_value=resp)

        importer = SkillPackageImporter(http_client=client)
        preview = await importer.preview_url("https://example.com/skill.zip")
        assert preview.name == "pdf-extractor"
        assert preview.source_label == "url:https://example.com/skill.zip"

    @pytest.mark.asyncio
    async def test_http_error_wrapped(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))

        importer = SkillPackageImporter(http_client=client)
        with pytest.raises(PackageImportError, match="Failed to fetch"):
            await importer.preview_url("https://example.com/skill.zip")
