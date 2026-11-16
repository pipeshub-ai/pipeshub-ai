"""Processing diagnostics a record carries about its current indexing run.

Four optional internal fields, written inside the statements that already change a record's status:

* ``firstQueuedAt`` (epoch ms): when the current run first entered the queue. Unlike ``queuedAtTimestamp`` it
  survives retries, deferrals and republishes.
* ``indexingAttempts``: attempt starts in the current run. Diagnostic only; the retry counters stay in Redis.
* ``failedAt`` (epoch ms): the most recent terminal failure.
* ``pageCount``: pages of the PDF bytes parsed for the record's ``contentRev``.

The run rule is decided from the *stored* record, inside each write, so a read-then-write race cannot start a
run twice. It exists in two forms that must agree: ``reference`` (Python, used where the caller already holds the
record) and one statement fragment per store dialect (``aql_parts``, ``cypher_parts``, ``sql_patch``). A real-store
conformance test compares them over generated records.

Every comparison needs a number on **both** sides. A missing value, a JSON null and a string all read as "unknown":
AQL orders ``null`` before every number and ``5 <= 'x'`` is true, Cypher answers null for them, and PostgreSQL's
``->`` returns a JSON null that ``IS NULL`` does not match.
"""

from __future__ import annotations

import math
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import ProgressStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

FIRST_QUEUED_AT = "firstQueuedAt"
FAILED_AT = "failedAt"
INDEXING_ATTEMPTS = "indexingAttempts"
PAGE_COUNT = "pageCount"
RUN_FIELDS = (FIRST_QUEUED_AT, INDEXING_ATTEMPTS, FAILED_AT)
ALL_FIELDS = (*RUN_FIELDS, PAGE_COUNT)

# Stored fields that say when a run last ended.
_OUTCOME_STAMPS = ("failedAt", "lastIndexTimestamp", "restoredAtTimestamp")

_QUEUE_STATUSES = (ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value)
_ACTIVE_STATUSES = (ProgressStatus.QUEUED.value, ProgressStatus.IN_PROGRESS.value)
_OPEN_STATUSES = (ProgressStatus.NOT_STARTED.value, *_ACTIVE_STATUSES)
_FAILED = ProgressStatus.FAILED.value

MAX_PAGE_COUNT = 1_000_000

# Every stored field the run rule reads: what a reader keeps of a stored record so a later write can decide
# without another read.
STORED_RUN_FIELDS = (
    "indexingStatus", "firstQueuedAt", "queuedAtTimestamp", "indexingAttempts", "failedAt",
    "lastIndexTimestamp", "restoredAtTimestamp", "contentRev",
)


class WriteKind(str, Enum):
    """What a status write means for the run."""

    QUEUE = "QUEUE"  # the status becomes NOT_STARTED or QUEUED: continue an open run, else start one
    ATTEMPT_START = "ATTEMPT_START"  # IN_PROGRESS for a new attempt
    FAILED = "FAILED"  # a terminal failure
    SINK_END = "SINK_END"  # NOT_STARTED as an outcome (sink-only end): the run is over
    START = "START"  # a run start whatever the stored status is (trash restore)
    STATUS_VAR = "STATUS_VAR"  # the written status is a variable: queue rule, failure stamp, or nothing


def is_number(value: Any) -> bool:  # noqa: ANN401
    """A JSON number. Not a bool, a string, None or NaN."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))


def _last_outcome(stored: Mapping[str, Any], stamps: tuple[str, ...] = _OUTCOME_STAMPS) -> float | None:
    found = [stored[name] for name in stamps if is_number(stored.get(name))]
    return max(found) if found else None


def is_open(stored: Mapping[str, Any], *, outcome_stamps: tuple[str, ...] = _OUTCOME_STAMPS, always_open: bool = False) -> bool:
    """Whether the stored record is inside a run that has not ended.

    ``outcome_stamps`` and ``always_open`` exist so a test can run the rule with a part left out and see its flows fail."""
    if always_open:
        return True
    status = stored.get("indexingStatus")
    queued_at = stored.get(FIRST_QUEUED_AT)
    if not is_number(queued_at):
        # a record that was already mid-run before the field existed
        return status in _ACTIVE_STATUSES
    if status not in _OPEN_STATUSES:
        return False
    outcome = _last_outcome(stored, outcome_stamps)
    return outcome is None or queued_at > outcome


def _fill(stored: Mapping[str, Any], now: int) -> Any:  # noqa: ANN401
    if is_number(stored.get(FIRST_QUEUED_AT)):
        return stored[FIRST_QUEUED_AT]
    if is_number(stored.get("queuedAtTimestamp")):
        return stored["queuedAtTimestamp"]
    return now


def reference(
    stored: Mapping[str, Any] | None,
    kind: WriteKind,
    now: int,
    *,
    status: str | None = None,
    content_rev: str | None = None,
) -> dict[str, Any]:
    """The fields this write sets, from the stored record. ``None`` as a value means "clear".

    ``stored`` is None for a record that does not exist yet (a first save), which is a run start.
    """
    s: Mapping[str, Any] = stored or {}
    if kind is WriteKind.STATUS_VAR:
        if status in _QUEUE_STATUSES:
            kind = WriteKind.QUEUE
        elif status == _FAILED:
            kind = WriteKind.FAILED
        else:
            return {}
    if kind is WriteKind.QUEUE:
        if is_open(s):
            return {FIRST_QUEUED_AT: _fill(s, now)}
        return {FIRST_QUEUED_AT: now, INDEXING_ATTEMPTS: 0}
    if kind is WriteKind.START:
        return {FIRST_QUEUED_AT: now, INDEXING_ATTEMPTS: 0}
    if kind is WriteKind.ATTEMPT_START:
        if is_open(s):
            attempts = s.get(INDEXING_ATTEMPTS)
            patch: dict[str, Any] = {
                FIRST_QUEUED_AT: _fill(s, now),
                INDEXING_ATTEMPTS: (attempts if is_number(attempts) else 0) + 1,
            }
        else:
            patch = {FIRST_QUEUED_AT: now, INDEXING_ATTEMPTS: 1}
        # a count belongs to the content it was taken from
        if content_rev is not None and s.get("contentRev") != content_rev:
            patch[PAGE_COUNT] = None
        return patch
    if kind is WriteKind.FAILED:
        return {FAILED_AT: now}
    if kind is WriteKind.SINK_END:
        return {FIRST_QUEUED_AT: None}
    raise ValueError(f"unknown write kind {kind!r}")


def apply_reference(stored: Mapping[str, Any] | None, kind: WriteKind, now: int, **kw: Any) -> dict[str, Any]:  # noqa: ANN401
    """``stored`` after the write's diagnostics (a cleared field is dropped), for comparisons."""
    out = dict(stored or {})
    for name, value in reference(stored, kind, now, **kw).items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def stamped(fields: dict[str, Any], stored: Mapping[str, Any] | None, kind: WriteKind, now: int, **kw: Any) -> dict[str, Any]:  # noqa: ANN401
    """``fields`` plus this write's diagnostics, for a caller that writes with a plain field update."""
    return {**fields, **reference(stored, kind, now, **kw)}


def page_count_of(value: Any) -> int | None:  # noqa: ANN401
    """A page count the record may carry: a whole number in 1..MAX_PAGE_COUNT, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    if isinstance(value, int) and 1 <= value <= MAX_PAGE_COUNT:
        return value
    return None


# ---- ArangoDB ------------------------------------------------------------------------------------------------


def _q(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(f"'{v}'" for v in values) + "]"


@lru_cache(maxsize=512)
def aql_parts(
    doc: str,
    kind: WriteKind,
    *,
    now: str = "@now",
    status: str = "@status",
    rev: str | None = None,
    outcome_stamps: tuple[str, ...] = _OUTCOME_STAMPS,
    always_open: bool = False,
) -> tuple[str, str]:
    """``(lets, patch)`` for an AQL ``UPDATE``. ``lets`` goes between the ``FOR`` and the ``UPDATE`` (it reads the
    stored ``doc``); ``patch`` is an object expression to ``MERGE`` into the written fields.

    Bind vars used: ``now``, ``status`` (STATUS_VAR only), ``rev`` (ATTEMPT_START with a revision only).
    """
    d = doc
    stamps = ", ".join(f"IS_NUMBER({d}.{n}) ? {d}.{n} : null" for n in outcome_stamps)
    open_expr = (
        f"(!IS_NUMBER({d}.firstQueuedAt) AND {d}.indexingStatus IN {_q(_ACTIVE_STATUSES)}) "
        f"OR (IS_NUMBER({d}.firstQueuedAt) AND {d}.indexingStatus IN {_q(_OPEN_STATUSES)} "
        f"AND (_lo == null OR {d}.firstQueuedAt > _lo))"
    )
    lets = (
        f"LET _lo = MAX([{stamps}])\n"
        f"LET _open = {'true' if always_open else open_expr}\n"
        f"LET _fill = IS_NUMBER({d}.firstQueuedAt) ? {d}.firstQueuedAt "
        f": (IS_NUMBER({d}.queuedAtTimestamp) ? {d}.queuedAtTimestamp : {now})"
    )
    queue = f"(_open ? {{ firstQueuedAt: _fill }} : {{ firstQueuedAt: {now}, indexingAttempts: 0 }})"
    if kind is WriteKind.QUEUE:
        return lets, queue
    if kind is WriteKind.START:
        return "", f"{{ firstQueuedAt: {now}, indexingAttempts: 0 }}"
    if kind is WriteKind.ATTEMPT_START:
        patch = (
            # BIT_OR(.., 0): arithmetic on a stored value is a double, which the collection's "integer" type refuses
            f"(_open ? {{ firstQueuedAt: _fill, indexingAttempts: BIT_OR((IS_NUMBER({d}.indexingAttempts) ? {d}.indexingAttempts : 0) + 1, 0) }}"
            f" : {{ firstQueuedAt: {now}, indexingAttempts: 1 }})"
        )
        if rev is not None:
            patch = f"MERGE({patch}, (!IS_STRING({d}.contentRev) OR {d}.contentRev != {rev}) ? {{ pageCount: null }} : {{}})"
        return lets, patch
    if kind is WriteKind.FAILED:
        return "", f"{{ failedAt: {now} }}"
    if kind is WriteKind.SINK_END:
        return "", "{ firstQueuedAt: null }"
    if kind is WriteKind.STATUS_VAR:
        patch = (
            f"({status} IN {_q(_QUEUE_STATUSES)} ? {queue} : ({status} == '{_FAILED}' ? {{ failedAt: {now} }} : {{}}))"
        )
        return lets, patch
    raise ValueError(f"unknown write kind {kind!r}")


# ---- Neo4j ---------------------------------------------------------------------------------------------------

# The values the rule reads, captured before any SET: a second SET clause would read the status the first wrote.
_CYPHER_CAPTURE = (
    "n.indexingStatus AS _st, n.firstQueuedAt AS _fq, n.queuedAtTimestamp AS _qa, n.indexingAttempts AS _at, "
    "n.failedAt AS _fa, n.lastIndexTimestamp AS _li, n.restoredAtTimestamp AS _ra, n.contentRev AS _cr"
)


def _cnum(x: str) -> str:
    return f"({x} IS :: INTEGER NOT NULL OR {x} IS :: FLOAT NOT NULL)"


@lru_cache(maxsize=512)
def cypher_parts(
    node: str = "n",
    kind: WriteKind = WriteKind.QUEUE,
    *,
    now: str = "$now",
    status: str = "$status",
    rev: str | None = None,
    carry: tuple[str, ...] = (),
    outcome_stamps: tuple[str, ...] = _OUTCOME_STAMPS,
    always_open: bool = False,
) -> tuple[str, str]:
    """``(with_clause, patch_map)``. The ``WITH`` captures the stored values and carries ``carry`` variables;
    put it after the ``MATCH``/``WHERE`` and write ``SET n += <fields> + <patch_map>`` in ONE clause.

    A cleared field is a ``null`` in the map, which ``SET n +=`` removes.
    """
    carried = ", ".join((node, *carry))
    with_clause = f"WITH {carried}, {_CYPHER_CAPTURE.replace('n.', node + '.')}"
    alias = {"failedAt": "_fa", "lastIndexTimestamp": "_li", "restoredAtTimestamp": "_ra"}
    listed = ", ".join(f"CASE WHEN {_cnum(alias[n])} THEN {alias[n]} END" for n in outcome_stamps)
    stamps = f"[x IN [{listed}] WHERE x IS NOT NULL]"
    open_ = (
        f"((NOT {_cnum('_fq')} AND _st IN {_q(_ACTIVE_STATUSES)}) OR ({_cnum('_fq')} AND _st IN {_q(_OPEN_STATUSES)} "
        f"AND (size({stamps}) = 0 OR _fq > reduce(m = head({stamps}), x IN {stamps} | CASE WHEN x > m THEN x ELSE m END))))"
    )
    if always_open:
        open_ = "true"
    fill = f"CASE WHEN {_cnum('_fq')} THEN _fq WHEN {_cnum('_qa')} THEN _qa ELSE {now} END"
    attempts = f"coalesce(CASE WHEN {_cnum('_at')} THEN _at END, 0)"
    if kind is WriteKind.QUEUE:
        return with_clause, (
            f"{{ firstQueuedAt: CASE WHEN {open_} THEN {fill} ELSE {now} END, "
            f"indexingAttempts: CASE WHEN {open_} THEN _at ELSE 0 END }}"
        )
    if kind is WriteKind.START:
        return with_clause, f"{{ firstQueuedAt: {now}, indexingAttempts: 0 }}"
    if kind is WriteKind.ATTEMPT_START:
        patch = (
            f"firstQueuedAt: CASE WHEN {open_} THEN {fill} ELSE {now} END, "
            f"indexingAttempts: CASE WHEN {open_} THEN {attempts} + 1 ELSE 1 END"
        )
        if rev is not None:
            patch += f", pageCount: CASE WHEN _cr IS NULL OR NOT _cr = {rev} THEN null ELSE {node}.pageCount END"
        return with_clause, "{ " + patch + " }"
    if kind is WriteKind.FAILED:
        return with_clause, f"{{ failedAt: {now} }}"
    if kind is WriteKind.SINK_END:
        return with_clause, "{ firstQueuedAt: null }"
    if kind is WriteKind.STATUS_VAR:
        return with_clause, (
            f"CASE WHEN {status} IN {_q(_QUEUE_STATUSES)} THEN {{ firstQueuedAt: CASE WHEN {open_} THEN {fill} ELSE {now} END, "
            f"indexingAttempts: CASE WHEN {open_} THEN _at ELSE 0 END }} "
            f"WHEN {status} = '{_FAILED}' THEN {{ failedAt: {now} }} ELSE {{}} END"
        )
    raise ValueError(f"unknown write kind {kind!r}")


# ---- PostgreSQL ----------------------------------------------------------------------------------------------


def _pnum(doc: str, name: str) -> str:
    return f"coalesce(jsonb_typeof({doc}->'{name}') = 'number', false)"


@lru_cache(maxsize=512)
def sql_patch(
    doc: str,
    kind: WriteKind,
    *,
    now: str = "$1::bigint",
    status: str = "$2::text",
    rev: str | None = None,
    outcome_stamps: tuple[str, ...] = _OUTCOME_STAMPS,
    always_open: bool = False,
) -> str:
    """A ``jsonb`` expression to ``||`` onto the document, evaluated against the stored ``doc``.

    A cleared field is a JSON null (readers treat it as absent). ``rev`` is a ``text`` expression.
    """
    d = doc

    def stamp(name: str) -> str:
        return f"CASE WHEN {_pnum(d, name)} THEN ({d}->>'{name}')::numeric END"

    lo = "GREATEST(" + ", ".join(stamp(n) for n in outcome_stamps) + ")" if outcome_stamps else "NULL::numeric"
    fq_num = _pnum(d, FIRST_QUEUED_AT)
    open_ = (
        f"(({d}->>'indexingStatus' IN {_sq(_ACTIVE_STATUSES)} AND NOT {fq_num}) "
        f"OR ({fq_num} AND {d}->>'indexingStatus' IN {_sq(_OPEN_STATUSES)} "
        f"AND (({lo}) IS NULL OR ({d}->>'firstQueuedAt')::numeric > ({lo}))))"
    )
    fill = (
        f"CASE WHEN {fq_num} THEN {d}->'firstQueuedAt' WHEN {_pnum(d, 'queuedAtTimestamp')} THEN {d}->'queuedAtTimestamp' "
        f"ELSE to_jsonb({now}) END"
    )
    queue = (
        f"CASE WHEN {open_} THEN jsonb_build_object('firstQueuedAt', {fill}) "
        f"ELSE jsonb_build_object('firstQueuedAt', {now}, 'indexingAttempts', 0) END"
    )
    if always_open:
        open_ = "true"
        queue = (
            f"CASE WHEN {open_} THEN jsonb_build_object('firstQueuedAt', {fill}) "
            f"ELSE jsonb_build_object('firstQueuedAt', {now}, 'indexingAttempts', 0) END"
        )
    if kind is WriteKind.QUEUE:
        return queue
    if kind is WriteKind.START:
        return f"jsonb_build_object('firstQueuedAt', {now}, 'indexingAttempts', 0)"
    if kind is WriteKind.ATTEMPT_START:
        attempts = f"coalesce(CASE WHEN {_pnum(d, 'indexingAttempts')} THEN ({d}->>'indexingAttempts')::numeric END, 0) + 1"
        patch = (
            f"CASE WHEN {open_} THEN jsonb_build_object('firstQueuedAt', {fill}, 'indexingAttempts', {attempts}) "
            f"ELSE jsonb_build_object('firstQueuedAt', {now}, 'indexingAttempts', 1) END"
        )
        if rev is not None:
            patch += (
                f" || CASE WHEN jsonb_typeof({d}->'contentRev') IS DISTINCT FROM 'string' OR {d}->>'contentRev' <> {rev} "
                f"THEN jsonb_build_object('pageCount', null) ELSE '{{}}'::jsonb END"
            )
        return patch
    if kind is WriteKind.FAILED:
        return f"jsonb_build_object('failedAt', {now})"
    if kind is WriteKind.SINK_END:
        return "jsonb_build_object('firstQueuedAt', null)"
    if kind is WriteKind.STATUS_VAR:
        return (
            f"CASE WHEN {status} IN {_sq(_QUEUE_STATUSES)} THEN {queue} "
            f"WHEN {status} = '{_FAILED}' THEN jsonb_build_object('failedAt', {now}) ELSE '{{}}'::jsonb END"
        )
    raise ValueError(f"unknown write kind {kind!r}")


def _sq(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def with_run_patch(statement: str, patch: str) -> str:
    """``statement`` with its ``{RUN_PATCH}`` marker replaced by ``patch`` (from ``sql_patch``). The fragment is built
    from constants only; no caller data reaches it."""
    return statement.replace("{RUN_PATCH}", patch)
