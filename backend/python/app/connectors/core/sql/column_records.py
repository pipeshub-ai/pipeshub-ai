"""SQL_COLUMN records for a table, for any SQL engine.

Column records carry what is known about a column: its definition, key
flags, counts and value profile. They are
internal records hung off their table's record and never indexed themselves.
"""

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from app.config.constants.arangodb import MimeTypes, OriginTypes
from app.connectors.core.sql.profiling import ColumnProfile
from app.models.entities import (
    ProgressStatus,
    Record,
    RecordType,
    SQLColumnRecord,
    SQLTableRecord,
)


class ColumnFacts(Protocol):
    """What a column record is built from; each engine's column model provides it."""

    @property
    def name(self) -> str: ...
    @property
    def data_type(self) -> str | None: ...
    @property
    def nullable(self) -> bool: ...
    @property
    def default(self) -> str | None: ...
    @property
    def is_primary_key(self) -> bool: ...
    @property
    def is_unique(self) -> bool: ...
    @property
    def is_part_of_composite_primary_key(self) -> bool: ...
    @property
    def is_part_of_composite_unique(self) -> bool: ...


_STAT_KEYS = ("distinct_count", "null_count", "cardinality_ratio")


def column_revision(
    table_revision: str | None,
    stats: Mapping[str, Any],
    profile: ColumnProfile | None,
) -> str | None:
    """The table's revision, extended by the column's stats and profile.

    A stored record is rewritten only when its revision changes, so without
    this a profile that moved while the rows did not would never be saved.
    profiled_at is left out: it changes on every sync.
    """
    facts = {key: stats.get(key) for key in _STAT_KEYS}
    if profile is not None:
        facts.update(profile.model_dump(exclude={"profiled_at"}))
    if not any(value is not None for value in facts.values()):
        return table_revision
    digest = hashlib.md5(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    return f"{table_revision}:{digest}" if table_revision is not None else digest


def stored_stats_and_profile(record: Record) -> tuple[dict[str, Any], ColumnProfile | None]:
    """A saved column record's stats and value profile, as a fresh profile run would give them."""
    stats = {key: getattr(record, key, None) for key in _STAT_KEYS}
    profile = None
    if getattr(record, "profiled_at", None) is not None:
        profile = ColumnProfile(
            top_values=getattr(record, "top_values", None),
            top_value_frequencies=getattr(record, "top_value_frequencies", None),
            min_value=getattr(record, "min_value", None),
            max_value=getattr(record, "max_value", None),
            numeric_text=getattr(record, "numeric_text", None),
            profiled_at=record.profiled_at,
        )
    return stats, profile


def build_column_records(
    table_record: SQLTableRecord,
    columns: Sequence[ColumnFacts],
    *,
    column_fqn: Callable[[str, str], str],
    stats_by_name: Mapping[str, Mapping[str, Any]] | None = None,
    profiles: Mapping[str, ColumnProfile] | None = None,
    stored: Mapping[str, Record] | None = None,
    keep_stored: bool = False,
    values_allowed: bool = True,
) -> list[SQLColumnRecord]:
    """Column records for a table, in column order.

    ``column_fqn(table_fqn, column_name)`` is the engine's column id; the
    connector and id of each record are the table record's. A column with a
    blank or repeated name is skipped; ``ordinal_position`` is the column's
    index in ``columns``, so it keeps the gap. ``keep_stored`` means this
    sync's profiling failed: a column with a ``stored`` record (keyed by
    column id) keeps that record's stats and profile. ``stored`` must hold
    typed records (see ``load_typed_columns``); base records carry no stats.

    Without ``values_allowed`` (table indexing off) no record keeps top
    values or extremes, kept ones included: they are row data. The profile's
    ``profiled_at`` and ``numeric_text`` stay.
    """
    table_fqn = table_record.external_record_id
    seen: set[str] = set()
    records: list[SQLColumnRecord] = []
    for position, column in enumerate(columns):
        name = (column.name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        fqn = column_fqn(table_fqn, name)
        stats: Mapping[str, Any] = (stats_by_name or {}).get(name, {})
        profile = (profiles or {}).get(name)
        previous = (stored or {}).get(fqn) if keep_stored else None
        if previous is not None:
            stats, profile = stored_stats_and_profile(previous)
        if profile is not None and not values_allowed:
            profile = ColumnProfile(numeric_text=profile.numeric_text, profiled_at=profile.profiled_at)
        records.append(SQLColumnRecord(
            id=str(uuid.uuid4()),
            record_name=name,
            record_type=RecordType.SQL_COLUMN,
            record_group_type=table_record.record_group_type,
            external_record_group_id=table_record.external_record_group_id,
            external_record_id=fqn,
            external_revision_id=column_revision(table_record.external_revision_id, stats, profile),
            parent_external_record_id=table_fqn,
            parent_record_type=RecordType.SQL_TABLE,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=table_record.connector_name,
            connector_id=table_record.connector_id,
            mime_type=MimeTypes.SQL_COLUMN.value,
            weburl="",
            source_created_at=table_record.source_created_at,
            source_updated_at=table_record.source_updated_at,
            version=table_record.version,
            inherit_permissions=True,
            is_dependent_node=True,
            parent_node_id=table_record.id,
            is_internal=True,
            indexing_status=ProgressStatus.AUTO_INDEX_OFF.value,
            fqn=fqn,
            table_fqn=table_fqn,
            data_type=column.data_type,
            nullable=bool(column.nullable),
            is_primary_key=bool(column.is_primary_key),
            is_unique=bool(column.is_unique),
            is_part_of_composite_primary_key=bool(column.is_part_of_composite_primary_key),
            is_part_of_composite_unique=bool(column.is_part_of_composite_unique),
            default=column.default,
            ordinal_position=position,
            distinct_count=stats.get("distinct_count"),
            null_count=stats.get("null_count"),
            cardinality_ratio=stats.get("cardinality_ratio"),
            top_values=profile.top_values if profile else None,
            top_value_frequencies=profile.top_value_frequencies if profile else None,
            min_value=profile.min_value if profile else None,
            max_value=profile.max_value if profile else None,
            numeric_text=profile.numeric_text if profile else None,
            profiled_at=profile.profiled_at if profile else None,
        ))
    return records


async def load_typed_columns(processor: Any, records: Mapping[str, Record]) -> dict[str, Record]:  # noqa: ANN401
    """The typed records of these stored column records, keyed the same way.

    A parent lookup returns base records, which hold none of a column's
    stats or profile; those live on its type node. Raises when the read
    fails, and LookupError when any of them cannot be loaded, so a caller
    never mistakes a failed read for a column without stats.
    """
    ids = {key: record.id for key, record in records.items() if record.id}
    if not ids:
        return {}
    typed = await processor.get_typed_records_by_ids(list(ids.values()), raise_on_error=True)
    missing = [record_id for record_id in ids.values() if record_id not in typed]
    if missing:
        raise LookupError(f"Could not load {len(missing)} of {len(ids)} column record(s)")
    return {key: typed[record_id] for key, record_id in ids.items()}
