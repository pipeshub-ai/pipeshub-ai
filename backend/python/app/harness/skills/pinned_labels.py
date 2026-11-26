"""The labels of the skills an engine run mounts (FOLLOWUPS1-1).

An engine mounts its pinned skill bundles in the sandbox workspace, where the engine may read any file at any time:
mounting is loading. A bundle an agent wrote carries the label of the run that wrote it (``SKILL.md``'s
``metadata.agent-loop.label``), so the mount journals it as ``skillLabels`` with the engine's start, and the engine
run's own-work label joins it (and through it the parent's, which joins the child's). A journal that cannot record
the label gets no such bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from harness_core.contracts import validate_contract
from harness_core.journal.types import SKILL_LABELS, NewEvent
from harness_core.skills import SKILL_ENTRYPOINT
from harness_core.skills.builder import pinned_content

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel, JsonValue

    from harness_core.journal.types import Claim
    from harness_core.logs import HarnessLog
    from harness_core.skills.types import SkillPin

__all__ = ["LISTED_DESCRIPTION_CHARS", "MountedSkills", "mounted_skills", "mounted_skills_instructions"]


# A description longer than this is cut in the listing: the engine reads the whole SKILL.md when it loads one.
LISTED_DESCRIPTION_CHARS = 240


@dataclass(frozen=True)
class MountedSkills:
    """The pins the engine mounts, the event journaling the labels they bring (``None``: they bring none), and
    each mounted skill's description as its pinned SKILL.md gives it ("" when unread)."""

    pins: tuple[SkillPin, ...]
    labels: NewEvent | None
    descriptions: tuple[tuple[str, str], ...] = ()


def mounted_skills_instructions(mounted: MountedSkills) -> str:
    """What the engine is told of its skills: one line each, name and description, and where to load one. The
    bodies stay in the skills folder, read when the task needs one (progressive disclosure, FIX-SKILLS)."""
    if not mounted.descriptions:
        return ""
    lines = [
        f"- {name}: {_cut(description)}" if description else f"- {name}"
        for name, description in sorted(mounted.descriptions)
    ]
    return (
        "\n\nSkills for this task are in your skills folder. When the task matches one, read its SKILL.md (with your "
        "skills tool, or ph skill show <name>) and follow it before you start:\n" + "\n".join(lines)
    )


def _cut(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= LISTED_DESCRIPTION_CHARS else flat[: LISTED_DESCRIPTION_CHARS - 1].rstrip() + "…"


async def _read(graph: object | None, org_id: str, pin: SkillPin) -> tuple[dict[str, Any] | None, str]:
    """The pinned content's label and description."""
    from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md
    from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
    from app.agents.agent_loop.skills.provenance import UNKNOWN_LABEL, content_label

    if graph is None:
        return dict(UNKNOWN_LABEL), ""
    try:
        files = await pinned_content(GraphSkillSource(graph), org_id, pin)  # type: ignore[arg-type]
        skill = parse_skill_md(files[SKILL_ENTRYPOINT].decode("utf-8"), expected_name=pin.name)
    except Exception:
        # The bundle the engine mounts is this content; unread, it cannot show it holds nothing a run wrote.
        return dict(UNKNOWN_LABEL), ""
    return content_label(skill.metadata), skill.metadata.description


async def mounted_skills(
    pins: Sequence[SkillPin], graph: object | None, claim: Claim, log: HarnessLog
) -> MountedSkills:
    from app.agents.agent_loop.skills.provenance import is_source_free

    kept: list[SkillPin] = []
    described: list[tuple[str, str]] = []
    entries: list[dict[str, Any]] = []
    for pin in pins:
        label, description = await _read(graph, claim.org_id, pin)
        if not is_source_free(label):
            if not claim.journal_accepts(SKILL_LABELS):
                log.info("skill.withheld", skill=pin.name, reason="journal_lacks_feature")
                continue
            entries.append({"skill": pin.name, "label": label})
        kept.append(pin)
        described.append((pin.name, description))
    if not entries:
        return MountedSkills(tuple(kept), None, tuple(described))
    data: dict[str, JsonValue] = {"skillLabels": cast("list[JsonValue]", entries)}
    model = EVENT_MODELS[CITATIONS_EVENT]
    _ = validate_contract(cast("type[BaseModel]", model.model_fields["data"].annotation), data)
    return MountedSkills(tuple(kept), NewEvent(CITATIONS_EVENT, data, critical=False), tuple(described))
