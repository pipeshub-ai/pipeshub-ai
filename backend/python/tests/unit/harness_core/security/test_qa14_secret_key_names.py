"""QA14-11: secret key names come from the shared vector; arguments shaped for another person mask their values."""

from __future__ import annotations

import json

import pytest

from harness_core.security.token_patterns import (
    SECRET_KEY_NAME,
    SECRET_MASK,
    redact_secrets_in,
)
from tests.support.token_vector import VECTOR_PATH

NAMES = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))["secretKeyNames"]


@pytest.mark.parametrize("key", NAMES["matches"])
def test_qa14_11_every_listed_secret_name_masks_its_value_whatever_it_looks_like(key: str) -> None:
    masked = redact_secrets_in({"title": "deploy", "nested": [{key: "hunter2"}]})
    assert masked == {"title": "deploy", "nested": [{key: SECRET_MASK}]}


@pytest.mark.parametrize("key", NAMES["ignores"])
def test_qa14_11_an_ordinary_name_keeps_its_value(key: str) -> None:
    assert SECRET_KEY_NAME.search(key) is None
    assert redact_secrets_in({key: "hunter2"}) == {key: "hunter2"}
