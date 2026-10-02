"""Stored record JSON keeps non-ASCII text as written, so grep over records matches it."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers.blob_storage import BlobStorage, _json_utf8_bytes

_TEXT = "Borgartún 27, 105 Reykjavík · 北京 · Москва"


def _make_bs() -> BlobStorage:
    bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
    bs.config_service.get_config = AsyncMock(
        side_effect=[
            {"scopedJwtSecret": "secret"},
            {"cm": {"endpoint": "http://localhost:3001"}},
            {"storageType": "local"},
        ]
    )
    return bs


def _session() -> AsyncMock:
    resp = AsyncMock()
    resp.status = 200
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = AsyncMock()
    session.put = MagicMock(return_value=resp)
    return session


async def _uploaded_record_bytes(record: dict) -> bytes:
    form = MagicMock()
    with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=_session()), \
         patch("app.modules.transformers.blob_storage.aiohttp.FormData", return_value=form):
        await _make_bs().update_record_buffer("org-1", "doc-1", record, "vr-1")
    file_calls = [c for c in form.add_field.call_args_list if c.args[0] == "file"]
    assert len(file_calls) == 1
    return file_calls[0].args[1]


def _record() -> dict:
    return {"block_containers": {"blocks": [{"data": _TEXT}]}}


@pytest.mark.asyncio
async def test_uploaded_record_keeps_non_ascii_text_readable() -> None:
    body = await _uploaded_record_bytes(_record())

    assert _TEXT.encode("utf-8") in body
    assert b"\\u00fa" not in body
    assert json.loads(body)["record"] == _record()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("grep") is None, reason="grep not installed")
async def test_grep_finds_non_ascii_word_in_stored_record(tmp_path: Path) -> None:
    stored = tmp_path / "record_vr-1.json"
    stored.write_bytes(await _uploaded_record_bytes(_record()))

    for word in ("Borgartún", "北京", "Москва"):
        result = subprocess.run(["grep", "-l", word, str(stored)], capture_output=True)
        assert result.returncode == 0, f"grep did not find {word!r}"


def test_lone_surrogate_falls_back_to_ascii_escapes() -> None:
    body = _json_utf8_bytes({"data": "broken \ud800 text"})

    assert b"\\ud800" in body
    assert json.loads(body) == {"data": "broken \ud800 text"}


def _session_cm(post_resp: AsyncMock | None = None) -> AsyncMock:
    session = AsyncMock()
    if post_resp is not None:
        session.post = MagicMock(return_value=post_resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _sent(record: dict) -> dict:
    return {"isCompressed": False, "record": record, "virtualRecordId": "vr-1"}


@pytest.mark.asyncio
async def test_next_version_size_matches_bytes_sent_to_cloud() -> None:
    bs = _make_bs()
    bs.config_service.get_config = AsyncMock(
        side_effect=[{"scopedJwtSecret": "secret"}, {"cm": {"endpoint": "http://localhost:3001"}}, {"storageType": "s3"}]
    )
    bs._get_signed_url = AsyncMock(return_value={"signedUrl": "https://x/y"})
    bs._upload_to_signed_url = AsyncMock(return_value=200)

    with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=_session_cm()):
        _, size = await bs.upload_next_version("org-1", "rec-1", "doc-1", _record(), "vr-1")

    assert size == len(json.dumps(_sent(_record())).encode("utf-8"))


@pytest.mark.asyncio
async def test_next_version_size_matches_bytes_stored_locally() -> None:
    bs = _make_bs()
    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"_id": "doc-1"})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    with patch("app.modules.transformers.blob_storage.aiohttp.ClientSession", return_value=_session_cm(resp)):
        _, size = await bs.upload_next_version("org-1", "rec-1", "doc-1", _record(), "vr-1")

    assert size == len(_json_utf8_bytes(_sent(_record())))
