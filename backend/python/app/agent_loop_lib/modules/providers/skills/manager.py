from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillCandidate,
    SkillFilter,
    SkillMatch,
    SkillMetadata,
    SkillSource,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.governor import (
    AutoApproveGovernor,
    ManualReviewGovernor,
    SkillGovernor,
)
from app.agent_loop_lib.modules.providers.skills.store import SkillStore
from app.agent_loop_lib.modules.providers.skills.tracker import SkillUsageTracker
from app.agent_loop_lib.modules.providers.skills.validator import SkillValidator

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import AgentResult
    from app.agent_loop_lib.eval.decision_trace import DecisionTraceEntry
    from app.agent_loop_lib.modules.providers.skills.evaluator import SkillEvaluator
    from app.agent_loop_lib.modules.providers.skills.extractor import SkillExtractor
    from app.agent_loop_lib.modules.providers.skills.index import SkillIndex

"""`SkillManager` — the composition root / single authority for skills
(Layer analogous to `ContextManager`/`BudgetManager`): composes every ABC
below it, enforces validation on every write, keeps the index in sync, and
coordinates the learning loop. Nothing outside this module talks to
`SkillStore`/`SkillIndex`/`SkillUsageTracker` directly — tools and
middleware only ever call `SkillManager`.

**Sync catalog snapshot (prompt-path constraint):** `DefaultPromptBuilder.
build()` is synchronous and renders the skills overview every turn (see
agent/prompt.py — "cheap: string assembly, no I/O"). This manager therefore
keeps an in-memory read model (`self._catalog`, a `dict[str, SkillMetadata]`)
refreshed by `start()`/`refresh()` and kept current by every mutation, and
exposes the sync `catalog_snapshot()` for the prompt builder. Every other
operation is async.
"""

logger = logging.getLogger(__name__)

_CANDIDATES_SUBDIR = os.path.join("_meta", "candidates")


class SkillManagerConfig(BaseModel):
    skills_dir: str = "skills"                       # primary root: writable, owns _meta/
    extra_skills_dirs: list[str] = Field(default_factory=list)  # read-only roots
    auto_approve: bool = False                        # auto-approve learning-loop candidates
    write_approval: bool = False                      # require governance approval for all writes
    learning_enabled: bool = True
    max_candidates: int = 50
    catalog_render_limit: int = 40                     # above this, prompt shows categories + skill_search hint


def _default_governor(config: SkillManagerConfig) -> SkillGovernor:
    """`write_approval=True` always wins (even over `auto_approve=True`) —
    it's the stronger, "never auto-persist" knob."""
    if config.write_approval:
        return ManualReviewGovernor()
    return AutoApproveGovernor() if config.auto_approve else ManualReviewGovernor()


class SkillManager:
    def __init__(
        self,
        store: SkillStore,
        index: "SkillIndex",
        tracker: SkillUsageTracker,
        validator: SkillValidator | None = None,
        extractor: "SkillExtractor | None" = None,
        evaluator: "SkillEvaluator | None" = None,
        governor: SkillGovernor | None = None,
        config: SkillManagerConfig | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self._tracker = tracker
        self._validator = validator or SkillValidator()
        self._extractor = extractor
        self._evaluator = evaluator
        self._config = config or SkillManagerConfig()
        self._governor = governor or _default_governor(self._config)
        self._catalog: dict[str, SkillMetadata] = {}
        self._candidates_dir = os.path.join(os.path.abspath(self._config.skills_dir), _CANDIDATES_SUBDIR)

    @property
    def config(self) -> SkillManagerConfig:
        return self._config

    # ---- Lifecycle -------------------------------------------------------

    async def start(self) -> None:
        await self.refresh()

    async def refresh(self) -> None:
        """Rescan every root and rebuild the index + read model — the hook
        for a UI markdown editor or an out-of-process installer to make
        changes visible without a restart."""
        if hasattr(self._store, "refresh"):
            self._store.refresh()
        metadatas = await self._store.list_skills()
        self._catalog = {m.name: m for m in metadatas}
        await self._index.rebuild(metadatas)

    # ---- Progressive disclosure -------------------------------------------

    def catalog_snapshot(self) -> list[SkillMetadata]:
        """Tier 1, SYNC — read model only, for the prompt builder. Excludes
        DEPRECATED skills (they're still loadable by exact name, just not
        advertised — see `activate_skill`)."""
        return [m for m in self._catalog.values() if m.status != SkillStatus.DEPRECATED]

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        """Tier 2 — full body, on demand. Records the activation (for the
        usage tracker) whenever `session_id` is given."""
        skill = await self._store.get_skill(name)
        if skill is None:
            raise RegistryError(f"Skill {name!r} not found")
        if session_id is not None:
            await self._tracker.record_activation(name, session_id)
        return skill

    async def load_resource(self, name: str, path: str) -> str:
        """Tier 3 — a bundled resource file's contents."""
        content = await self._store.get_resource(name, path)
        if content is None:
            raise RegistryError(f"Resource {path!r} not found for skill {name!r}")
        return content

    # ---- Search ------------------------------------------------------------

    async def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
        subcategory: str | None = None,
        tags: list[str] | None = None,
        status: SkillStatus | None = None,
        source: SkillSource | None = None,
        limit: int = 10,
    ) -> list[SkillMatch]:
        filt = SkillFilter(
            query=query or None, category=category, subcategory=subcategory,
            tags=tags, status=status, source=source,
        )
        return await self._index.search(query, filt, limit=limit)

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:
        return await self._store.list_skills(filter)

    async def get_categories(self) -> dict[str, list[str]]:
        return await self._index.get_categories()

    async def get_tags(self) -> list[str]:
        return await self._index.get_tags()

    # ---- CRUD (delegates to store, validates, updates index + read model) --

    async def create(
        self, name: str, content: str, category: str | None = None, subcategory: str | None = None,
    ) -> SkillMetadata:
        metadata = await self._store.create_skill(name, content, category, subcategory)
        self._catalog[name] = metadata
        await self._index.add_entry(metadata)
        return metadata

    async def update(self, name: str, content: str) -> SkillMetadata:
        metadata = await self._store.update_skill(name, content)
        self._catalog[name] = metadata
        await self._index.update_entry(metadata)
        return metadata

    async def patch(self, name: str, old_string: str, new_string: str) -> bool:
        ok = await self._store.patch_skill(name, old_string, new_string)
        if ok:
            await self._resync_entry(name)
        return ok

    async def delete(self, name: str) -> bool:
        ok = await self._store.delete_skill(name)
        if ok:
            self._catalog.pop(name, None)
            await self._index.remove_entry(name)
        return ok

    async def deprecate(self, name: str, reason: str, replaced_by: str | None = None) -> bool:
        ok = await self._store.deprecate_skill(name, reason, replaced_by)
        if ok:
            await self._resync_entry(name)
            await self._governor.on_skill_deprecated(name, reason)
        return ok

    async def _resync_entry(self, name: str) -> None:
        skill = await self._store.get_skill(name)
        if skill is not None:
            self._catalog[name] = skill.metadata
            await self._index.update_entry(skill.metadata)

    # ---- Resource management -----------------------------------------------

    async def write_resource(self, name: str, path: str, content: str) -> bool:
        return await self._store.write_resource(name, path, content)

    async def remove_resource(self, name: str, path: str) -> bool:
        return await self._store.remove_resource(name, path)

    # ---- Learning loop -------------------------------------------------------

    async def learn_from_execution(
        self,
        result: "AgentResult",
        trajectory: dict | None = None,
        decision_trace: "list[DecisionTraceEntry] | None" = None,
        session_id: str | None = None,
    ) -> list[SkillCandidate]:
        """Extract, quality-gate, and route candidates from a finished run.
        Returns every candidate that passed evaluation — each already
        tagged `status="approved"` (governor cleared it for immediate
        persistence; the caller, typically the skill_learning middleware,
        spawns the skill_writer sub-agent to author + persist the final
        SKILL.md via `skill_manage`, preserving "everything via tool calls"
        for the actual write) or `status="pending"` (already written to
        `_meta/candidates/` for human review via `get_pending_candidates`/
        `approve_candidate`/`reject_candidate`)."""
        if not self._config.learning_enabled or self._extractor is None:
            return []
        if not await self._extractor.should_extract(result):
            return []

        raw_candidates = await self._extractor.extract_candidates(
            result, trajectory, decision_trace, self.catalog_snapshot(),
        )
        accepted: list[SkillCandidate] = []
        for candidate in raw_candidates[: self._config.max_candidates]:
            candidate.source_session_id = session_id
            if self._evaluator is not None:
                passed, feedback = await self._evaluator.evaluate_candidate(candidate)
                if not passed:
                    logger.info("skill_learning: candidate %r rejected: %s", candidate.name, feedback)
                    continue

            if await self._governor.should_approve(candidate):
                candidate.status = "approved"
            else:
                candidate.status = "pending"
                await self.queue_candidate(candidate)
            accepted.append(candidate)
        return accepted

    async def queue_candidate(self, candidate: SkillCandidate) -> None:
        os.makedirs(self._candidates_dir, exist_ok=True)
        path = os.path.join(self._candidates_dir, f"{candidate.candidate_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(candidate.model_dump(mode="json"), f, indent=2)

    async def get_pending_candidates(self) -> list[SkillCandidate]:
        if not os.path.isdir(self._candidates_dir):
            return []
        candidates: list[SkillCandidate] = []
        for filename in sorted(os.listdir(self._candidates_dir)):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(self._candidates_dir, filename), "r", encoding="utf-8") as f:
                candidates.append(SkillCandidate(**json.load(f)))
        return candidates

    async def approve_candidate(self, candidate_id: str) -> SkillMetadata:
        candidate = await self._find_candidate(candidate_id)
        if candidate is None:
            raise RegistryError(f"Candidate {candidate_id!r} not found")
        content = _candidate_to_skill_md(candidate)
        metadata = await self.create(candidate.name, content, candidate.category, candidate.subcategory)
        self._remove_candidate_file(candidate_id)
        await self._governor.on_skill_created(metadata)
        return metadata

    async def reject_candidate(self, candidate_id: str) -> None:
        self._remove_candidate_file(candidate_id)

    async def _find_candidate(self, candidate_id: str) -> SkillCandidate | None:
        for candidate in await self.get_pending_candidates():
            if candidate.candidate_id == candidate_id:
                return candidate
        return None

    def _remove_candidate_file(self, candidate_id: str) -> None:
        path = os.path.join(self._candidates_dir, f"{candidate_id}.json")
        if os.path.isfile(path):
            os.remove(path)

    # ---- Usage tracking --------------------------------------------------

    async def record_activation(self, name: str, session_id: str) -> None:
        await self._tracker.record_activation(name, session_id)

    async def record_outcome(self, name: str, session_id: str, success: bool, notes: str = "") -> None:
        await self._tracker.record_outcome(name, session_id, success, notes)

    async def evaluate_skill_health(self, name: str) -> tuple[str, str]:
        """(action, reason) where action is "keep"/"refine"/"deprecate" —
        see `SkillEvaluator.evaluate_existing`. Returns ("keep", ...) when
        no evaluator is configured."""
        experience = await self._tracker.get_experience(name)
        if self._evaluator is None:
            return "keep", "no evaluator configured"
        return await self._evaluator.evaluate_existing(experience)


def _candidate_to_skill_md(candidate: SkillCandidate) -> str:
    """Render an approved/human-approved `SkillCandidate` into a full
    SKILL.md string, tagging its provenance as agent-created."""
    metadata = SkillMetadata(
        name=candidate.name,
        description=candidate.description,
        category=candidate.category,
        subcategory=candidate.subcategory,
        tags=list(candidate.tags),
        source=SkillSource.AGENT_CREATED,
        created_at=candidate.created_at,
    )
    import yaml
    frontmatter_yaml = yaml.safe_dump(metadata.to_frontmatter_dict(), sort_keys=False)
    return f"---\n{frontmatter_yaml}---\n\n{candidate.body.strip()}\n"
