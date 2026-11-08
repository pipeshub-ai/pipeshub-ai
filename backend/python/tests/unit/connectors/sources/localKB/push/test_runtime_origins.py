"""A malformed ``KB_PUSH_SIGNED_URL_ORIGINS`` stops pushes with images only, never the push route."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.connectors.sources.localKB.push.api import PushRuntime
from app.connectors.sources.localKB.push.contract import PushRefusal

from .image_harness import image_body, make_harness, ref


def runtime() -> PushRuntime:
    return PushRuntime(worker=MagicMock(), spool=MagicMock(), admission=MagicMock())


@pytest.mark.parametrize("origins", ["*", "https://*.example.com", "http://s3.example.com", "https://s3.example.com/bucket", "https://user@s3.example.com"])
def test_a_malformed_origin_list_leaves_no_materialiser_and_does_not_raise(monkeypatch, origins) -> None:
    monkeypatch.setenv("KB_PUSH_SIGNED_URL_ORIGINS", origins)
    rt = runtime()
    assert rt.materialiser is None and rt.materialiser is None  # asked twice: still None, no exception
    assert rt.image_admission is not None


def test_a_valid_origin_list_builds_one_materialiser(monkeypatch) -> None:
    monkeypatch.setenv("KB_PUSH_SIGNED_URL_ORIGINS", "https://s3.example.com")
    rt = runtime()
    assert rt.materialiser is not None and rt.materialiser is rt.materialiser


async def test_a_push_with_images_is_503_when_the_materialiser_could_not_be_made(tmp_path) -> None:
    h = await make_harness(tmp_path)
    try:
        h.svc.materialiser = None  # what the runtime hands over for a malformed origin list
        with pytest.raises(PushRefusal) as e:
            await h.push(image_body([ref(1)]))
        assert (e.value.status, e.value.reason) == (503, "IMAGES_UNAVAILABLE")
        assert (await h.push(image_body([]))).status == "created"  # text-only is untouched
    finally:
        await h.store.stop()
