"""The reviewed reasons a pinned decision may change, and the rule that says which reason a change has.

Every change from the snapshot pinned on main must match one rule below; anything else is a
change to an existing type's behaviour and fails ``test_pinned_decisions``. Where it can, a rule
checks the new value against main itself: a spelling that differs only in case or parameters
(``D2``), or another spelling of the same type (``D3``), must now decide exactly as main decided
the plain spelling.

    python -m tests.unit.config.file_types.delta_rules PINNED REPLAY

writes ``allowed_deltas.json`` from a replay (``gen_pinned_decisions --replay``), and fails on any
change no rule explains.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ALLOWED_DELTAS_FILE = Path(__file__).with_name("allowed_deltas.json")

REASONS: dict[str, str] = {
    "D1": "msg, odt, rtf, xlsm, docm, dotx and potx are indexable where main read nothing: gate, "
          "route, parse format, tier and kind",
    "D2": "extensions and mime types match in any case, with a leading dot or mime parameters; the "
          "decision is the one main made for the plain spelling",
    "D3": "every spelling of an indexable type's mime routes as its canonical spelling does",
    "D4": "htm takes the HTML route where main's dispatch refused it (htm with no routed mime)",
    "D5": "FILE records take pyi, mts, cts, scala, groovy, gradle and lua by extension (code parser)",
    "D7": ".log and .cfg are indexable as capped plain text where main refused them (octet-stream, "
          "no mime, a CODE_FILE); stored as text/plain they keep main's full-text path (D-16)",
    "D8": "the macro-enabled workbook mime alone (no extension) names xlsm and is read as a workbook",
    "D10": "the sync filter offers the new types; log moves from exclude-only to offered",
    "D12": "a FILE stored as text/plain whose bytes are a package (msg, odt, rtf, xlsm, docm, dotx, "
           "potx) is read as that package, not as text (listed cell by cell below)",
}

# The lists a reason changes; each list test checks the change is only that.
GLOBAL_REASONS: dict[str, str] = {
    "ExtensionTypes": "D1", "MimeTypes": "D1",
    "EXCLUDABLE_FILE_EXTENSIONS": "D10", "file_extension_filter": "D10", "libreoffice_probe_inputs": "D1",
}

# The only cells main already indexed (gate ok, a handler reached) whose decision may change, each
# reviewed: the raw package bytes main read as Markdown text. Any other such cell changing fails.
EXISTING_CELL_EXCEPTIONS: dict[str, str] = {
    json.dumps([ext, "text/plain", f"file.{ext}", "FILE"]): "D12"
    for ext in ("msg", "odt", "rtf", "xlsm", "docm", "dotx", "potx")
}

NEW_TYPES = frozenset({"msg", "odt", "rtf", "xlsm", "log", "cfg", "docm", "dotx", "potx"})
NEW_MIMES = frozenset({
    "application/vnd.ms-outlook", "application/vnd.oasis.opendocument.text", "application/rtf",
    "text/rtf", "application/x-rtf", "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "application/vnd.openxmlformats-officedocument.presentationml.template",
})
_XLSM_MIME = "application/vnd.ms-excel.sheet.macroenabled.12"
CODE_GRAMMARS = frozenset({"pyi", "mts", "cts", "scala", "groovy", "gradle", "lua"})

COLUMNS = ("extension", "mime", "name", "record_type", "gate", "legacy", "parse_format",
           "tier", "kind", "password_check", "is_code_file", "clears_embeddings_first")


def _ext(value: object) -> object:
    return value.lower().lstrip(".") if isinstance(value, str) else value


def _mime(value: object) -> object:
    return value.split(";")[0].strip().lower() if isinstance(value, str) else value


def _name_for(ext: object) -> str:
    return f"file.{ext.lstrip('.')}" if isinstance(ext, str) and ext.lstrip(".") else "file"


def _key(row: list[Any]) -> str:
    return json.dumps(list(row[:4]), sort_keys=True)


def _canonical_spelling(mime: str) -> str | None:
    """The first spelling of the indexable type a mime spells, when it is another spelling."""
    from app.config import file_types

    ft = file_types._ROUTED_MIMES.get(mime)
    if ft is None or not ft.mimes or ft.mimes[0].lower() == mime:
        return None
    return ft.mimes[0]


class Oracle:
    """The decisions pinned on main, looked up by their inputs."""

    def __init__(self, pinned: dict[str, Any]) -> None:
        self.rows = {json.dumps(r[:4]): r for r in pinned["rows"]}
        self.mimes = dict(pinned["mimes"])

    def row(self, ext: object, mime: object, name: str, record_type: str) -> list[Any] | None:
        for candidate in (name, _name_for(ext)):
            found = self.rows.get(json.dumps([ext, mime, candidate, record_type]))
            if found is not None:
                return found
        return None


def main_indexed(row: list[Any]) -> bool:
    """Whether main read this cell: its gate let it through and its dispatch reached a parser."""
    return row[4] == "ok" and not str(row[5]).startswith("!")


def classify_cell(oracle: Oracle, row: list[Any], column: str, new: object) -> str | None:
    """The reason a row's *column* may now read *new*, or None when no reviewed reason covers it."""
    if main_indexed(row):
        return EXISTING_CELL_EXCEPTIONS.get(json.dumps(list(row[:4])))
    ext, mime, name, record_type = row[:4]
    e, m = _ext(ext), _mime(mime)
    name_ext = name.rsplit(".", 1)[-1].lower() if "." in name else None
    i = COLUMNS.index(column)
    if e in NEW_TYPES or m in NEW_MIMES or (
        name_ext in NEW_TYPES and (record_type == "CODE_FILE" or e in (None, "", "unknown"))
    ):
        decided_by = e if e in NEW_TYPES else name_ext if name_ext in NEW_TYPES else None
        if m == _XLSM_MIME and decided_by is None:
            return "D8" if e in (None, "", "unknown") else None
        return "D7" if decided_by in ("log", "cfg") else "D1"
    if e in CODE_GRAMMARS or (record_type == "CODE_FILE" and name_ext in CODE_GRAMMARS):
        return "D5" if column == "gate" else None
    if e == "htm":
        if column == "gate" or (column == "legacy" and row[5] == "!UnsupportedFormatError"):
            return "D4"
        return None
    plain = oracle.row(e if isinstance(ext, str) else ext, m if isinstance(mime, str) else mime, name, record_type)
    if plain is not None and plain is not oracle.rows.get(json.dumps(row[:4])) and plain[i] == new:
        return "D2"
    canonical = _canonical_spelling(m) if isinstance(m, str) else None
    if canonical is not None:
        spelled = oracle.row(e if isinstance(ext, str) else ext, canonical, name, record_type)
        if spelled is None or spelled[i] == new:
            return "D3"
    return None


def classify_mime(oracle: Oracle, mime: str | None, new: object) -> str | None:
    m = _mime(mime)
    if m == _XLSM_MIME:
        return "D8"
    if m in NEW_MIMES:
        return "D1"
    if isinstance(m, str) and m != mime and oracle.mimes.get(m) == new:
        return "D2"
    return None


def build(pinned: dict[str, Any], replay: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    oracle = Oracle(pinned)
    rows: dict[str, dict[str, Any]] = {}
    unexplained: list[str] = []
    for old, new in zip(pinned["rows"], replay["rows"], strict=True):
        for column, a, b in zip(COLUMNS, old, new, strict=True):
            if a == b:
                continue
            why = classify_cell(oracle, old, column, b)
            if why is None:
                unexplained.append(f"{tuple(old[:4])} {column}: {a!r} -> {b!r}")
                continue
            entry = rows.setdefault(_key(old), {"why": []})
            entry[column] = b
            if why not in entry["why"]:
                entry["why"].append(why)
    mimes: dict[str, dict[str, Any]] = {}
    for (mime, a), (_, b) in zip(pinned["mimes"], replay["mimes"], strict=True):
        if a != b:
            why = classify_mime(oracle, mime, b)
            if why is None:
                unexplained.append(f"mime {mime!r}: {a!r} -> {b!r}")
            else:
                mimes[mime] = {"why": why, "extension": b}
    unexplained.extend(
        f"{section} changed" for section in ("extensions", "docling_names") if pinned[section] != replay[section]
    )
    changed = {k: v for k, v in replay["globals"].items() if pinned["globals"].get(k) != v}
    unexplained.extend(f"list {k} changed" for k in changed if k not in GLOBAL_REASONS)
    return {"rows": rows, "mimes": mimes, "globals": changed}, unexplained


def dump(deltas: dict[str, Any]) -> str:
    """One entry per line, so a reviewed delta reads as a one-line diff."""
    lines = ["{"]
    for section in ("rows", "mimes"):
        items = sorted(deltas[section].items())
        lines.append(f'  "{section}": {{')
        lines.extend(
            f"    {json.dumps(k)}: {json.dumps(v, sort_keys=True)}{',' if i < len(items) - 1 else ''}"
            for i, (k, v) in enumerate(items)
        )
        lines.append("  },")
    lines.append(f'  "globals": {json.dumps(deltas["globals"], indent=2, sort_keys=True)}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    pinned, replay = (json.loads(Path(p).read_text()) for p in argv)
    deltas, unexplained = build(pinned, replay)
    if unexplained:
        print(f"{len(unexplained)} changes no reviewed reason covers:", *unexplained[:50], sep="\n  ")
        return 1
    ALLOWED_DELTAS_FILE.write_text(dump(deltas))
    print(f"wrote {len(deltas['rows'])} rows, {len(deltas['mimes'])} mimes, "
          f"{len(deltas['globals'])} lists to {ALLOWED_DELTAS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
