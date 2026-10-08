"""Per-deployment tuning for named-entity extraction.

Read from ``/services/namedEntities`` when present. Missing keys keep the
code defaults, so a deployment with no blob behaves the same as one that
never set the key.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.constants.service import config_node_constants
from app.modules.named_entities.domain.kinds import EntityKind, default_enabled_kinds

NAMED_ENTITIES_CONFIG_KEY = "/services/namedEntities"

logger = logging.getLogger(__name__)


class NamedEntityBudgets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_turns: int = 8
    max_tool_calls: int = 24
    max_input_tokens: int = 60_000
    max_output_tokens: int = 8_000
    wall_clock_seconds: float = 120.0
    max_llm_windows: int = 3
    max_entities: int = 300
    max_per_block: int = 40
    max_mentions_per_entity: int = 25
    max_text_chars: int = 256
    max_submit_batch: int = 100


class NamedEntityConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tz: str = "UTC"
    enabled_kinds: list[str] = Field(default_factory=list)
    budgets: NamedEntityBudgets = Field(default_factory=NamedEntityBudgets)

    def enabled(self) -> frozenset[EntityKind]:
        if not self.enabled_kinds:
            return default_enabled_kinds()
        parsed: set[EntityKind] = set()
        for raw in self.enabled_kinds:
            try:
                parsed.add(EntityKind(str(raw).casefold()))
            except ValueError:
                continue
        return frozenset(parsed) or default_enabled_kinds()


async def load_named_entity_config(config_service) -> NamedEntityConfig:
    if config_service is None:
        return NamedEntityConfig()
    try:
        raw = await config_service.get_config(
            config_node_constants.NAMED_ENTITIES.value
            if hasattr(config_node_constants, "NAMED_ENTITIES")
            else NAMED_ENTITIES_CONFIG_KEY,
            default={},
            use_cache=False,
        )
    except Exception:
        return NamedEntityConfig()
    if not isinstance(raw, dict) or not raw:
        return NamedEntityConfig()
    try:
        return NamedEntityConfig.model_validate(raw)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        logger.warning("named entity config rejected, using defaults; invalid fields=%s", fields)
        return NamedEntityConfig()
