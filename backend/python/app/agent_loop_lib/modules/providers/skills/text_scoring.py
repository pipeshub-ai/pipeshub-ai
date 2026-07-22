from __future__ import annotations

from app.agent_loop_lib.core.text_scoring import keyword_overlap_score, tokenize
from app.agent_loop_lib.modules.providers.skills.base import SkillMetadata

"""Skill-specific search haystack, plus a re-export of the generic
tokenizer/scoring now shared with `tools/index.py` (see `core/text_scoring.py`
for the DRY rationale) — every existing `from ...skills.text_scoring import
tokenize, keyword_overlap_score` call site keeps working unchanged.
"""

__all__ = ["tokenize", "skill_haystack", "keyword_overlap_score"]


def skill_haystack(metadata: SkillMetadata) -> set[str]:
    """Every token a keyword search should match against for one skill —
    name, description, tags, concepts, category/subcategory."""
    return tokenize(
        f"{metadata.name} {metadata.description} {' '.join(metadata.tags)} "
        f"{' '.join(metadata.concepts)} {metadata.category or ''} {metadata.subcategory or ''}"
    )
