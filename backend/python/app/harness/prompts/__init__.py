"""PipesHub's released prompt versions, as data (P4.7)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from harness_core.models.prompts import InMemoryPromptRegistry

PROMPTS = Path(__file__).with_name("prompts.json")


@cache
def _document() -> dict[str, object]:
    return json.loads(PROMPTS.read_text(encoding="utf-8"))


def pipeshub_prompt_registry() -> InMemoryPromptRegistry:
    """A fresh registry holding the released prompt versions."""
    return InMemoryPromptRegistry.from_document(_document())


__all__ = ["PROMPTS", "pipeshub_prompt_registry"]
