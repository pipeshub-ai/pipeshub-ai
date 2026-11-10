"""The structural pass: a pre-parse check of the raw push body.

pydantic-core builds the whole JSON tree before it validates, so a key the DTO
refuses (or ignores) still costs memory. This pass runs first, on the bytes, and
refuses everything the DTO would refuse for shape reasons, so a body that reaches
the parser has the same shape class as a valid one and costs no more than the
worst valid document:

1. Lex. One C-speed ``re.sub`` consumes every string literal left to right. A key
   (a literal followed by ``:``) keeps its name; a value string becomes ``""``.
   Keys must be written literally: an escaped key is an unknown key.
2. Scalar lists. A list of more than ``MAX_SCALAR_LIST`` values is refused.
3. Positional keys. A tokenizer over keys and brackets walks the nesting with the
   per-kind field map, which is **derived from the DTO at import** (see
   ``FIELD_MAP``); it refuses an unknown, misplaced or duplicate key, a container
   where a scalar belongs, a container in a list of values, a list in a list, and
   a scalar inside a list of objects (the gap between two tokens must hold only
   whitespace and commas).
4. Exact counts, checked **inside the loop**: more than ``MAX_VALID_OBJECTS``
   objects or ``MAX_VALID_KEYS`` keys is refused at the crossing token, so a
   refused body costs a bounded part of the pass. Then the predictor, a second
   line only.

``metadata.values`` is the only free-form object: its keys must be property
names of the KB schema version the caller named.
"""

from __future__ import annotations

import re
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from typing_extensions import is_typeddict

from app.models.record_metadata import RESERVED_PROPERTY_NAMES

from . import contract as C
from .contract import PushRefusal

S = "scalar"
SL = "scalar_list"
VALUES = "Values"
VALUE_ITEM = "ValueItem"

MAX_SCALAR_LIST = 512
WORKER_BOUND_BYTES = 704 << 20

# One string literal's content, consumed possessively: the regex engine never backtracks into it,
# so each position is scanned a bounded number of times. A string with no closing quote runs to the
# end of the body in one match (the third alternative) instead of failing at every `\"` it holds,
# which was quadratic. A lone trailing backslash is part of that run.
_STRING_BODY = rb'(?:[^"\\]++|\\.|\\\Z)*+'
_KEY_OR_STRING = re.compile(
    rb'"(' + _STRING_BODY + rb')"(?=\s*:)|"' + _STRING_BODY + rb'"|(")' + _STRING_BODY + rb"\Z", re.DOTALL,
)
_LONG_SCALAR_LIST = re.compile(rb"\[(?:[^\[\]{},]*,){%d}" % MAX_SCALAR_LIST)
_TOKEN = re.compile(rb'"(' + _STRING_BODY + rb')"\s*:|[{}\[\]]', re.DOTALL)
_ASTRAL = re.compile(rb"[\xf0-\xf4]|\\u[dD][89abAB]")
_GAP_JUNK = b" \t\r\n,"


# ---- the field map, derived from the DTO ---------------------------------------

_TYPEDDICT_KINDS = {C.ImageData: "BlockData", C.RowData: "BlockData", C.TableData: "GroupData"}


Shape = str | tuple[str, str]


def _combine(a: Shape, b: Shape) -> Shape:
    if a == b:
        return a
    if a == S:
        return b
    if b == S:
        return a
    raise TypeError(f"the DTO gives one field two incompatible shapes: {a!r} and {b!r}")


def _classify(tp: object) -> Shape:
    """The pass's view of a field type: S, SL, a kind name, or ("list", kind)."""
    origin = get_origin(tp)
    if origin is typing.Annotated:
        return _classify(get_args(tp)[0])
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        out: Any = S
        for arg in get_args(tp):
            if arg is type(None):
                continue
            out = _combine(out, _classify(arg))
        return out
    if origin is list:
        item = _classify(get_args(tp)[0])
        if item == S:
            return SL
        if isinstance(item, str) and item not in (SL, VALUES):
            return ("list", item)
        raise TypeError(f"unsupported list item in the DTO: {item!r}")
    if origin is dict:
        return VALUES
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return tp.PASS_KIND
    if is_typeddict(tp):
        return _TYPEDDICT_KINDS[tp]
    return S


def _fields_of(cls: type) -> dict[str, Any]:
    if is_typeddict(cls):
        hints = typing.get_type_hints(cls)
        return {name: _classify(tp) for name, tp in hints.items() if not name.startswith("__")}
    return {name: _classify(f.annotation) for name, f in cls.model_fields.items()}


def _member_classes() -> dict[str, list[type]]:
    """Every DTO class reachable from ``Envelope``, grouped by pass kind."""
    found: dict[str, list[type]] = {}
    queue: list[type] = [C.Envelope]
    while queue:
        cls = queue.pop()
        kind = _TYPEDDICT_KINDS[cls] if is_typeddict(cls) else cls.PASS_KIND
        if cls in found.setdefault(kind, []):
            continue
        found[kind].append(cls)
        for tp in _raw_types(cls):
            queue.extend(_classes_in(tp))
    return found


def _raw_types(cls: type) -> list[Any]:
    if is_typeddict(cls):
        return list(typing.get_type_hints(cls).values())
    return [f.annotation for f in cls.model_fields.values()]


def _classes_in(tp: object) -> list[type]:
    origin = get_origin(tp)
    if origin is not None:
        return [c for arg in get_args(tp) for c in _classes_in(arg)]
    if isinstance(tp, type) and issubclass(tp, BaseModel) and hasattr(tp, "PASS_KIND"):
        return [tp]
    if is_typeddict(tp):
        return [tp]
    return []


def _derive() -> tuple[dict[str, dict[str, Any]], dict[str, list[type]]]:
    members = _member_classes()
    kinds: dict[str, dict[str, Any]] = {}
    for kind, classes in members.items():
        merged: dict[str, Any] = {}
        for cls in classes:
            for name, shape in _fields_of(cls).items():
                merged[name] = _combine(merged[name], shape) if name in merged else shape
        kinds[kind] = merged
    return kinds, members


FIELD_MAP, MEMBERS = _derive()

# Global caps on how many elements a list field can carry in a valid document.
# Ranges are limited by coverage: each block (group) is a child at most once.
_LIST_CAPS = {
    ("Blocks", "blocks"): C.MAX_BLOCKS,
    ("Blocks", "block_groups"): C.MAX_GROUPS,
    ("Children", "block_ranges"): C.MAX_BLOCKS,
    ("Children", "block_group_ranges"): C.MAX_GROUPS,
}


def _keys_per_object(kind: str) -> int:
    if kind == VALUES:
        return C.MAX_VALUES
    return max(len(_fields_of(cls)) for cls in MEMBERS[kind])


def _worst_counts() -> tuple[int, int]:
    """Objects and keys of the largest valid document, from the DTO and its caps."""
    mult: dict[str, int] = {}

    def visit(kind: str, times: int) -> None:
        mult[kind] = mult.get(kind, 0) + times
        for name, shape in FIELD_MAP[kind].items():
            if shape == VALUES:
                mult[VALUES] = mult.get(VALUES, 0) + times
            elif isinstance(shape, tuple):
                visit(shape[1], _LIST_CAPS[(kind, name)])
            elif shape not in (S, SL):
                visit(shape, times)

    visit("Envelope", 1)
    objects = sum(mult.values())
    keys = sum(times * _keys_per_object(kind) for kind, times in mult.items())
    return objects, keys


MAX_VALID_OBJECTS, MAX_VALID_KEYS = _worst_counts()


# ---- the pass --------------------------------------------------------------------

def _refuse(reason: str, detail: str, kind: str | None = None) -> PushRefusal:
    code = "ENVELOPE_INVALID" if kind in ("Envelope", "Meta", VALUES) else "BLOCKS_INVALID"
    return PushRefusal(422, reason, f"{reason}: {detail}", detail, extra={"code": code})


def _unknown(kind: str, name: str, raw: bytes) -> PushRefusal:
    shown = name[:96]
    if kind == VALUES:
        if name in RESERVED_PROPERTY_NAMES:
            return PushRefusal(422, "PROPERTY_RESERVED", f"property {name!r} is reserved", name[:96], extra={"code": "ENVELOPE_INVALID"})
        return PushRefusal(422, "PROPERTY_UNKNOWN", f"property {name!r} is not in the schema", name[:96], extra={"code": "ENVELOPE_INVALID"})
    detail = f"{kind}.{shown}"
    if b"\\" in raw:
        detail += " (an escaped key: write keys literally, without \\u escapes)"
    return _refuse("UNKNOWN_FIELD", detail, kind)


# Bytes of worker memory per counted item, advisory (``parseModel`` "pb-mem-3"). Fitted to clean peaks of
# the whole of task 1 measured from one baseline that includes this pass (``bench_push_worst.py``,
# 2026-10-25), so that the prediction is at least the measurement for each shape:
#
#   shape (body)                 objects   keys      elements   string MiB  measured  predicted
#   worst valid (17.15 MB)       460,001   1,060,005   840,004     0.46      588.7     647.9
#   rich table rows (16.9 MB)    415,006     996,016   747,013     1.41      500.2     601.7
#   worst warnings (16.78 MB)    389,242   1,018,430   885,260     0.54      578.2     602.5
#   sparse cells (17.9 MB)        22,076      66,226 5,705,108     0.18      247.7     318.2
#   astral text (15.73 MB)            17          67        66    15.00      199.1     210.0
#
# ADVISORY, not a bound: it is the cheap second line that refuses an oversized body before the parse. A body of
# many DISTINCT strings in lists (CJK cells, 4-char column names) costs up to 1.8x more per element than the
# fit above, which stays under the 704 MiB worker bound by measurement; the bound itself is held by the structural
# checks, the 740,004-object / 3,720,268-key limits and RLIMIT_AS, not by this formula.
#
# The scalar-list element costs about 40 bytes (the parsed list and its validated copy). It was
# priced at 105 when the structural pass itself cost 220 bytes per string literal (it collected a
# Python object per match); the pass now builds one buffer and its peak is flat in the string count.
PREDICT_BYTES_PER_OBJECT = 480
PREDICT_BYTES_PER_KEY = 390
PREDICT_BYTES_PER_ELEMENT = 52
PREDICT_BYTES_PER_STRING_BYTE = 3
PREDICT_BYTES_PER_ASTRAL_STRING_BYTE = 14


def predicted_bytes(objects: int, keys: int, elements: int, string_bytes: int, *, astral: bool) -> int:
    per_string_byte = PREDICT_BYTES_PER_ASTRAL_STRING_BYTE if astral else PREDICT_BYTES_PER_STRING_BYTE
    return (
        PREDICT_BYTES_PER_OBJECT * objects + PREDICT_BYTES_PER_KEY * keys
        + PREDICT_BYTES_PER_ELEMENT * elements + per_string_byte * string_bytes
    )


def _lex(body: bytes) -> bytes:
    """The body with every value string emptied (``""``); keys keep their text. One left-to-right pass.

    Built piece by piece into one buffer: ``re.sub`` collects a Python object for every string
    literal before it joins them, which cost about 220 bytes per string (600 MiB for a 9 MB body
    of short cells) on top of the parse. An unterminated string is refused here.
    """
    out = bytearray()
    pos = 0
    for m in _KEY_OR_STRING.finditer(body):
        out += body[pos:m.start()]
        if m.group(1) is not None:
            out += m.group(0)  # `"name"`: a key keeps its text
        elif m.group(2) is not None:
            raise PushRefusal(400, "INVALID_JSON", "The body is not valid JSON (a string is not closed).")
        else:
            out += b'""'
        pos = m.end()
    out += body[pos:]
    return bytes(out)


def structure_pass(body: bytes, schema_names: frozenset[str]) -> dict[str, Any]:
    """Refuse a body by shape before any parse; return its exact counts."""
    s1 = _lex(body)
    if _LONG_SCALAR_LIST.search(s1):
        raise _refuse("LIST_TOO_LONG", f"a list holds more than {MAX_SCALAR_LIST} values")
    stack: list[list[Any]] = []  # object: [kind, pending child, seen keys]; list: [None, item kind]
    objects = keys = 0
    prev_end = 0
    rooted = False
    for m in _TOKEN.finditer(s1):
        tok = m.group()
        c = tok[0]
        top = stack[-1] if stack else None
        in_object_list = top is not None and top[0] is None and top[1] != S
        if in_object_list and s1[prev_end:m.start()].strip(_GAP_JUNK):
            raise _refuse("NOT_AN_OBJECT_HERE", f"a value that is not an object in a list of {top[1]}", top[1])
        prev_end = m.end()
        if c == 34:  # a key
            keys += 1
            if keys > MAX_VALID_KEYS:
                raise _refuse("BODY_NOT_VALID_SHAPE", "the body holds more keys than any valid push")
            if top is None or top[0] is None:
                raise _refuse("NOT_AN_OBJECT_HERE", "a key outside an object")
            raw = m.group(1)
            name = raw.decode("utf-8", "replace")
            kind = top[0]
            if kind == VALUES:
                child = VALUE_ITEM if name in schema_names else None
            else:
                child = FIELD_MAP[kind].get(name)
            if child is None:
                raise _unknown(kind, name, raw)
            if name in top[2]:
                raise _refuse("DUPLICATE_FIELD", f"{kind}.{name[:96]}", kind)
            top[2].add(name)
            top[1] = child
        elif c == 123:  # {
            objects += 1
            if objects > MAX_VALID_OBJECTS:
                raise _refuse("BODY_NOT_VALID_SHAPE", "the body holds more objects than any valid push")
            if top is None:
                if rooted:
                    raise _refuse("NOT_AN_OBJECT_HERE", "data after the document")
                rooted = True
                child = "Envelope"
            elif top[0] is None:
                if top[1] == S:
                    raise _refuse("NOT_A_SCALAR", "an object in a list of values")
                child = top[1]
            else:
                child, top[1] = top[1], None
                if child is None or child in (S, SL, VALUE_ITEM) or isinstance(child, tuple):
                    raise _refuse("NOT_AN_OBJECT_HERE", f"{top[0]} holds an object where {child if isinstance(child, str) else 'a list'} belongs", top[0])
            stack.append([child, None, set()])
        elif c == 91:  # [
            if top is None:
                raise _refuse("NOT_AN_OBJECT_HERE", "the body is a list")
            if top[0] is None:
                raise _refuse("NESTED_LIST", "a list inside a list")
            child, top[1] = top[1], None
            if isinstance(child, tuple):
                stack.append([None, child[1]])
            elif child in (SL, VALUE_ITEM):
                stack.append([None, S])
            else:
                raise _refuse("NOT_A_LIST_HERE", f"{top[0]} holds a list where {child} belongs", top[0])
        else:  # } or ]
            if not stack:
                raise _refuse("BODY_NOT_VALID_SHAPE", "unbalanced brackets")
            stack.pop()
    string_bytes = len(body) - len(s1)
    astral = bool(_ASTRAL.search(body))
    elements = s1.count(b",") + s1.count(b"[") + 1
    predicted = predicted_bytes(objects, keys, elements, string_bytes, astral=astral)
    if predicted > WORKER_BOUND_BYTES:
        raise PushRefusal(
            413, "PAYLOAD_TOO_LARGE", "The body is too complex to parse within its budget.",
            extra={"limit": "complexity", "predictedBytes": predicted, "budgetBytes": WORKER_BOUND_BYTES,
                   "hint": "split the document or send fewer, larger blocks"},
        )
    return {"objects": objects, "keys": keys, "elements": elements, "string_bytes": string_bytes, "astral": astral, "predicted_bytes": predicted}
