"""What may be stored about a column's values, for any SQL engine.

Top values and value ranges are copied into the graph and into prompts, so
only columns that look like a vocabulary get top values, and nothing that
looks like personal data is kept. Each engine reads its own statistics and
classifies its own type names; these rules decide what survives.
"""

import re
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict

ColumnKind = Literal["numeric", "temporal", "text"]

TOP_VALUES_LIMIT = 20
# Longer values are dropped, not cut: a stored value can be used verbatim as
# an equality literal, and a cut one matches nothing.
TOP_VALUE_MAX_CHARS = 80

# Only columns that look like a vocabulary qualify: few distinct values, each
# repeated. The ratio is over non-null rows, or a mostly-null column of unique
# emails passes.
_SMALL_VOCABULARY = (50, 0.5)
_LARGE_VOCABULARY = (1000, 0.2)
# Second line of defence for columns that hold personal data yet pass the
# vocabulary test, matched against the column name with non-alphanumerics as _.
# [flm]name catches abbreviations like admfname2 (and the odd false positive).
_PERSONAL_COLUMN_RE = re.compile(
    r"e_?mail|phone|mobile|(?:^|_)fax(?:_|$)|(?:^|_)ssn(?:_|$)|social_?security|passw(?:or)?d"
    r"|(?:first|last|middle|full|display|user|given|family|sur|fore|nick|maiden)_?name|[flm]name"
    r"|(?:customer|contact|client|employee|patient|person|member|owner|author|recipient"
    r"|sender|manager|assignee|reporter)_?name"
    r"|(?:^|_)(?:owner|author|assignee|reporter|assigned_to|created_by|updated_by|modified_by)(?:_|$)"
    r"|birth|(?:^|_)dob(?:_|$)"
    r"|login|address|street|(?:^|_)ip(?:_|$)|ip_?addr"
    r"|passport|national_?id|tax_?id|(?:^|_)iban(?:_|$)|(?:credit_?)?card_?(?:no|num|number)(?:_|$)"
    r"|salary|(?:^|_)wage"
)
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,}|\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}")
# A column is free text when most of its common values are long or wordy.
_FREE_TEXT_CHARS = 40
_FREE_TEXT_WORDS = 5
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


class ColumnStats(TypedDict):
    """A column's exact counts, as column records store them."""
    name: str
    ordinal_position: int
    distinct_count: int | None
    null_count: int | None
    # Distinct values per row of the table, nulls included.
    cardinality_ratio: float | None


def _first_present(row: dict[str, Any], *keys: str) -> Any:  # noqa: ANN401
    for key in keys:
        if key in row:
            return row[key]
    return None


def _int_or_none(value: Any) -> int | None:  # noqa: ANN401
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_table_column_stats_row(
    stats_row: dict[str, Any],
    column_names: list[str],
    *,
    fallback_total_rows: int = 0,
) -> tuple[int, list[ColumnStats]]:
    """Row count and per-column counts from one stats row.

    The row holds ``total_rows`` and, per column at position ``i``, ``d{i}``
    (distinct) and ``n{i}`` (nulls); positional aliases because a name-based
    alias can pass an engine's identifier length limit. Keys are matched in
    either case, since engines fold unquoted aliases differently.
    """
    total_rows = _int_or_none(stats_row.get("TOTAL_ROWS") or stats_row.get("total_rows") or 0)
    if total_rows is None:
        total_rows = fallback_total_rows

    column_stats: list[ColumnStats] = []
    for pos, name in enumerate(column_names):
        if not name:
            continue
        safe = name.replace('"', "")
        distinct_count = _int_or_none(_first_present(
            stats_row, f"d{pos}", f"D{pos}", f"DISTINCT_{safe.upper()}", f"distinct_{safe}",
        ))
        null_count = _int_or_none(_first_present(
            stats_row, f"n{pos}", f"N{pos}", f"NULLS_{safe.upper()}", f"nulls_{safe}",
        ))
        ratio = distinct_count / total_rows if distinct_count is not None and total_rows > 0 else None
        column_stats.append({
            "name": name,
            "ordinal_position": pos,
            "distinct_count": distinct_count,
            "null_count": null_count,
            "cardinality_ratio": ratio,
        })
    return total_rows, column_stats


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra='allow')
    top_values: list[str] | None = None
    top_value_frequencies: list[float] | None = None
    min_value: str | None = None
    max_value: str | None = None
    numeric_text: bool | None = None
    profiled_at: int | None = None


def looks_personal(column_name: str) -> bool:
    normalized = re.sub(r"[^0-9a-z]+", "_", column_name.lower())
    return bool(_PERSONAL_COLUMN_RE.search(normalized))


def looks_like_contact_value(value: str) -> bool:
    """An e-mail address or phone number."""
    return bool(_EMAIL_RE.search(value) or _PHONE_RE.fullmatch(value.strip()))


def distinct_and_ratio_from_counts(
    distinct_count: float | None,
    null_count: float | None,
    row_count: float | None,
) -> tuple[float | None, float | None]:
    """Distinct count, and distinct values per non-null row, from counts."""
    if row_count is None or row_count <= 0 or distinct_count is None or null_count is None:
        return None, None
    distinct = float(distinct_count)
    non_null = row_count - float(null_count)
    if non_null <= 0:
        return distinct, None
    return distinct, distinct / non_null


def vocabulary_eligible(
    *,
    name: str,
    is_unique: bool,
    is_primary_key: bool,
    is_part_of_composite_primary_key: bool,
    excluded_type: bool,
    distinct_count: float | None,
    non_null_ratio: float | None,
) -> bool:
    """Whether a column's most common values may be stored as its vocabulary.

    ``excluded_type`` is the engine's say that values of this type are no
    vocabulary (binary, structured, geometric, network addresses).
    """
    if is_unique or (is_primary_key and not is_part_of_composite_primary_key):
        return False
    if excluded_type or looks_personal(name):
        return False
    if distinct_count is None or non_null_ratio is None or distinct_count <= 0:
        return False
    max_small, ratio_small = _SMALL_VOCABULARY
    max_large, ratio_large = _LARGE_VOCABULARY
    return (
        (distinct_count <= max_small and non_null_ratio <= ratio_small)
        or (distinct_count <= max_large and non_null_ratio <= ratio_large)
    )


def range_eligible(kind: ColumnKind | None, name: str) -> bool:
    """Whether a column's MIN and MAX may be stored.

    An extreme is one row's value: a phone number stored as a number or a
    birth date leaks as surely as a top value would.
    """
    return kind in ("numeric", "temporal") and not looks_personal(name)


def frequency(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 4)


def top_values_from_counts(rows: list[dict[str, Any]]) -> tuple[list[str], list[float]] | None:
    """Values from rows of ``value``, ``n`` and ``total`` (all non-null rows), unscreened."""
    top: list[str] = []
    shares: list[float] = []
    for row in rows:
        value, count, total = row.get("value"), row.get("n"), row.get("total")
        if value is None or not count or not total:
            continue
        top.append(str(value))
        shares.append(frequency(float(count) / float(total)))
    return (top, shares) if top else None


def screen_top_values(values: list[str], shares: list[float]) -> tuple[list[str], list[float]] | None:
    """The values fit to store: none for a column of free text or contact
    details, and none longer than ``TOP_VALUE_MAX_CHARS``."""
    wordy = sum(1 for v in values if len(v) > _FREE_TEXT_CHARS or len(v.split()) > _FREE_TEXT_WORDS)
    if wordy * 2 > len(values):
        return None
    if any(looks_like_contact_value(v) for v in values):
        return None
    kept = [(v, s) for v, s in zip(values, shares, strict=False) if len(v) <= TOP_VALUE_MAX_CHARS][:TOP_VALUES_LIMIT]
    if not kept:
        return None
    return [v for v, _ in kept], [s for _, s in kept]


def set_top_values(profile: ColumnProfile, candidates: tuple[list[str], list[float]]) -> None:
    screened = screen_top_values(*candidates)
    if screened is not None:
        profile.top_values, profile.top_value_frequencies = screened


def detect_numeric_text(kind: ColumnKind | None, observed: list[str | None]) -> bool | None:
    """Whether every observed value of a text column is a number."""
    if kind != "text":
        return None
    values = [v.strip() for v in observed if v is not None]
    if not values:
        return None
    # A leading zero (ZIP 02134, code 007) means the text is an identifier:
    # casting it to a number would drop the zero and break equality filters.
    if any(len(v) > 1 and v[0] == "0" and v[1].isdigit() for v in values):
        return False
    return all(_NUMBER_RE.fullmatch(v) for v in values)
