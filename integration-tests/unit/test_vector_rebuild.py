"""The Labs rebuild helpers: settings written whole, model lists read right, 409s waited out.

The platform-settings document is replaced whole on every save, so a flag
change that drops the other flags turns them all back to their defaults. And a
job start that gave up on the first 409 would fail on the few seconds after
every upload when the product is still indexing.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from helper import vector_rebuild
from helper.vector_rebuild import (
    VECTOR_STORE_REBUILD_FLAG,
    EmbeddingModel,
    PlatformSettings,
    default_model,
    error_message,
    parse_embedding_models,
    start_vector_store_job,
)

pytestmark = pytest.mark.unit


def _response(status: int, body: Any) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return resp


def test_changing_one_flag_keeps_the_other_settings() -> None:
    settings = PlatformSettings.from_api(
        {"fileUploadMaxSizeBytes": 1234, "featureFlags": {"ENABLE_OTHER": True}}
    )
    changed = settings.with_flag(VECTOR_STORE_REBUILD_FLAG, True)
    assert changed.to_api() == {
        "fileUploadMaxSizeBytes": 1234,
        "featureFlags": {"ENABLE_OTHER": True, VECTOR_STORE_REBUILD_FLAG: True},
    }
    assert settings.flag(VECTOR_STORE_REBUILD_FLAG) is False


def test_settings_without_a_size_limit_are_refused_rather_than_saved_back() -> None:
    with pytest.raises(AssertionError):
        PlatformSettings.from_api({"featureFlags": {}})


def test_error_message_reads_each_gateway_shape() -> None:
    assert error_message(_response(400, {"error": {"status": "error", "message": "refused"}})) == "refused"
    assert error_message(_response(403, {"detail": "disabled"})) == "disabled"
    assert error_message(_response(400, {"message": "plain"})) == "plain"
    assert error_message(_response(502, b"<html>bad gateway</html>")) == "<html>bad gateway</html>"


def test_the_default_model_is_the_marked_one_else_the_first() -> None:
    body = {
        "models": [
            {"modelKey": "a", "provider": "azureOpenAI", "configuration": {"model": "m1"}, "isDefault": False},
            {"modelKey": "b", "provider": "default", "configuration": {"model": "m2"}, "isDefault": True},
        ]
    }
    models = parse_embedding_models(body)
    assert default_model(models) == EmbeddingModel("b", "default", "m2", True)
    assert default_model(models[:1]) == models[0]
    assert default_model([]) is None


def test_a_model_list_without_keys_is_an_error_not_an_empty_list() -> None:
    with pytest.raises(AssertionError):
        parse_embedding_models({"status": "success"})
    with pytest.raises(AssertionError):
        parse_embedding_models({"models": [{"provider": "openAI"}]})


def test_a_job_start_waits_out_409s(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([_response(409, {"detail": "busy"}), _response(409, {"detail": "busy"}), _response(202, {"accepted": True})])
    post = MagicMock(side_effect=lambda *_a, **_k: next(answers))
    monkeypatch.setattr(vector_rebuild, "post_vector_store_job", post)
    monkeypatch.setattr(vector_rebuild.time, "sleep", lambda _s: None)

    resp = start_vector_store_job(object(), "cleanup", timeout=60)  # type: ignore[arg-type]

    assert resp.status_code == 202
    assert post.call_count == 3


def test_a_409_that_outlasts_the_wait_is_returned_for_the_caller_to_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vector_rebuild, "post_vector_store_job", lambda *_a, **_k: _response(409, {"detail": "queued"}))
    monkeypatch.setattr(vector_rebuild.time, "sleep", lambda _s: None)

    resp = start_vector_store_job(object(), "reindex", timeout=0)  # type: ignore[arg-type]

    assert resp.status_code == 409
    assert error_message(resp) == "queued"
