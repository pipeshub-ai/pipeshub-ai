"""A document batch of a classified record carries its class to the embedding server (``X-Parse-Class``)."""
from __future__ import annotations

import pytest

from app.capacity.classes import CostClass
from app.config.constants.ai_models import PARSE_CLASS_HEADER
from app.modules.parse_engine import pools
from app.utils.embedding_server_client import EmbeddingServerEmbeddings


def _client() -> EmbeddingServerEmbeddings:
    return EmbeddingServerEmbeddings(model="m", max_retries=0, timeout=1.0)


def test_a_batch_with_no_class_uses_the_plain_client_and_sends_no_class() -> None:
    c = _client()
    assert c._documents_inner() is c._inner
    assert PARSE_CLASS_HEADER not in (c._inner.default_headers or {})


@pytest.mark.parametrize("cls", list(CostClass))
def test_a_classified_batch_sends_its_class(cls: CostClass) -> None:
    c = _client()
    pools.set_class(cls)
    try:
        inner = c._documents_inner()
        assert inner is not c._inner
        assert inner.default_headers[PARSE_CLASS_HEADER] == cls.value
        assert c._documents_inner() is inner  # built once per class
    finally:
        pools.set_class(None)


def test_a_search_query_never_carries_a_class() -> None:
    c = _client()
    pools.set_class(CostClass.LARGE)
    try:
        assert PARSE_CLASS_HEADER not in (c._query_inner.default_headers or {})
    finally:
        pools.set_class(None)
