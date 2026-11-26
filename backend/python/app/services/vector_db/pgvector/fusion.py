"""Reciprocal-rank fusion for the pgvector provider: the shared one, which the Qdrant provider uses too."""

from __future__ import annotations

from app.services.vector_db.fusion import rrf

__all__ = ["rrf"]
