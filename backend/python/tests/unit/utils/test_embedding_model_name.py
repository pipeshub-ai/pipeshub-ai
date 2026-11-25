from __future__ import annotations

import pytest

from app.utils.embedding_model_name import (
    normalize_embedding_model_name,
    same_embedding_model,
)


@pytest.mark.parametrize(("name", "expected"), [
    (None, None),
    ("text-embedding-3-small", "text-embedding-3-small"),
    ("azure/text-embedding-3-small", "text-embedding-3-small"),
    ("models/text-embedding-004", "text-embedding-004"),
    (" BAAI/bge-large-en-v1.5 ", "bge-large-en-v1.5"),
])
def test_normalize(name: str | None, expected: str | None) -> None:
    assert normalize_embedding_model_name(name) == expected


@pytest.mark.parametrize(("stored", "current"), [
    ("azureOpenAI:text-embedding-3-small:1536", "openAICompatible:azure/text-embedding-3-small:1536"),
    ("default:BAAI/bge-large-en-v1.5:1024", "openAICompatible:bge-large-en-v1.5:1024"),
    ("ollama:nomic-embed-text:latest:768", "openAICompatible:nomic-embed-text:latest:768"),
    ("default:BAAI/bge-large-en-v1.5:1024@int8-w", "default:BAAI/bge-large-en-v1.5:1024@int8-w"),
])
def test_the_provider_is_not_part_of_the_model(stored: str, current: str) -> None:
    assert same_embedding_model(stored, current)


@pytest.mark.parametrize(("stored", "current"), [
    (None, "openAI:text-embedding-3-small:1536"),
    ("openAI:text-embedding-3-small:1536", "openAI:text-embedding-ada-002:1536"),
    ("openAI:text-embedding-3-small:1536", "openAI:text-embedding-3-small:512"),
    ("default:BAAI/bge-large-en-v1.5:1024", "default:BAAI/bge-large-en-v1.5:1024@int8-w"),
    ("ollama:nomic-embed-text:v1:768", "ollama:nomic-embed-text:v1.5:768"),
    ("unreadable", "openAI:text-embedding-3-small:1536"),
])
def test_another_model_dimension_or_precision_is_different(stored: str | None, current: str) -> None:
    assert not same_embedding_model(stored, current)
