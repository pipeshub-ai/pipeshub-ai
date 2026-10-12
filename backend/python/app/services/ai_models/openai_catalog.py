"""OpenAI model-list shape for the models this organization has configured.

Clients that speak ``GET /v1/models`` (and ``GET /v1/models/{id}``) read this
payload. It carries model ids and which task each one serves. Credentials
stay in the encrypted config and are never copied here.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.utils.aimodels import iter_model_names

# Same bucket order as the Node ``AI_MODEL_TYPES`` constant.
_EXPOSED_TYPES: tuple[tuple[str, str], ...] = (
    ("llm", "chat"),
    ("embedding", "embedding"),
    ("ocr", "ocr"),
    ("slm", "chat"),
    ("reasoning", "chat"),
    ("multiModal", "chat"),
    ("imageGeneration", "image"),
    ("tts", "text-to-speech"),
    ("stt", "automatic-speech-recognition"),
)


def _created_seconds(entry: Mapping[str, Any]) -> int:
    for key in ("updatedAt", "createdAt"):
        raw = entry.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            stamp = int(raw)
            if stamp > 10_000_000_000:
                return stamp // 1000
            return stamp
    return 0


def _model_object(entry: Mapping[str, Any], model_id: str, task: str) -> dict[str, Any]:
    provider = entry.get("provider")
    owned_by = provider.strip() if isinstance(provider, str) and provider.strip() else "pipeshub"
    return {
        "id": model_id,
        "object": "model",
        "created": _created_seconds(entry),
        "owned_by": owned_by,
        "task": task,
        "root": model_id,
        "parent": None,
    }


def openai_model_list(ai_models: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return ``{"object": "list", "data": [...]}`` for every stored model name."""
    data: list[dict[str, Any]] = []
    stored = ai_models or {}
    for model_type, task in _EXPOSED_TYPES:
        entries = stored.get(model_type)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            configuration = entry.get("configuration")
            for model_id in iter_model_names(configuration if isinstance(configuration, Mapping) else None):
                data.append(_model_object(entry, model_id, task))
    return {"object": "list", "data": data}


def find_openai_model(ai_models: Mapping[str, Any] | None, model_id: str) -> dict[str, Any] | None:
    """First listed model with this id, or None."""
    wanted = model_id.strip()
    if not wanted:
        return None
    for item in openai_model_list(ai_models)["data"]:
        if item["id"] == wanted:
            return item
    return None


def openai_model_not_found(model_id: str) -> dict[str, Any]:
    return {
        "error": {
            "message": f"The model '{model_id}' does not exist",
            "type": "invalid_request_error",
            "param": "model",
            "code": "model_not_found",
        }
    }
