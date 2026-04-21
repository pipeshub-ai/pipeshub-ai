"""Types for the AI model provider registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelCapability(str, Enum):
    """Capabilities that an AI model provider can support."""

    TEXT_GENERATION = "text_generation"
    EMBEDDING = "embedding"
    IMAGE_GENERATION = "image_generation"
    TTS = "tts"
    STT = "stt"
    VIDEO = "video"
    OCR = "ocr"
    REASONING = "reasoning"


# Maps registry capability names to the existing model-type bucket keys
# used in the KV store and CRUD APIs.
CAPABILITY_TO_MODEL_TYPE: dict[str, str] = {
    ModelCapability.TEXT_GENERATION.value: "llm",
    ModelCapability.EMBEDDING.value: "embedding",
    ModelCapability.OCR.value: "ocr",
    ModelCapability.REASONING.value: "reasoning",
    ModelCapability.IMAGE_GENERATION.value: "imageGeneration",
    ModelCapability.TTS.value: "tts",
    ModelCapability.STT.value: "stt",
    ModelCapability.VIDEO.value: "video",
}

MODEL_TYPE_TO_CAPABILITY: dict[str, str] = {v: k for k, v in CAPABILITY_TO_MODEL_TYPE.items()}


@dataclass
class AIModelField:
    """Schema for a single configuration field exposed to the frontend."""

    name: str
    display_name: str
    field_type: str = "TEXT"
    required: bool = True
    default_value: Any = ""
    placeholder: str = ""
    description: str = ""
    is_secret: bool = False
    options: list[dict[str, str]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "displayName": self.display_name,
            "fieldType": self.field_type,
            "required": self.required,
        }
        if self.default_value != "":
            result["defaultValue"] = self.default_value
        if self.placeholder:
            result["placeholder"] = self.placeholder
        if self.description:
            result["description"] = self.description
        if self.is_secret:
            result["isSecret"] = True
        if self.options:
            result["options"] = self.options
        if self.validation:
            result["validation"] = self.validation
        return result
