"""Resolve semantic names inside one org and one kind. Value kinds are already keyed."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from app.modules.entity_resolution.gates import (
    digit_guard,
    entropy_allows_fuzzy,
    fuzzy_same,
    validate_candidate_id,
)
from app.modules.entity_resolution.models import ResolutionMode
from app.modules.named_entities.domain.kinds import EntityKind, spec_for
from app.modules.named_entities.domain.models import NamedEntity
from app.modules.named_entities.keys import named_entity_key

logger = logging.getLogger(__name__)

_MAX_SELECT = 30


class _SelectItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    id: str


class _SelectBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[_SelectItem]


@dataclass
class ResolvedEntity:
    entity: NamedEntity
    graph_key: str
    merged_into: str | None = None
    shadow_target: str | None = None


class NamedEntityResolver:
    def __init__(self, graph, *, vector_search=None, llm=None, mode: ResolutionMode = ResolutionMode.SHADOW) -> None:
        self._graph = graph
        self._vector_search = vector_search
        self._llm = llm
        self._mode = mode

    async def resolve(self, org_id: str, entities: list[NamedEntity]) -> list[ResolvedEntity]:
        resolved: list[ResolvedEntity] = []
        pending: list[NamedEntity] = []
        for entity in entities:
            spec = spec_for(entity.kind)
            key = named_entity_key(org_id, entity.kind.value, entity.norm_key)
            if spec is None or not spec.resolvable:
                resolved.append(ResolvedEntity(entity=entity, graph_key=key))
                continue
            pending.append(entity)
        by_kind: dict[EntityKind, list[NamedEntity]] = {}
        for entity in pending:
            by_kind.setdefault(entity.kind, []).append(entity)
        for kind, group in by_kind.items():
            resolved.extend(await self._resolve_kind(org_id, kind, group))
        return resolved

    async def _survivors(self, org_id: str, rows: list[dict]) -> dict[str, str]:
        """A merged node is never linked again: its mentions go to the survivor."""
        merged = [_node_id(row) for row in rows if row.get("mergedInto")]
        if not merged:
            return {}
        try:
            return await self._graph.resolve_named_entity_redirects(org_id, merged)
        except Exception:
            logger.warning("Named-entity redirect lookup failed for %d nodes", len(merged))
            return {}

    async def _resolve_kind(self, org_id: str, kind: EntityKind, group: list[NamedEntity]) -> list[ResolvedEntity]:
        keys = [entity.norm_key for entity in group]
        try:
            found = await self._graph.find_named_entities(org_id, kind.value, keys)
        except Exception:
            logger.warning("Named-entity lookup failed for kind %s", kind.value)
            found = []
        survivors = await self._survivors(org_id, found)
        by_norm = {row.get("normKey"): _node_id(row) for row in found}
        alias_index: dict[str, str] = {}
        for row in found:
            for alias in row.get("normalizedAliases") or []:
                alias_index[alias] = _node_id(row)
        out: list[ResolvedEntity] = []
        ambiguous: list[tuple[NamedEntity, list[dict]]] = []
        for entity in group:
            own = named_entity_key(org_id, kind.value, entity.norm_key)
            match = by_norm.get(entity.norm_key) or alias_index.get(entity.norm_key)
            survivor = survivors.get(match or "", match)
            if match and survivor != match:
                # A recorded merge is a decision already taken, so it holds in every mode.
                out.append(ResolvedEntity(entity=entity, graph_key=survivor, merged_into=survivor))
                continue
            if match:
                out.append(self._apply(entity, own, match))
                continue
            candidates = await self._candidates(org_id, kind, entity)
            gated = [c for c in candidates if self._gates(entity.display_name, c.get("name") or "")]
            same = next((c for c in gated if fuzzy_same(entity.display_name, c.get("name") or "")), None)
            if same:
                out.append(self._apply(entity, own, str(same.get("id") or own)))
                continue
            if gated and len(ambiguous) < _MAX_SELECT:
                ambiguous.append((entity, gated[:5]))
            else:
                out.append(ResolvedEntity(entity=entity, graph_key=own))
        if ambiguous and self._llm is not None and self._mode is not ResolutionMode.OFF:
            choices = await self._select(ambiguous)
            for entity, candidates in ambiguous:
                offered = {str(c.get("id")) for c in candidates if c.get("id")}
                chosen = validate_candidate_id(offered, choices.get(entity.display_name))
                own = named_entity_key(org_id, kind.value, entity.norm_key)
                out.append(self._apply(entity, own, chosen or own))
        else:
            for entity, _candidates in ambiguous:
                own = named_entity_key(org_id, kind.value, entity.norm_key)
                out.append(ResolvedEntity(entity=entity, graph_key=own))
        return out

    def _apply(self, entity: NamedEntity, own: str, target: str) -> ResolvedEntity:
        if target == own or self._mode is not ResolutionMode.APPLY:
            return ResolvedEntity(
                entity=entity,
                graph_key=own,
                shadow_target=None if target == own or self._mode is ResolutionMode.OFF else target,
            )
        return ResolvedEntity(entity=entity, graph_key=target, merged_into=target)

    def _gates(self, left: str, right: str) -> bool:
        if not digit_guard(left, right):
            return False
        if entropy_allows_fuzzy(left) and entropy_allows_fuzzy(right):
            return True
        return left.casefold().strip() == right.casefold().strip()

    async def _candidates(self, org_id: str, kind: EntityKind, entity: NamedEntity) -> list[dict]:
        if self._vector_search is None:
            return []
        try:
            rows = await self._vector_search(org_id, kind.value, entity.display_name)
        except Exception:
            logger.warning("Named-entity candidate search failed")
            return []
        checked = []
        for row in rows or []:
            node_id = str(row.get("id") or "")
            if not node_id or row.get("orgId") not in (None, org_id) or row.get("kind") not in (None, kind.value):
                continue
            checked.append(row)
        return checked[:5]

    async def _select(self, ambiguous: list[tuple[NamedEntity, list[dict]]]) -> dict[str, str]:
        lines = []
        for entity, candidates in ambiguous:
            options = ", ".join(f"{c.get('id')}={c.get('name')}" for c in candidates)
            snippet = entity.mentions[0].surface if entity.mentions else ""
            lines.append(f"{entity.display_name} | {snippet[:200]} | {options}")
        prompt = (
            "For each line pick a candidate id or NEW. "
            "Return JSON {\"decisions\": [{\"name\": \"...\", \"id\": \"...\"}]}.\n" + "\n".join(lines)
        )
        try:
            from langchain_core.messages import HumanMessage

            from app.utils.streaming import invoke_with_structured_output_and_reflection

            parsed = await invoke_with_structured_output_and_reflection(
                self._llm, [HumanMessage(content=prompt)], _SelectBatch
            )
        except Exception:
            logger.warning("Named-entity select call failed")
            return {}
        if parsed is None:
            return {}
        return {item.name: item.id for item in parsed.decisions}


def _node_id(row: dict) -> str:
    return str(row.get("_key") or row.get("id") or "")
