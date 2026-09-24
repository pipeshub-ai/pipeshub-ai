"""The one pipeline from search hits to the context the model reads.

Every retrieval entry point (the search tool, first-turn prefetch, the legacy
retrieval tool) goes through ``KnowledgeContextBuilder.build`` so they cannot
drift apart:

    hits → units → rank and keep the best → neighbours → graph context → reading order
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.context.neighbours import expand_neighbours
from app.modules.retrieval.context.ordering import order_for_reading
from app.modules.retrieval.context.ranking import RelevanceRanker
from app.utils.chat_helpers import (
    enrich_records_with_graph_context,
    enrich_virtual_record_id_to_result_with_fk_children,
    get_flattened_results,
)

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.modules.retrieval.context.units import Unit
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


@dataclass
class KnowledgeContext:
    units: list[Unit]
    """Units in reading order: records most relevant first, blocks in document order."""
    virtual_record_id_to_result: dict[str, Any]
    """The records those units belong to, and nothing else."""


class KnowledgeContextBuilder:
    def __init__(
        self,
        *,
        blob_store: BlobStorage,
        graph_provider: IGraphDBProvider | None,
        org_id: str,
        config_service: ConfigurationService | None = None,
        ranker: RelevanceRanker | None = None,
    ) -> None:
        self._blob_store = blob_store
        self._graph_provider = graph_provider
        self._org_id = org_id
        self._config_service = config_service
        self._ranker = ranker or RelevanceRanker()

    async def build(
        self,
        search_results: list[dict[str, Any]],
        virtual_to_record_map: dict[str, Any],
        *,
        is_multimodal_llm: bool,
        max_units: int | None = None,
        include_fk_children: bool = False,
    ) -> KnowledgeContext:
        """``max_units`` caps the ranked units; neighbours come on top of it.

        ``include_fk_children`` adds the DDL of tables related by foreign key
        to any SQL table that survived ranking.
        """
        records: dict[str, Any] = {}
        units = await get_flattened_results(
            search_results,
            self._blob_store,
            self._org_id,
            is_multimodal_llm,
            records,
            virtual_to_record_map,
            graph_provider=self._graph_provider,
        )
        units = [u for u in units if records.get(u.get("virtual_record_id")) is not None]
        units = self._ranker.rank(units, max_units)
        units = expand_neighbours(units, records)

        kept = {unit.get("virtual_record_id") for unit in units}
        records = {vrid: record for vrid, record in records.items() if vrid in kept}
        if include_fk_children:
            await enrich_virtual_record_id_to_result_with_fk_children(
                records, self._blob_store, self._org_id, self._graph_provider, units,
            )
        if units and self._graph_provider:
            await enrich_records_with_graph_context(
                records,
                self._graph_provider,
                units,
                virtual_to_record_map,
                blob_store=self._blob_store,
                org_id=self._org_id,
                config_service=self._config_service,
            )
        return KnowledgeContext(units=order_for_reading(units), virtual_record_id_to_result=records)
