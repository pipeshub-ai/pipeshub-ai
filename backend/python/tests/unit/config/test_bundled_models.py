"""The full image must bake the models the code falls back to, or the first use downloads them."""

from __future__ import annotations

from pathlib import Path

from app.config.constants.ai_models import DEFAULT_RERANKER_MODEL

_DOCKERFILE_BASE = Path(__file__).resolve().parents[5] / "Dockerfile.base"


def test_the_full_image_bakes_the_default_reranker() -> None:
    dockerfile = _DOCKERFILE_BASE.read_text()
    assert "ARG BUNDLE_RERANKER=1" in dockerfile
    assert f"CrossEncoder('{DEFAULT_RERANKER_MODEL}'" in dockerfile
