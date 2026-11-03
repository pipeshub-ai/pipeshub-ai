"""A readable view of a tool's result for the chat's tool card, built from the FULL result.

Tools answer in JSON the person can't read ("{" was the whole summary of a Jira search). This
finds what the result is — a list of records, one record, or plain text — and returns a one-line
summary ("Found 23 issues") plus, for JSON, a small view the card draws as a table or a list of
fields, with the raw output one click away:

    {"kind": "records", "columns": ["Key", "Summary", …], "rows": [{"cells": [...], "url": "…"}],
     "total": 50}
    {"kind": "fields", "fields": [{"label": "Status", "value": "Done"}, …], "url": "…"}

Plain text needs no view: the card shows the (bounded) preview as text. A view is stored with the
conversation, so it is kept under `MAX_VIEW_BYTES`.

Nothing here trusts the shape it is given: anything unexpected gives no view (`None`) and the card
shows the raw output as before. Links are http(s) only, without user-info.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

__all__ = ["MAX_VIEW_BYTES", "Described", "describe", "parse_embedded_json", "view_bytes"]

MAX_ROWS = 20
MAX_COLUMNS = 5
MAX_FIELDS = 12
# UTF-8 bytes of one encoded view.
MAX_VIEW_BYTES = 5000
_MAX_CELL_CHARS = 120
_MAX_FIELD_CHARS = 200
_MAX_SUMMARY_CHARS = 200
_MAX_NOUN_CHARS = 40
_MAX_URL_CHARS = 2000
# Longer text isn't parsed for the card; the model still gets all of it.
_MAX_PARSE_CHARS = 250_000
_MAX_EMBEDDED_ATTEMPTS = 3
# Records sampled to choose the columns.
_SAMPLE = 20
_FALLBACK_COLUMNS = 4

# Keys whose dict values hold a record's own fields (Jira's `fields`, Confluence's `content`).
_NESTED = ("fields", "properties", "attributes", "content", "data")
# A dict value shown by its first one of these.
_DISPLAY_KEYS = ("displayName", "display_name", "name", "title", "value", "login", "email", "key")
# Column roles, in column order; each takes the first of its keys most sampled records have.
_ROLES: tuple[tuple[str, ...], ...] = (
    ("key", "identifier", "number", "issuekey"),
    ("title", "summary", "name", "subject", "displayname", "filename", "headline"),
    ("status", "state", "statuscategory"),
    ("assignee", "owner", "author", "creator", "createdby", "reporter", "user", "from", "sender", "organizer"),
    (
        "updated", "updatedat", "updatedtime", "lastmodified", "modified", "modifiedat", "modifiedtime",
        "lastupdated", "lastedited", "lasteditedtime", "created", "createdat", "createddate", "createdtime",
        "date", "timestamp", "duedate", "due",
    ),
)
_IDENTITY_KEYS = frozenset({"id", "key", "identifier", "number", "title", "name", "summary", "subject"})
_LINK_KEYS = (
    "url", "htmlurl", "weburl", "weblink", "webviewlink", "browseurl", "permalink", "link", "href", "shareurl",
    "viewurl", "webui",
)
_TOTAL_KEYS = ("total", "totalsize", "totalcount", "totalresults", "totalhits", "count")
# List keys that say nothing about what is in them.
_GENERIC_NOUNS = frozenset({
    "results", "result", "items", "item", "data", "values", "value", "records", "entries", "nodes", "edges",
    "hits", "list", "rows", "objects", "elements", "response", "content", "contents",
})
_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_JSON_LINE_START = re.compile(r"^[ \t]*[\[{]", re.MULTILINE)
_SPACES = re.compile(r"\s+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_DECODER = json.JSONDecoder()


@dataclass(frozen=True)
class Described:
    summary: Optional[str] = None
    view: Optional[dict[str, Any]] = None


def describe(content: Any) -> Described:  # noqa: ANN401
    """Summary and view for a tool result's content: text, or JSON-shaped data."""
    try:
        if isinstance(content, str):
            parsed = parse_embedded_json(content)
            return _described_text(content) if parsed is None else _described_data(parsed)
        if isinstance(content, (dict, list)):
            return _described_data(content)
    except Exception:
        pass
    return Described()


def parse_embedded_json(text: str) -> Any:  # noqa: ANN401
    """The JSON object or array `text` is, or ends with after a note (Atlassian sends a notice
    before its JSON); None when there isn't one."""
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_PARSE_CHARS:
        return None
    if stripped[0] in "{[":
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    for attempt, match in enumerate(_JSON_LINE_START.finditer(text)):
        if attempt >= _MAX_EMBEDDED_ATTEMPTS:
            break
        start = match.end() - 1
        if not text[:start].strip():
            continue
        try:
            value, end = _DECODER.raw_decode(text, start)
        except ValueError:
            continue
        if not text[end:].strip():
            return value
    return None


# ---------------------------------------------------------------------------

def _described_text(text: str) -> Described:
    stripped = text.strip()
    if not stripped:
        return Described("No output")
    return Described(_clip(_first_line(stripped), _MAX_SUMMARY_CHARS))


def _described_data(data: Any) -> Described:  # noqa: ANN401
    if isinstance(data, list):
        return _described_list(data, noun=None, envelope=None)
    if not isinstance(data, dict):
        return Described()
    if not _looks_like_one_record(data):
        found = _main_list(data)
        if found is not None:
            key, items, holder = found
            return _described_list(items, noun=key, envelope=data, holder=holder)
    return _described_record(data, base=_base_url(data))


def _looks_like_one_record(data: dict[str, Any]) -> bool:
    keys = {_norm(k) for k in data}
    for name in _NESTED:
        nested = data.get(name)
        if isinstance(nested, dict):
            keys |= {_norm(k) for k in nested}
    return bool(keys & _IDENTITY_KEYS)


def _main_list(data: dict[str, Any]) -> Optional[tuple[str, list[Any], dict[str, Any]]]:
    """The longest list of records within three levels of dicts (`{"data": {"issues": {"nodes":
    [...]}}}`), else the longest list of plain values: (its key, the list, the dict holding it)."""
    best: Optional[tuple[str, list[Any], dict[str, Any]]] = None
    best_values: Optional[tuple[str, list[Any], dict[str, Any]]] = None

    def visit(node: dict[str, Any], depth: int) -> None:
        nonlocal best, best_values
        for key, value in node.items():
            if isinstance(value, list) and value:
                if _mostly_records(value):
                    if best is None or len(value) > len(best[1]):
                        best = (key, value, node)
                elif all(_is_scalar(v) for v in value) and (best_values is None or len(value) > len(best_values[1])):
                    best_values = (key, value, node)
            elif isinstance(value, list) and best is None and best_values is None:
                best_values = (key, value, node)
            elif isinstance(value, dict) and depth < 3:
                visit(value, depth + 1)

    visit(data, 1)
    return best or best_values


def _described_list(
    items: list[Any], *, noun: Optional[str], envelope: Optional[dict[str, Any]], holder: Optional[dict[str, Any]] = None,
) -> Described:
    total = _declared_total(holder, len(items)) if holder is not None else len(items)
    if envelope is not None and holder is not envelope:
        total = max(total, _declared_total(envelope, len(items)))
    label = _noun(noun)
    if not items:
        return Described(f"No {label} found")
    summary = f"Found {total} {_singular(label) if total == 1 else label}"
    if total > len(items):
        summary += f", {len(items)} returned"
    records = [item for item in items if isinstance(item, dict)]
    if _mostly_records(items):
        view = _records_view(records, total=len(items), base=_base_url(envelope) if envelope else None)
    else:
        view = _values_view(items)
    return Described(_clip(summary, _MAX_SUMMARY_CHARS), view)


def _records_view(records: list[dict[str, Any]], *, total: int, base: Optional[str]) -> Optional[dict[str, Any]]:
    flat = [_flatten(record) for record in records[:MAX_ROWS]]
    columns = _choose_columns(flat[:_SAMPLE])
    if not columns:
        return None
    rows = []
    for record, cells in zip(records, flat):
        row: dict[str, Any] = {"cells": [_clip(cells[key][1], _MAX_CELL_CHARS) if key in cells else "" for key in columns]}
        if url := _link(record, base):
            row["url"] = url
        rows.append(row)
    labels = [_humanize(_flat_label(flat, key)) for key in columns]
    return _fit({"kind": "records", "columns": labels, "rows": rows, "total": total}, "rows")


def _flat_label(flat: list[dict[str, tuple[str, str]]], key: str) -> str:
    return next(cells[key][0] for cells in flat if key in cells)


def _values_view(items: list[Any]) -> Optional[dict[str, Any]]:
    rows = [{"cells": [_clip(_display(item) or "", _MAX_CELL_CHARS)]} for item in items[:MAX_ROWS]]
    return _fit({"kind": "records", "columns": ["Value"], "rows": rows, "total": len(items)}, "rows")


def _described_record(record: dict[str, Any], *, base: Optional[str]) -> Described:
    cells = _flatten(record)
    if not cells:
        return Described(f"Returned {_plural(len(record), 'field')}")
    ordered = [key for role in _ROLES for key in role if key in cells]
    ordered = list(dict.fromkeys(ordered + list(cells)))
    fields = [{"label": _humanize(cells[key][0]), "value": _clip(cells[key][1], _MAX_FIELD_CHARS)} for key in ordered[:MAX_FIELDS]]
    view: dict[str, Any] = {"kind": "fields", "fields": fields}
    if url := _link(record, base):
        view["url"] = url
    identity = next((cells[key][1] for key in _ROLES[0] if key in cells), None)
    title = next((cells[key][1] for key in _ROLES[1] if key in cells), None)
    message = record.get("message") if isinstance(record.get("message"), str) else None
    if identity and title and identity != title:
        summary = f"{identity} · {title}"
    else:
        summary = title or identity or (_first_line(message) if message else f"Returned {_plural(len(cells), 'field')}")
    return Described(_clip(summary, _MAX_SUMMARY_CHARS), _fit(view, "fields"))


def _choose_columns(flat: list[dict[str, tuple[str, str]]]) -> list[str]:
    if not flat:
        return []
    enough = (len(flat) + 1) // 2

    def common(key: str) -> bool:
        return sum(key in cells for cells in flat) >= enough

    chosen = [next((key for key in role if common(key)), None) for role in _ROLES]
    columns = [key for key in chosen if key]
    if len(columns) >= 2:
        return columns[:MAX_COLUMNS]
    # Nothing we recognise: the first short fields most records have.
    seen = list(dict.fromkeys(key for cells in flat for key in cells))
    short = [key for key in seen if common(key) and all(len(cells[key][1]) <= _MAX_FIELD_CHARS for cells in flat if key in cells)]
    return list(dict.fromkeys(columns + short))[:_FALLBACK_COLUMNS]


def _flatten(record: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """normalised key → (its key, shown value): the record's own values, then those of its
    `fields`/`content`/… dict. Keys starting with `_` (links, expand hints) are skipped."""
    cells: dict[str, tuple[str, str]] = {}

    def add(source: dict[str, Any]) -> None:
        for key, value in source.items():
            if not isinstance(key, str) or key.startswith("_"):
                continue
            norm = _norm(key)
            if norm in cells or norm in ("self", "ari", "base64encodedari"):
                continue
            shown = _display(value)
            if shown:
                cells[norm] = (key, shown)

    add(record)
    for name in _NESTED:
        nested = record.get(name)
        if isinstance(nested, dict):
            add(nested)
    return cells


def _display(value: Any) -> Optional[str]:  # noqa: ANN401
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = _SPACES.sub(" ", value).strip()
        if match := _ISO_DATE.match(text):
            return match.group(1)
        return text or None
    if isinstance(value, dict):
        for key in _DISPLAY_KEYS:
            shown = value.get(key)
            if isinstance(shown, str) and shown.strip():
                return _SPACES.sub(" ", shown).strip()
        return None
    if isinstance(value, list):
        names = [shown for item in value[:4] if not isinstance(item, list) and (shown := _display(item))]
        if not names:
            return None
        return ", ".join(names[:3]) + (", …" if len(value) > 3 else "")
    return None


def _link(record: dict[str, Any], base: Optional[str]) -> Optional[str]:
    sources = [record, *(record.get(name) for name in _NESTED), record.get("_links")]
    candidates: dict[str, Any] = {}
    for source in sources:
        if isinstance(source, dict):
            for key, value in source.items():
                if isinstance(key, str):
                    candidates.setdefault(_norm(key), value)
    for key in _LINK_KEYS:
        url = _safe_url(candidates.get(key), base)
        if url:
            return url
    return None


def _safe_url(value: Any, base: Optional[str]) -> Optional[str]:  # noqa: ANN401
    if isinstance(value, dict):
        value = value.get("href")
    if not isinstance(value, str) or not value or len(value) > _MAX_URL_CHARS:
        return None
    if value.startswith("/") and not value.startswith("//") and base:
        value = base.rstrip("/") + value
    try:
        parts = urlparse(value)
        has_user_info = parts.username is not None or parts.password is not None
    except ValueError:
        return None
    return value if parts.scheme in ("http", "https") and parts.hostname and not has_user_info else None


def _base_url(envelope: Optional[dict[str, Any]]) -> Optional[str]:
    links = envelope.get("_links") if isinstance(envelope, dict) else None
    base = links.get("base") if isinstance(links, dict) else None
    return _safe_url(base, None)


def _declared_total(holder: dict[str, Any], returned: int) -> int:
    for key, value in holder.items():
        is_count = isinstance(value, int) and not isinstance(value, bool)
        if isinstance(key, str) and _norm(key) in _TOTAL_KEYS and is_count and value >= returned:
            return value
    return returned


def _fit(view: dict[str, Any], list_key: str) -> Optional[dict[str, Any]]:
    """`view` within `MAX_VIEW_BYTES` once encoded, dropping rows/fields from the end."""
    while view_bytes(view) > MAX_VIEW_BYTES:
        if not view[list_key]:
            return None
        view[list_key].pop()
    return view if view[list_key] else None


def view_bytes(view: dict[str, Any]) -> int:
    return len(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _mostly_records(items: list[Any]) -> bool:
    return bool(items) and sum(isinstance(item, dict) for item in items) * 5 >= len(items) * 4


def _is_scalar(value: Any) -> bool:  # noqa: ANN401
    return value is None or isinstance(value, (str, int, float, bool))


def _noun(key: Optional[str]) -> str:
    # The key is the server's: a long one isn't a noun.
    if not key or _norm(key) in _GENERIC_NOUNS or len(key) > _MAX_NOUN_CHARS:
        return "results"
    return _humanize(key).lower()


def _singular(noun: str) -> str:
    if noun.endswith("ies") and len(noun) > 3:
        return noun[:-3] + "y"
    if noun.endswith("s") and not noun.endswith("ss"):
        return noun[:-1]
    return noun


def _humanize(key: str) -> str:
    words = _CAMEL.sub(" ", key).replace("_", " ").replace("-", " ").split()
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:] if text else key


def _norm(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "")


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"
