"""Strict OpenAPI audit of POST /api/v1/skills/import/upload/preview.

The import routes share a 10 calls/minute limiter per user, so the calls run as a
disposable member.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest
from helper.second_user import SecondUser
from skills_audit_support import (
    RESOURCE_PATH,
    SkillsClient,
    request_as,
    skill_md,
    unique_skill_name,
)
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/skills/import/upload/preview"
PATH = "/import/upload/preview"
ARCHIVE_NAME = "spec-audit-skill.zip"
RESOURCE_TEXT = "Bundled by the skills spec audit.\n"


def _zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in members.items():
            archive.writestr(path, text)
    return buffer.getvalue()


def _tgz(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _upload(data: bytes, filename: str = ARCHIVE_NAME) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, data, "application/octet-stream")}


@pytest.mark.parametrize(
    ("filename", "pack"),
    [pytest.param(ARCHIVE_NAME, _zip, id="zip"), pytest.param("spec-audit-skill.tgz", _tgz, id="tgz")],
)
def test_upload_preview_returns_parsed_skill(import_user: SecondUser, filename: str, pack) -> None:
    name = unique_skill_name()
    content = skill_md(name)
    archive = pack({"SKILL.md": content, RESOURCE_PATH: RESOURCE_TEXT})

    resp = request_as(import_user, "POST", PATH, files=_upload(archive, filename))

    # Preview is stateless: nothing is persisted, so there is nothing to clean up.
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    preview = resp.json()
    assert preview["name"] == name
    assert preview["content"] == content
    assert preview["resources"] == {RESOURCE_PATH: RESOURCE_TEXT}
    assert preview["skippedBinaryResources"] == []
    assert preview["sourceLabel"] == f"upload:{filename}"


def test_upload_preview_without_token_is_unauthorized(skills_client: SkillsClient) -> None:
    resp = skills_client.post(PATH, auth=False, files=_upload(_zip({"SKILL.md": "x"})))
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_upload_preview_without_file_is_rejected_by_node(import_user: SecondUser) -> None:
    resp = request_as(import_user, "POST", PATH)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"detail": "A file upload is required (field 'file')"}
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"attachment": (ARCHIVE_NAME, b"x", "application/zip")}, id="wrong-field-name"),
        pytest.param(
            [("file", (ARCHIVE_NAME, b"x", "application/zip")), ("file", ("b.zip", b"y", "application/zip"))],
            id="two-files",
        ),
    ],
)
def test_upload_preview_unexpected_file_part_is_an_internal_error(import_user: SecondUser, files) -> None:
    # API bug: the upload middleware's "unexpected field" error is not mapped to a 400.
    resp = request_as(import_user, "POST", PATH, files=files)
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize(
    ("archive", "detail_start"),
    [
        pytest.param(b"plain text, not an archive", "Not a valid zip or tar/tgz archive.", id="not-an-archive"),
        pytest.param(_zip({"README.md": "# Not a skill\n"}), "No SKILL.md found in the archive.", id="no-skill-md"),
    ],
)
def test_upload_preview_rejects_archive(import_user: SecondUser, archive: bytes, detail_start: str) -> None:
    resp = request_as(import_user, "POST", PATH, files=_upload(archive))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["detail"].startswith(detail_start)
