"""DN-1, DN-2 (P2.2): the name a person sees for a configured model."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes.agent import _enrich_agent_models
from app.config.ai_models.display import UNAVAILABLE_MODEL, model_display_name

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/vectors/model-display-names.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["id"])
def test_dn1_vectors(case: dict[str, Any]) -> None:
    shown = model_display_name(case["entry"], case["modelName"])
    assert shown == case["display"]
    entry = case["entry"] or {}
    secrets = [entry.get("modelKey"), *((entry.get("configuration") or {}).get(k) for k in ("deploymentName", "apiKey"))]
    assert not any(s and s in shown for s in secrets)
    assert VECTORS["unavailable"] == UNAVAILABLE_MODEL


async def test_dn2_an_agents_models_carry_display_names_never_a_key() -> None:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"llm": [
        {"modelKey": "k1", "provider": "openAI", "modelFriendlyName": "Team GPT", "configuration": {"model": "gpt-4o, gpt-4o-mini"}},
    ]})
    agent: dict[str, Any] = {"models": ["k1_gpt-4o-mini", "gone-key-uuid"]}
    await _enrich_agent_models(agent, config, logging.getLogger("dn2"))
    shown = [m["modelFriendlyName"] for m in agent["models"]]
    assert shown == ["Team GPT (gpt-4o-mini)", UNAVAILABLE_MODEL]
    assert "gone-key-uuid" not in json.dumps(shown)
