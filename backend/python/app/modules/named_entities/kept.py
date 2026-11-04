"""The named-entity extraction a record keeps, without a model call.

The ``entities`` stage keeps its extraction in its stage state's ``output``, keyed by the
record's virtual record id and content revision. Records indexed before the stage existed
kept it in their stored record's ``semantic_metadata.named_entities`` section instead,
which is read only when the revision has no stage state.
"""

import json
import logging
from typing import TYPE_CHECKING

from app.config.constants.arangodb import ProgressStatus
from app.modules.named_entities.domain.models import (
    NamedEntityExtraction,
    read_stored_extraction,
)
from app.modules.pipeline.models import StageStatePatch, stage_state_key

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

ENTITIES_STAGE = "entities"

logger = logging.getLogger(__name__)


async def read_kept_extraction(
    graph: "IGraphDBProvider",
    virtual_record_id: str | None,
    rev: str | None,
    legacy_section: object = None,
) -> NamedEntityExtraction | None:
    """The extraction kept for this revision, or None when there is none.

    A revision the stage has a state for answers from that state alone: a state with no
    output (skipped, turned off, not finished) means no extraction, never the legacy section,
    which may hold an older revision's entities. Raises on infrastructure errors.
    """
    if virtual_record_id and rev:
        state = await graph.stage_state_get(stage_state_key(virtual_record_id, rev, ENTITIES_STAGE))
        if state is not None:
            output = state.get("output")
            if not output:
                return None
            try:
                return read_stored_extraction(json.loads(output))
            except ValueError:
                logger.warning("The kept named-entity extraction of %s is not JSON", virtual_record_id)
                return None
    return read_stored_extraction(legacy_section)


async def keep_extraction(
    graph: "IGraphDBProvider",
    virtual_record_id: str | None,
    rev: str | None,
    extraction: NamedEntityExtraction,
) -> bool:
    """Replace the kept extraction of a revision the stage completed, as a re-extraction that
    finished what it left unread does. Only a COMPLETED state is updated: a run in progress
    writes its own. Whether it was replaced."""
    if not virtual_record_id or not rev:
        return False
    completed = ProgressStatus.COMPLETED.value
    return await graph.stage_state_compare_and_set(
        stage_state_key(virtual_record_id, rev, ENTITIES_STAGE),
        completed,
        completed,
        StageStatePatch(output=extraction.model_dump_json()).fields(),
    )
