"""Whether two references name the same embedding model, whoever serves it."""

from __future__ import annotations


def normalize_embedding_model_name(name: str | None) -> str | None:
    """Normalize an embedding model name so the *same* model is comparable across providers.

    The identical underlying model is referenced differently depending on the
    serving provider, e.g. ``nomic-ai/nomic-embed-text`` (sentence-transformers)
    vs ``nomic-embed-text`` (Ollama), or ``models/text-embedding-004`` (Gemini).
    These all produce the same embeddings/dimension, so switching the provider
    for the same model must NOT be treated as a breaking model change.

    We lowercase and strip any provider/org namespace prefix (the part before the
    last ``/``, which also covers the ``models/`` prefix).
    """
    if name is None:
        return None
    normalized = name.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized


def same_embedding_model(stored: str | None, current: str) -> bool:
    """Whether two ``provider:model:dimension`` fingerprints are the same model at the same dimension
    (and ``@`` precision), whichever provider serves it. A model name may itself hold colons."""
    return stored is not None and _model_and_dimension(stored) == _model_and_dimension(current)


def _model_and_dimension(fingerprint: str) -> tuple[str | None, str]:
    _, _, rest = fingerprint.partition(":")
    model, separator, dimension = rest.rpartition(":")
    if not separator:
        return None, fingerprint
    return normalize_embedding_model_name(model), dimension
