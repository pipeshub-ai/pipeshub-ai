"""``EntityResolver``: map extracted taxonomy names to canonical per-org nodes.

Runs once per record, after classification and before any write, so the
blob, the graph and the entity vector points all see canonical names. See
the package docstring for the three tiers.

Failure policy: a vector-store or model failure never fails the record. The
names involved simply become new nodes and a counter is bumped. A graph
failure in the exact-match tier raises in ``APPLY`` mode, because the graph
write that follows would fail anyway; in ``SHADOW`` mode it is logged.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.languages import canonical_language
from app.modules.entity_resolution.models import (
    CATEGORY,
    LANGUAGE,
    MAX_ALIASES_PER_NODE,
    SUBCATEGORY_CHAIN,
    TOPIC,
    EntityResolution,
    ExtractedName,
    MergeDecision,
    MergeDecisions,
    ResolutionMode,
    ResolutionStats,
    ResolvedEntity,
    TaxonomyKind,
    WinnerCandidate,
)
from app.modules.entity_resolution.normalizer import (
    display_form,
    is_acceptable_name,
    normalize_name,
)
from app.modules.entity_resolution.prompt import build_prompt
from app.telemetry.modules import entity_resolution_metrics as metrics
from app.utils.llm import get_llm_for_role
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    import logging

    from langchain_core.language_models import BaseChatModel

    from app.config.configuration_service import ConfigurationService
    from app.models.blocks import SemanticMetadata
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.modules.transformers.transformer import TransformContext
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

LLM_ROLE = "indexing"


class EntityResolver:
    """Resolves one record's taxonomy names. Safe to share across records.

    Always on: the default mode is ``APPLY``. ``SHADOW`` and ``OFF`` exist for
    tests and for callers that construct the resolver themselves.
    """

    def __init__(
        self,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_provider: IGraphDBProvider,
        entity_vector_store: EntityVectorStore | None = None,
        *,
        mode: ResolutionMode = ResolutionMode.APPLY,
        max_aliases: int = MAX_ALIASES_PER_NODE,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider
        self.entity_vector_store = entity_vector_store
        self.mode = ResolutionMode(mode)
        self.max_aliases = max_aliases
        self._llm: BaseChatModel | None = None
        self._llm_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def resolve(self, ctx: TransformContext) -> EntityResolution | None:
        """Resolve ``ctx.record.semantic_metadata``.

        In ``APPLY`` mode the metadata is rewritten to canonical names and the
        resolution is attached to ``ctx.entity_resolution``. Returns the
        resolution (also in shadow mode) or ``None`` when nothing ran.
        """
        mode = self.mode
        if mode is ResolutionMode.OFF:
            return None
        record = ctx.record
        metadata = getattr(record, "semantic_metadata", None)
        org_id = getattr(record, "org_id", None) or ""
        if metadata is None or not org_id:
            return None

        started = time.monotonic()
        stats = ResolutionStats()
        try:
            resolution = await self._resolve(org_id, metadata, mode, stats)
        except Exception:
            if mode is ResolutionMode.SHADOW:
                self.logger.warning(
                    "entity_resolution shadow failed for record %s",
                    getattr(record, "id", "?"), exc_info=True,
                )
                return None
            raise
        stats.latency_ms = int((time.monotonic() - started) * 1000)
        metrics.record_latency(mode.value, time.monotonic() - started)

        if mode is ResolutionMode.APPLY:
            self._rewrite_metadata(metadata, resolution)
            ctx.entity_resolution = resolution
        else:
            self.logger.info(
                "entity_resolution shadow org=%s record=%s decisions=%s",
                org_id, getattr(record, "id", "?"),
                json.dumps(resolution.decisions_for_log(), ensure_ascii=False),
            )
        self.logger.info(
            "entity_resolution mode=%s org=%s record=%s %s",
            mode.value, org_id, getattr(record, "id", "?"),
            " ".join(f"{k}={v}" for k, v in stats.as_dict().items()),
        )
        return resolution

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    async def _resolve(
        self,
        org_id: str,
        metadata: SemanticMetadata,
        mode: ResolutionMode,
        stats: ResolutionStats,
    ) -> EntityResolution:
        resolution = EntityResolution(org_id=org_id, mode=mode, stats=stats)
        names = self._collect_names(metadata, stats)

        existing = await self._tier0(org_id, names)
        unresolved: list[ExtractedName] = []
        for name in names:
            node = existing.get((name.kind.collection, name.normalized))
            if node is None:
                if name.kind is LANGUAGE:
                    # Languages are a closed set mapped through the ISO table;
                    # a miss is simply a new per-org node, never a model call.
                    self._new_entity(org_id, resolution, name, name.display, name.normalized)
                else:
                    unresolved.append(name)
                continue
            stats.tier0_hits += 1
            entity = self._existing_entity(resolution, name.kind, node, decision="exact")
            self._attach(resolution, name, entity)

        winners = await self._tier1(org_id, unresolved, stats)
        decisions = await self._tier2(metadata, unresolved, winners, stats)
        await self._apply_decisions(org_id, resolution, unresolved, winners, decisions)

        self._record_outcomes(resolution)
        return resolution

    # ---- collection --------------------------------------------------

    def _collect_names(
        self, metadata: SemanticMetadata, stats: ResolutionStats
    ) -> list[ExtractedName]:
        """Clean and dedupe the resolvable names, in metadata order.

        Subcategories keep their chain semantics: a level is only kept when
        every level above it was kept, matching how the graph transformer
        links them.
        """
        names: list[ExtractedName] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: TaxonomyKind, raw: object) -> bool:
            if not isinstance(raw, str):
                return False
            stats.names_seen += 1
            normalized = normalize_name(raw)
            if not is_acceptable_name(normalized):
                stats.names_dropped += 1
                return False
            dedupe_key = (kind.collection, normalized)
            if dedupe_key in seen:
                stats.names_deduped += 1
                return False
            seen.add(dedupe_key)
            names.append(
                ExtractedName(
                    index=len(names),
                    kind=kind,
                    raw=raw,
                    display=display_form(raw),
                    normalized=normalized,
                )
            )
            return True

        categories = metadata.categories or []
        category_kept = bool(categories) and add(CATEGORY, categories[0])

        # The chain hangs off the category (the graph links level 1 to it),
        # so without a category no level is kept either.
        chain_values = (
            metadata.sub_category_level_1,
            metadata.sub_category_level_2,
            metadata.sub_category_level_3,
        ) if category_kept else ()
        for kind, value in zip(SUBCATEGORY_CHAIN, chain_values):
            if not value or not add(kind, value):
                break

        for topic in metadata.topics or []:
            add(TOPIC, topic)

        for raw in metadata.languages or []:
            if isinstance(raw, str):
                add(LANGUAGE, canonical_language(raw) or raw)
        return names

    # ---- tier 0 ------------------------------------------------------

    async def _tier0(
        self, org_id: str, names: list[ExtractedName]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        by_collection: dict[str, list[str]] = {}
        for name in names:
            by_collection.setdefault(name.kind.collection, []).append(name.normalized)
        return await self._lookup_existing(org_id, by_collection)

    async def _lookup_existing(
        self, org_id: str, by_collection: dict[str, list[str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for collection, normalized_names in by_collection.items():
            wanted = set(normalized_names)
            rows = await self.graph_provider.find_taxonomy_nodes(
                collection, org_id, sorted(wanted)
            )
            nodes: list[tuple[dict[str, Any], str | None, list[str]]] = []
            for row in rows or []:
                key = row.get("id") or row.get("_key")
                if not key:
                    continue
                node = {
                    "id": str(key),
                    "name": row.get("name") or str(key),
                    "aliases": [str(a) for a in (row.get("aliases") or []) if a],
                }
                normalized = row.get("normalizedName")
                alias_forms = [str(a) for a in (row.get("normalizedAliases") or []) if a]
                nodes.append((node, str(normalized) if normalized else None, alias_forms))
            # A node reached by its own name wins over one reached by an alias.
            for node, normalized, _alias_forms in nodes:
                if normalized in wanted:
                    found[(collection, normalized)] = node
            for node, _normalized, alias_forms in nodes:
                for alias in alias_forms:
                    if alias in wanted:
                        found.setdefault((collection, alias), node)
        return found

    # ---- tier 1 ------------------------------------------------------

    async def _tier1(
        self, org_id: str, unresolved: list[ExtractedName], stats: ResolutionStats
    ) -> dict[int, WinnerCandidate | None]:
        winners: dict[int, WinnerCandidate | None] = {n.index: None for n in unresolved}
        if self.entity_vector_store is None or not unresolved:
            return winners

        groups: dict[tuple[str, str | None], list[ExtractedName]] = {}
        for name in unresolved:
            groups.setdefault((name.kind.entity_type.value, name.kind.level), []).append(name)

        for (entity_type, level), group in groups.items():
            try:
                matches = await self.entity_vector_store.find_best_matches(
                    [n.display for n in group], org_id, entity_type, level=level,
                )
            except Exception:
                stats.vector_failures += 1
                metrics.record_fallback("vector_error", len(group))
                self.logger.warning(
                    "entity_resolution: winner lookup failed for %s/%s; treating %d "
                    "names as new", entity_type, level, len(group), exc_info=True,
                )
                continue
            for name, match in zip(group, matches):
                winner = self._winner_from_match(match, entity_type, level)
                if winner is not None:
                    stats.winners_offered += 1
                winners[name.index] = winner
        return winners

    @staticmethod
    def _winner_from_match(
        match: dict[str, Any] | None, entity_type: str, level: str | None
    ) -> WinnerCandidate | None:
        if not match:
            return None
        if match.get("entityType") != entity_type:
            return None
        if (match.get("level") or None) != level:
            return None
        entity_id = match.get("entityId")
        if not entity_id:
            return None
        return WinnerCandidate(
            entity_id=str(entity_id),
            name=str(match.get("name") or entity_id),
            aliases=tuple(str(a) for a in (match.get("aliases") or []) if a),
        )

    # ---- tier 2 ------------------------------------------------------

    def _needs_model(
        self, unresolved: list[ExtractedName], winners: dict[int, WinnerCandidate | None]
    ) -> bool:
        if any(winners.get(n.index) is not None for n in unresolved):
            return True
        per_kind: dict[str, int] = {}
        for name in unresolved:
            per_kind[name.kind.collection] = per_kind.get(name.kind.collection, 0) + 1
        return any(count >= 2 for count in per_kind.values())

    async def _tier2(
        self,
        metadata: SemanticMetadata,
        unresolved: list[ExtractedName],
        winners: dict[int, WinnerCandidate | None],
        stats: ResolutionStats,
    ) -> dict[int, MergeDecision]:
        if not unresolved or not self._needs_model(unresolved, winners):
            return {}
        stats.model_calls += 1
        prompt = build_prompt(metadata.summary, unresolved, winners)
        try:
            llm = await self._get_llm()
            response = await invoke_with_structured_output_and_reflection(
                llm, [HumanMessage(content=prompt)], MergeDecisions,
            )
        except Exception:
            response = None
            self._llm = None
            self.logger.warning("entity_resolution: merge model call raised", exc_info=True)
        if response is None:
            stats.model_failures += 1
            metrics.record_model_call("failed")
            metrics.record_fallback("model_error", len(unresolved))
            return {}
        metrics.record_model_call("ok")
        valid_indexes = {n.index for n in unresolved}
        decisions: dict[int, MergeDecision] = {}
        for decision in response.decisions:
            if decision.i in valid_indexes and decision.i not in decisions:
                decisions[decision.i] = decision
        return decisions

    async def _get_llm(self) -> BaseChatModel:
        if self._llm is not None:
            return self._llm
        async with self._llm_lock:
            if self._llm is None:
                self._llm, _ = await get_llm_for_role(
                    self.config_service, LLM_ROLE, reasoning_effort="low",
                )
        return self._llm

    # ---- decisions ---------------------------------------------------

    async def _apply_decisions(
        self,
        org_id: str,
        resolution: EntityResolution,
        unresolved: list[ExtractedName],
        winners: dict[int, WinnerCandidate | None],
        decisions: dict[int, MergeDecision],
    ) -> None:
        stats = resolution.stats
        by_index = {n.index: n for n in unresolved}
        parent: dict[int, int] = {n.index: n.index for n in unresolved}

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        # Pointer decisions first: they only group items, they never pick a node.
        merged_to_winner: dict[int, WinnerCandidate] = {}
        proposed_new: dict[int, str] = {}
        for name in unresolved:
            decision = decisions.get(name.index)
            if decision is None:
                proposed_new[name.index] = ""
                continue
            if decision.same and decision.same_as_item >= 0:
                other = by_index.get(decision.same_as_item)
                if other is None or other.index == name.index or other.kind != name.kind:
                    stats.rejected_decisions += 1
                    metrics.record_fallback("rejected_item")
                    proposed_new[name.index] = ""
                    continue
                union(name.index, other.index)
                continue
            if decision.same:
                winner = winners.get(name.index)
                if winner is not None and decision.target == winner.entity_id:
                    merged_to_winner[name.index] = winner
                    continue
                stats.rejected_decisions += 1
                metrics.record_fallback("rejected_target")
                proposed_new[name.index] = ""
                continue
            proposed_new[name.index] = decision.canonical_name or ""

        # Group members follow their component leader.
        components: dict[int, list[ExtractedName]] = {}
        for name in unresolved:
            components.setdefault(find(name.index), []).append(name)

        # Canonical display forms may name a node the raw spelling did not
        # match; look those up once, batched, before creating anything.
        proposals: dict[int, tuple[str, str]] = {}
        pending_lookup: dict[str, list[str]] = {}
        for leader, members in components.items():
            if any(m.index in merged_to_winner for m in members):
                continue
            head = min(members, key=lambda m: m.index)
            display, normalized = self._canonical_form(head, proposed_new.get(head.index, ""))
            proposals[leader] = (display, normalized)
            if (head.kind.collection, normalized) not in resolution.entries:
                pending_lookup.setdefault(head.kind.collection, []).append(normalized)
        looked_up = (
            await self._lookup_existing(org_id, pending_lookup) if pending_lookup else {}
        )

        for leader, group in sorted(components.items()):
            members = sorted(group, key=lambda m: m.index)
            head = members[0]
            winner_members = [m for m in members if m.index in merged_to_winner]
            if winner_members:
                winner = merged_to_winner[winner_members[0].index]
                if len({merged_to_winner[m.index].entity_id for m in winner_members}) > 1:
                    stats.rejected_decisions += 1
                    metrics.record_fallback("conflicting_targets")
                entity = self._existing_entity(
                    resolution, head.kind,
                    {"id": winner.entity_id, "name": winner.name, "aliases": list(winner.aliases)},
                    decision="merge",
                )
                stats.merges += len(winner_members)
                stats.in_record_merges += len(members) - len(winner_members)
                for member in members:
                    self._attach(resolution, member, entity)
                continue

            display, normalized = proposals[leader]
            known = resolution.entries.get((head.kind.collection, normalized))
            if known is None:
                node = looked_up.get((head.kind.collection, normalized))
                if node is not None:
                    known = self._existing_entity(
                        resolution, head.kind, node, decision="canonical_exact"
                    )
                    stats.tier0_hits += 1
            if known is not None:
                entity = known
                for member in members:
                    self._attach(resolution, member, entity)
            else:
                entity = self._new_entity(org_id, resolution, head, display, normalized)
                for member in members[1:]:
                    self._attach(resolution, member, entity)
            stats.in_record_merges += len(members) - 1

    def _new_entity(
        self,
        org_id: str,
        resolution: EntityResolution,
        name: ExtractedName,
        display: str,
        normalized: str,
    ) -> ResolvedEntity:
        entity = ResolvedEntity(
            kind=name.kind,
            key=taxonomy_node_key(org_id, name.kind.collection, normalized),
            name=display,
            normalized=normalized,
            is_new=True,
            decision="new",
        )
        resolution.add(entity)
        resolution.stats.new_nodes += 1
        self._attach(resolution, name, entity)
        return entity

    def _canonical_form(self, head: ExtractedName, canonical_name: str) -> tuple[str, str]:
        if canonical_name:
            display = display_form(canonical_name)
            normalized = normalize_name(display)
            if is_acceptable_name(normalized):
                return display, normalized
        return head.display, head.normalized

    # ---- helpers -----------------------------------------------------

    def _existing_entity(
        self,
        resolution: EntityResolution,
        kind: TaxonomyKind,
        node: dict[str, Any],
        *,
        decision: str,
    ) -> ResolvedEntity:
        name = str(node.get("name") or node["id"])
        normalized = normalize_name(name)
        entity = resolution.entries.get((kind.collection, normalized))
        if entity is None:
            entity = ResolvedEntity(
                kind=kind,
                key=str(node["id"]),
                name=name,
                normalized=normalized,
                is_new=False,
                decision=decision,
                aliases=[str(a) for a in (node.get("aliases") or []) if a],
            )
            resolution.add(entity)
        return entity

    def _attach(
        self, resolution: EntityResolution, name: ExtractedName, entity: ResolvedEntity
    ) -> None:
        stats = resolution.stats
        resolution.assignments[name.index] = entity
        entity.extracted_names.append(name.raw)
        if name.normalized == entity.normalized:
            return
        known = {normalize_name(a) for a in entity.aliases}
        if name.normalized in known:
            return
        if len(entity.aliases) >= self.max_aliases:
            stats.alias_cap_hits += 1
            metrics.record_fallback("alias_cap")
            return
        entity.aliases.append(name.display)
        entity.new_aliases.append(name.display)

    def _record_outcomes(self, resolution: EntityResolution) -> None:
        counts: dict[tuple[str, str], int] = {}
        for entity in resolution.entries.values():
            key = (entity.kind.entity_type.value, entity.decision)
            counts[key] = counts.get(key, 0) + len(entity.extracted_names)
        for (kind, outcome), count in counts.items():
            metrics.record_name_outcome(kind, outcome, count)
        metrics.record_name_outcome("any", "dropped", resolution.stats.names_dropped)

    # ---- apply -------------------------------------------------------

    def _rewrite_metadata(
        self, metadata: SemanticMetadata, resolution: EntityResolution
    ) -> None:
        """Replace extracted names with canonical ones, in place."""
        by_slot: dict[str, list[str]] = {}
        for index in sorted(resolution.assignments):
            entity = resolution.assignments[index]
            bucket = by_slot.setdefault(entity.kind.slot, [])
            if entity.name not in bucket:
                bucket.append(entity.name)

        metadata.categories = by_slot.get(CATEGORY.slot, [])
        chain = [by_slot.get(kind.slot, [None])[0] for kind in SUBCATEGORY_CHAIN]
        metadata.sub_category_level_1 = chain[0] or None
        metadata.sub_category_level_2 = (chain[1] if chain[0] else None) or None
        metadata.sub_category_level_3 = (chain[2] if chain[0] and chain[1] else None) or None
        metadata.topics = by_slot.get(TOPIC.slot, [])
        metadata.languages = by_slot.get(LANGUAGE.slot, [])


__all__ = ["LLM_ROLE", "EntityResolver"]
