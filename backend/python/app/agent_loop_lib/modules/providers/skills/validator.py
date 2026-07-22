from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.agent_loop_lib.core.exceptions import AgentLoopError
from app.agent_loop_lib.modules.providers.skills.base import Skill

"""Deterministic, stateless spec enforcement for skills — extracted from the
original loader.py so both the loader (reading) and the store/manager
(writing) share exactly one source of truth for "is this a valid skill".

`SkillValidator` is a concrete class, not an ABC: unlike `SkillStore`/
`SkillIndex`/etc., there is no swap point here — validation rules are the
agentskills.io spec plus a couple of agent-loop-specific limits, not a
policy that should vary by backend. A future Hermes-style content/security
scanner (see the plan's "Future-Readiness" section) extends this class or
wraps it, rather than the codebase needing a second implementation today.
"""

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_BODY_LENGTH = 200_000  # generous cap against a runaway learning-loop write
MAX_CATEGORY_LENGTH = 64

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_CATEGORY_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Soft (warning-only) thresholds from agentskills.io ecosystem guidance —
# never block a save, just surface a lint hint in the editor (see
# `SkillValidator.lint`). Kept separate from the hard MAX_* limits above,
# which the store enforces unconditionally.
BODY_LINE_WARN_THRESHOLD = 500
BODY_TOKEN_WARN_THRESHOLD = 5000  # ~4 chars/token heuristic, no tokenizer dependency here
REFERENCE_DEPTH_WARN = 1  # "one level deep from SKILL.md" per spec guidance
_WORKFLOW_SUMMARY_HINTS = (
    "first,", "then,", "step 1", "step 2", "1)", "2)", "1.", "2.",
)


class SkillLintWarning(BaseModel):
    """One non-blocking spec-conformance hint — `code` is stable/machine-
    matchable (for the frontend to render a specific icon/action), `message`
    is the human-readable text."""

    code: str
    message: str


class SkillFormatError(AgentLoopError):
    """SKILL.md content (or an agent-loop extension field) violates spec."""


class SkillValidator:
    """Deterministic validation — no I/O, no LLM calls, no state."""

    def validate_name(self, name: object) -> None:
        if not isinstance(name, str) or not name:
            raise SkillFormatError("SKILL.md frontmatter requires a non-empty 'name'")
        if len(name) > MAX_NAME_LENGTH:
            raise SkillFormatError(f"Skill name {name!r} exceeds the {MAX_NAME_LENGTH}-character limit")
        if not _NAME_RE.match(name):
            raise SkillFormatError(
                f"Skill name {name!r} must be lowercase alphanumeric with single hyphens "
                "only (no leading/trailing/consecutive hyphens)"
            )

    def validate_description(self, description: object) -> None:
        if not isinstance(description, str) or not description:
            raise SkillFormatError("SKILL.md frontmatter requires a non-empty 'description'")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise SkillFormatError(
                f"Skill description exceeds the {MAX_DESCRIPTION_LENGTH}-character limit "
                f"({len(description)})"
            )

    def validate_category(self, category: str | None) -> None:
        if category is None:
            return
        if len(category) > MAX_CATEGORY_LENGTH or not _CATEGORY_RE.match(category):
            raise SkillFormatError(
                f"Category {category!r} must be lowercase alphanumeric with single hyphens only "
                f"(max {MAX_CATEGORY_LENGTH} chars)"
            )

    def validate_body(self, body: object) -> None:
        if not isinstance(body, str) or not body.strip():
            raise SkillFormatError("SKILL.md body must be non-empty")
        if len(body) > MAX_BODY_LENGTH:
            raise SkillFormatError(f"SKILL.md body exceeds the {MAX_BODY_LENGTH}-character limit")

    def validate_frontmatter(self, data: dict[str, Any]) -> None:
        """Structural validation of a raw parsed-YAML frontmatter dict —
        called by the loader right after `yaml.safe_load`, before a `Skill`
        is even constructed."""
        self.validate_name(data.get("name"))
        self.validate_description(data.get("description"))

    def validate_skill(self, skill: Skill, *, expected_name: str | None = None) -> None:
        """Full validation of an assembled `Skill` — called by the store
        before any create/update touches disk."""
        self.validate_name(skill.metadata.name)
        self.validate_description(skill.metadata.description)
        self.validate_body(skill.body)
        self.validate_category(skill.metadata.category)
        self.validate_category(skill.metadata.subcategory)
        if expected_name is not None and skill.metadata.name != expected_name:
            raise SkillFormatError(
                f"Skill 'name' ({skill.metadata.name!r}) must match its directory name "
                f"({expected_name!r})"
            )

    def lint(self, skill: Skill) -> list[SkillLintWarning]:
        """Non-blocking agentskills.io conformance hints — called by the
        `/skills/validate` route (and, before that, by the editor's save
        pipeline) IN ADDITION TO `validate_skill`, never instead of it: a
        warning never prevents a save, but `validate_skill`'s
        `SkillFormatError` always does. Deterministic and cheap (no I/O,
        no LLM) by the same design constraint as the rest of this class."""
        warnings: list[SkillLintWarning] = []
        body = skill.body

        line_count = body.count("\n") + 1
        if line_count > BODY_LINE_WARN_THRESHOLD:
            warnings.append(SkillLintWarning(
                code="body_too_long",
                message=(
                    f"Body is {line_count} lines — agentskills.io guidance recommends staying "
                    f"under {BODY_LINE_WARN_THRESHOLD} lines; move detail into a bundled "
                    "reference file instead."
                ),
            ))
        approx_tokens = len(body) // 4
        if approx_tokens > BODY_TOKEN_WARN_THRESHOLD:
            warnings.append(SkillLintWarning(
                code="body_token_estimate_high",
                message=(
                    f"Body is roughly {approx_tokens} tokens (~{BODY_TOKEN_WARN_THRESHOLD}+ is "
                    "the guidance ceiling) — consider splitting into resources loaded on demand."
                ),
            ))

        description_lower = skill.metadata.description.lower()
        if any(hint in description_lower for hint in _WORKFLOW_SUMMARY_HINTS):
            warnings.append(SkillLintWarning(
                code="description_summarizes_workflow",
                message=(
                    "Description reads like a step-by-step summary. Per Anthropic's testing, "
                    "models follow the description instead of reading the body — describe WHAT "
                    "the skill does and WHEN to use it, not HOW."
                ),
            ))

        for path in (*skill.resources.get("scripts", []), *skill.resources.get("references", []),
                     *skill.resources.get("assets", [])):
            if path.count("/") > REFERENCE_DEPTH_WARN:
                warnings.append(SkillLintWarning(
                    code="reference_nested_too_deep",
                    message=f"Resource {path!r} is nested more than one level deep from SKILL.md.",
                ))
                break

        return warnings
