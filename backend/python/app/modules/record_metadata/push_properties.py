"""The property values a push stored with a record's blocks, read back at index time.

The push validates the values once (size caps, types, schema) and stores
``{"schemaId", "values"}`` next to the blocks (``push/contract.py:canonical_properties``). Indexing trusts that
validation and only converts. Three outcomes, because projection treats them differently:

* ``None``: the record has none (no member, or an empty one).
* a ``RecordMetadata``: the values (``values`` may be empty: "the record has a schema and no values").
* ``NOT_PROVIDED``: the member is there and cannot be read. Projection keeps what it stored before.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from pydantic import ValidationError

from app.models.record_metadata import MetadataProvenance, MetadataValue, RecordMetadata
from app.modules.record_metadata.projection import NOT_PROVIDED, _NotProvided
from app.telemetry.modules.record_storage_metrics import pushed_properties_refused

#: ``MetadataValue.source`` of a pushed value: the string the push validated it with (``push/worker.py``).
PUSH_SOURCE: Final = "push"

#: The key of the stored document that holds the values.
PROPERTIES_KEY: Final = "properties"

_log = logging.getLogger(__name__)


def push_properties(blocks_dict: object) -> RecordMetadata | None | _NotProvided:
    """The pushed values of an already-decoded stored document. Never raises."""
    if not isinstance(blocks_dict, Mapping):
        return NOT_PROVIDED
    member = blocks_dict.get(PROPERTIES_KEY)
    if member is None:
        return None
    if not isinstance(member, Mapping):
        return _malformed(TypeError)
    if not member:
        return None
    values = member.get("values")
    schema_id = member.get("schemaId")
    if not isinstance(values, Mapping) or not (
        schema_id is None or isinstance(schema_id, str)
    ):
        return _malformed(TypeError)
    try:
        return RecordMetadata(
            schema_id=schema_id,
            values={
                name: MetadataValue(
                    value=value,
                    provenance=MetadataProvenance.PROVIDED,
                    source=PUSH_SOURCE,
                )
                for name, value in values.items()
            },
        )
    except (ValidationError, TypeError, ValueError) as exc:
        return _malformed(type(exc))


def _malformed(kind: type[BaseException]) -> _NotProvided:
    # The exception type only: the message can quote a pushed value.
    _log.warning(
        "Pushed properties are unreadable (%s); the record's earlier values are kept",
        kind.__name__,
    )
    pushed_properties_refused("malformed")
    return NOT_PROVIDED
