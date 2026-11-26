"""The org's live Azure model for the lanes that drive a real engine: its variables, its entry, and the answer a run
ended with.

Keys come from the dotenv file ``HARNESS_LIVE_ENV_FILE`` names, exported into this process only; never printed.
"""

from __future__ import annotations

import os
from typing import Any, cast

AZURE_KEY = "key-azure-live"
AZURE_REQUIRED = ("TEST_AZURE_OPENAI_API_KEY", "TEST_AZURE_OPENAI_ENDPOINT", "TEST_AZURE_OPENAI_DEPLOYMENT_NAME")


def load_live_env(path: str | None = None) -> None:
    """Export the Azure variables from the dotenv file ``HARNESS_LIVE_ENV_FILE`` names, for this process only.
    Values are never printed."""
    from tests.unit.harness.access.test_live_azure_gateway import (
        _dotenv,  # pyright: ignore[reportPrivateUsage]
    )

    source = path or os.environ.get("HARNESS_LIVE_ENV_FILE")
    if source and os.path.isfile(source):
        for name, value in _dotenv(source).items():
            if (name.startswith("TEST_AZURE_OPENAI_") or name == "EVAL_PROVIDER") and value and name not in os.environ:
                os.environ[name] = value
    os.environ.setdefault("EVAL_REASONING_MODEL", "1")


def azure_ready() -> bool:
    return all(os.environ.get(v) for v in AZURE_REQUIRED)


def azure_entry() -> dict[str, Any]:
    """The org's model entry for the Azure deployment the live variables name."""
    deployment = os.environ["TEST_AZURE_OPENAI_DEPLOYMENT_NAME"]
    model = os.environ.get("TEST_AZURE_OPENAI_MODEL") or deployment
    return {
        "provider": "azureOpenAI",
        "modelKey": AZURE_KEY,
        "isReasoning": True,
        "isDefault": True,
        "configuration": {
            "model": model,
            "apiKey": os.environ["TEST_AZURE_OPENAI_API_KEY"],
            "endpoint": os.environ["TEST_AZURE_OPENAI_ENDPOINT"],
            "deploymentName": deployment,
        },
    }


def final_answer(events: list[dict[str, Any]]) -> str:
    """The run's final assistant text as Node's resolved events carry it."""
    for event in reversed(events):
        data = cast("dict[str, Any]", event.get("data") or {})
        if event.get("type") == "message.completed" and data.get("role") == "assistant" and data.get("isFinal"):
            text = data.get("text")
            if isinstance(text, str):
                return text
        if event.get("type") == "x.pipeshub.answer.finalized" and isinstance(data.get("answer"), str):
            return cast("str", data["answer"])
    return ""


__all__ = ["AZURE_KEY", "AZURE_REQUIRED", "azure_entry", "azure_ready", "final_answer", "load_live_env"]
