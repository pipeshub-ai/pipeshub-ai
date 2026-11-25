"""UX1 LB-11: Labs stores only the flags an admin changed, so a Python reader of a flag nobody changed reads its
release default. That default must be Node's (the registry the Labs page shows and Node's readers use)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.featureflag.platform_settings import (
    PLATFORM_FLAG_DEFAULTS,
    is_actions_enabled,
    is_knowledge_publications_enabled,
    is_skills_enabled,
    is_soft_delete_enabled,
    is_user_context_enabled,
    read_platform_feature_flag,
)

REGISTRY = (
    Path(__file__).resolve().parents[5]
    / "nodejs/apps/src/modules/configuration_manager/constants/constants.ts"
)
_ENTRY = re.compile(r"key: '([A-Z_]+)',.*?defaultEnabled: (true|false)", re.S)


def node_defaults() -> dict[str, bool]:
    text = REGISTRY.read_text(encoding="utf-8")
    block = text[text.index("export const PLATFORM_FEATURE_FLAGS") :]
    return {key: value == "true" for key, value in _ENTRY.findall(block)}


def test_lb11_every_python_default_is_nodes() -> None:
    node = node_defaults()
    assert set(PLATFORM_FLAG_DEFAULTS) <= set(node), set(PLATFORM_FLAG_DEFAULTS) - set(node)
    assert {k: v for k, v in PLATFORM_FLAG_DEFAULTS.items() if node[k] != v} == {}


class _Config:
    def __init__(self, settings: object) -> None:
        self.settings = settings

    async def get_config(self, key: str, default: object = None, use_cache: bool = True) -> object:
        return self.settings


@pytest.mark.parametrize(
    "reader",
    [
        is_actions_enabled,
        is_skills_enabled,
        is_soft_delete_enabled,
        is_user_context_enabled,
        is_knowledge_publications_enabled,
    ],
)
async def test_lb11_a_flag_nobody_changed_reads_its_release_default(reader: object) -> None:
    from app.services.featureflag.config.config import CONFIG

    names = {
        is_actions_enabled: CONFIG.ENABLE_ACTIONS,
        is_skills_enabled: CONFIG.ENABLE_SKILLS,
        is_soft_delete_enabled: CONFIG.ENABLE_SOFT_DELETE,
        is_user_context_enabled: CONFIG.ENABLE_USER_CONTEXT,
        is_knowledge_publications_enabled: CONFIG.ENABLE_KNOWLEDGE_PUBLICATIONS,
    }
    overrides_only = {"featureFlagsVersion": 2, "featureFlags": {"SOME_OTHER_FLAG": True}}
    assert await reader(_Config(overrides_only)) is PLATFORM_FLAG_DEFAULTS[names[reader]]  # type: ignore[operator, index]


async def test_lb11_an_override_wins() -> None:
    settings = {"featureFlagsVersion": 2, "featureFlags": {"ENABLE_SOFT_DELETE": True}}
    assert await read_platform_feature_flag("ENABLE_SOFT_DELETE", _Config(settings)) is True
