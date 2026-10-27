"""The public ``blocksFormat: 1`` push contract: request DTO, normaliser and digest.

The DTO is a closed allowlist. Every object forbids unknown keys and typing is
strict (``"5"`` is not an int, ``1`` is not a bool). Block and group ``id`` are
not part of the contract: indexing assigns ids and reconciliation keeps them
stable, so the stored bytes carry none.

Canonical form (``stored_bytes``): the DTO's own ``model_dump_json`` with
``exclude_none`` and ``exclude_defaults``, in field declaration order. An empty
list, an empty object, an all-default sub-object and an omitted key are one form
(``_empty_is_absent``). Field order is part of the stored format: change it, a
default or a serialiser only together with ``NORMALISER``.

Every class here carries a pass kind (``PASS_KIND``) used by ``structure.py`` to
derive the pre-parse field map from this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    UrlConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from typing_extensions import TypedDict

MAX_BLOCKS = 100_000
MAX_GROUPS = 20_000
MAX_RANGES_PER_GROUP = 1_000
MAX_GROUP_DEPTH = 64
MAX_CELLS = 512
MAX_VALUES = 256
MAX_ISSUES = 50
PUSH_FORMAT = 1
#: First input of the digest and prefix of ``externalRevisionId``. Bumped on any
#: change that could alter the canonical form of the same input.
NORMALISER = "pb1"

EXTERNAL_RECORD_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._~:-]{0,255}$"
_EXTERNAL_RECORD_ID = re.compile(EXTERNAL_RECORD_ID_PATTERN)

# Stop a list at its first bad item: the error list stays small whatever the body.
_FAIL_FAST: dict[str, Any] = {"fail_fast": True}


def _str(max_length: int, min_length: int = 0) -> object:
    return Annotated[str, StringConstraints(max_length=max_length, min_length=min_length)]


Text = _str(1 << 20)
Short = _str(1024)
WebUrl = Annotated[AnyUrl, UrlConstraints(max_length=2048, allowed_schemes=["http", "https"], host_required=True)]


class PushRefusal(Exception):
    """A request the push refuses; ``status`` and ``reason`` are the API's."""

    def __init__(self, status: int, reason: str, message: str, location: str | None = None, issues: list[dict] | None = None, extra: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.location = location
        self.issues = issues or []
        self.extra = extra or {}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    #: Required fields whose empty value is real data, not an absent field.
    KEEP_EMPTY: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _empty_is_absent(self) -> Self:
        for name in type(self).model_fields:
            if name in self.KEEP_EMPTY:
                continue
            v = getattr(self, name)
            if v == [] or v == {} or (
                isinstance(v, BaseModel) and v.model_dump(exclude_none=True, exclude_defaults=True) == {}
            ):
                object.__setattr__(self, name, None)
        return self


class Range(_M):
    PASS_KIND: ClassVar[str] = "Range"
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("range start is after its end")
        return self


class Children(_M):
    PASS_KIND: ClassVar[str] = "Children"
    block_ranges: list[Range] | None = Field(None, max_length=MAX_RANGES_PER_GROUP, **_FAIL_FAST)
    block_group_ranges: list[Range] | None = Field(None, max_length=MAX_RANGES_PER_GROUP, **_FAIL_FAST)


# Block and group ``data`` objects are TypedDicts: validated as strictly as models,
# but the value is a plain dict, which is what BlockContainerValidator and
# indexing read. Inside ``data`` there is no null and no empty value: a field is
# present with a value or omitted, so each meaning has exactly one spelling.
class ImageData(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)  # type: ignore[misc]
    uri: _str(28 << 20, 1)


class RowData(TypedDict, total=False):
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)  # type: ignore[misc]
    cells: Annotated[list[Text], Field(min_length=1, max_length=MAX_CELLS, **_FAIL_FAST)]
    row_natural_language_text: _str(1 << 20, 1)
    row_number: Annotated[int, Field(ge=1)]


class TableData(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)  # type: ignore[misc]
    table_summary: Text


class Citation(_M):
    PASS_KIND: ClassVar[str] = "Citation"
    section_title: Short | None = None
    page_number: int | None = Field(None, ge=0)
    line_number: int | None = Field(None, ge=0)
    paragraph_number: int | None = Field(None, ge=0)
    sheet_name: Short | None = None
    row_number: int | None = Field(None, ge=0)
    column_number: int | None = Field(None, ge=0)
    slide_number: int | None = Field(None, ge=0)


class ListMd(_M):
    PASS_KIND: ClassVar[str] = "ListMd"
    list_style: Literal["bullet", "numbered", "checkbox", "dash"] | None = None
    indent_level: int = Field(0, ge=0, le=32)
    item_count: int | None = Field(None, ge=0)


class TableMd(_M):
    PASS_KIND: ClassVar[str] = "TableMd"
    num_of_rows: int | None = Field(None, ge=0)
    num_of_cols: int | None = Field(None, ge=0)
    num_of_cells: int | None = Field(None, ge=0)
    has_header: bool = False
    column_names: list[Short] | None = Field(None, max_length=MAX_CELLS, **_FAIL_FAST)
    captions: list[Short] | None = Field(None, max_length=64, **_FAIL_FAST)
    footnotes: list[Short] | None = Field(None, max_length=64, **_FAIL_FAST)


class RowMd(_M):
    PASS_KIND: ClassVar[str] = "RowMd"
    row_number: int | None = Field(None, ge=0)
    row_span: int | None = Field(None, ge=1)
    is_header: bool = False


class _BlockBase(_M):
    """Field order here is the canonical order; subclasses narrow ``type`` and ``data`` in place."""

    PASS_KIND: ClassVar[str] = "Block"
    index: int = Field(ge=0)
    parent_index: int | None = Field(None, ge=0)
    type: str
    sub_type: Literal["paragraph", "heading", "quote", "list_item", "code", "equation", "divider", "link"] | None = None
    name: Short | None = None
    format: Literal["txt", "markdown", "html", "code", "json", "base64"] | None = None
    data: Any = None
    weburl: WebUrl | None = None
    citation_metadata: Citation | None = None
    list_metadata: ListMd | None = None
    table_row_metadata: RowMd | None = None

    @property
    def parent_block_index(self) -> None:
        # BlockContainerValidator reads this on a BlocksContainer block; a pushed block has none.
        return None


class TextBlock(_BlockBase):
    type: Literal["text"]
    data: Text


class ImageBlock(_BlockBase):
    type: Literal["image"]
    data: ImageData


class TableRowBlock(_BlockBase):
    type: Literal["table_row"]
    data: RowData

    @field_validator("data")
    @classmethod
    def _row_not_empty(cls, v: RowData) -> RowData:
        if "cells" not in v and "row_natural_language_text" not in v:
            raise ValueError("a table row needs cells or row_natural_language_text")
        return v


Block = Annotated[TextBlock | ImageBlock | TableRowBlock, Field(discriminator="type")]


class _GroupBase(_M):
    PASS_KIND: ClassVar[str] = "Group"
    index: int = Field(ge=0)
    parent_index: int | None = Field(None, ge=0)
    type: str
    name: Short | None = None
    description: Text | None = None
    requires_processing: bool = False
    format: Literal["txt", "markdown", "html"] | None = None
    data: Any = None
    children: Children | None = None
    weburl: WebUrl | None = None
    citation_metadata: Citation | None = None
    list_metadata: ListMd | None = None
    table_metadata: TableMd | None = None


class SectionGroup(_GroupBase):
    type: Literal["text_section", "list", "ordered_list"]
    data: Text | None = None


class TableGroup(_GroupBase):
    type: Literal["table"]
    data: TableData | None = None


Group = Annotated[SectionGroup | TableGroup, Field(discriminator="type")]


class Blocks(_M):
    PASS_KIND: ClassVar[str] = "Blocks"
    block_groups: list[Group] | None = Field(None, max_length=MAX_GROUPS, **_FAIL_FAST)
    blocks: list[Block] | None = Field(None, max_length=MAX_BLOCKS, **_FAIL_FAST)

    @model_validator(mode="after")
    def _linear(self) -> Self:
        check_structure(self.blocks or [], self.block_groups or [])
        return self


def check_structure(blocks: list[TextBlock | ImageBlock | TableRowBlock], groups: list[SectionGroup | TableGroup]) -> None:
    """Linear structure checks that bound BlockContainerValidator.

    Indices equal positions, parents are in range, children are covered at most
    once (coverage is summed before any index is touched), and nesting is at most
    ``MAX_GROUP_DEPTH`` with no cycle.
    """
    nb, ng = len(blocks), len(groups)
    for pos, b in enumerate(blocks):
        if b.index != pos:
            raise ValueError(f"blocks[{pos}].index is {b.index}; it must equal its position")
        if b.parent_index is not None and b.parent_index >= ng:
            raise ValueError(f"blocks[{pos}].parent_index is out of range")
    for pos, g in enumerate(groups):
        if g.index != pos:
            raise ValueError(f"block_groups[{pos}].index is {g.index}; it must equal its position")
        if g.parent_index is not None and g.parent_index >= ng:
            raise ValueError(f"block_groups[{pos}].parent_index is out of range")
    cov_b = cov_g = 0
    for gi, g in enumerate(groups):
        if g.children:
            cov_b += sum(r.end - r.start + 1 for r in g.children.block_ranges or [])
            cov_g += sum(r.end - r.start + 1 for r in g.children.block_group_ranges or [])
            if cov_b > nb or cov_g > ng:
                raise ValueError(f"block_groups[{gi}].children cover more children than exist")
    ob, og = [-1] * nb, [-1] * ng
    for gi, g in enumerate(groups):
        if not g.children:
            continue
        for ranges, n, owner in ((g.children.block_ranges, nb, ob), (g.children.block_group_ranges, ng, og)):
            for r in ranges or []:
                if r.end >= n:
                    raise ValueError(f"block_groups[{gi}].children range ends outside the list")
                for i in range(r.start, r.end + 1):
                    if owner[i] != -1:
                        raise ValueError(f"block_groups[{gi}].children lists a child that another range already lists")
                    owner[i] = gi
    depth = [0] * ng
    for s in range(ng):
        path, cur = [], s
        while cur is not None and depth[cur] == 0:
            depth[cur] = -1
            path.append(cur)
            cur = groups[cur].parent_index
        if cur is not None and depth[cur] == -1:
            raise ValueError(f"block_groups[{s}] has a parent cycle")
        base = 0 if cur is None else depth[cur]
        for node in reversed(path):
            base += 1
            if base > MAX_GROUP_DEPTH:
                raise ValueError(f"block_groups nest deeper than {MAX_GROUP_DEPTH}")
            depth[node] = base


# ---- envelope ---------------------------------------------------------------

Finite = Annotated[float, Field(allow_inf_nan=False)]
Value = (
    Short |
    StrictBool |
    StrictInt |
    Finite |
    Annotated[list[Short], Field(max_length=64, **_FAIL_FAST)] |
    None
)


class Meta(_M):
    PASS_KIND: ClassVar[str] = "Meta"
    KEEP_EMPTY: ClassVar[frozenset[str]] = frozenset({"values"})
    schemaId: _str(300, 1)
    values: Annotated[dict[_str(64), Value], Field(max_length=MAX_VALUES)]


_FORMAT_SPECIFIER = re.compile(r"(?<!\w)%(?:\d{1,10}\$)?[#0\-+ ]{0,10}(?:\d{1,10})?(?:\.\d{1,10})?(?:h{1,2}|l{1,2}|L|z|j|t)?[nsxXp]")
_HTML_TAG = re.compile(r"<[^>]{0,1000}>")


class Envelope(_M):
    PASS_KIND: ClassVar[str] = "Envelope"
    recordName: _str(255)
    mimeType: Literal["application/blocks"]
    externalRecordId: Annotated[str, Field(pattern=EXTERNAL_RECORD_ID_PATTERN)]
    version: int = Field(ge=0, le=2**53 - 1)
    blocksFormat: Literal[1]
    sourceLastModifiedTimestamp: int | None = Field(None, gt=0)
    metadata: Meta | None = None
    blocks: Blocks

    @model_validator(mode="after")
    def _empty_is_absent(self) -> Self:
        # `blocks` is required: an empty container is a valid push, not an absent one.
        return self

    @field_validator("blocksFormat", "version", mode="before")
    @classmethod
    def _not_a_bool(cls, v: object) -> object:
        # Literal[1] and int accept True in strict mode; JSON `true` is not a number here.
        if isinstance(v, bool):
            raise ValueError("expected a number, not true or false")
        return v

    @field_validator("recordName")
    @classmethod
    def _record_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("recordName is empty")
        if "<" in v and _HTML_TAG.search(v):
            raise ValueError("recordName must not contain HTML")
        if _FORMAT_SPECIFIER.search(v):
            raise ValueError("recordName must not contain format specifiers")
        return v

    @field_validator("externalRecordId")
    @classmethod
    def _external_id(cls, v: str) -> str:
        # fullmatch: a regex `$` also matches before a trailing newline.
        if not _EXTERNAL_RECORD_ID.fullmatch(v):
            raise ValueError("externalRecordId has characters outside A-Z a-z 0-9 . _ ~ : - or is too long")
        return v


# ---- canonical form ---------------------------------------------------------

def canonical_blocks(blocks: Blocks) -> bytes:
    return blocks.model_dump_json(exclude_none=True, exclude_defaults=True).encode()


def canonical_properties(schema_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """``properties`` of the stored document: coerced values, sorted by name; None is absent."""
    return {"schemaId": schema_id, "values": {k: values[k] for k in sorted(values) if values[k] is not None}}


def canonical_properties_bytes(properties: dict[str, Any] | None) -> bytes:
    if properties is None:
        return b"{}"
    return json.dumps(properties, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


class Stored(_M):
    """The stored layout. ``BlocksContainer`` parses it and ignores ``properties`` and ``push_format``."""

    block_groups: list[Group] | None = None
    blocks: list[Block] | None = None
    properties: dict | None = None
    push_format: int


def stored_bytes(blocks: Blocks, properties: dict[str, Any] | None) -> bytes:
    s = Stored.model_construct(block_groups=blocks.block_groups, blocks=blocks.blocks, properties=properties, push_format=PUSH_FORMAT)
    return s.model_dump_json(exclude_none=True, exclude_defaults=True).encode()


def blocks_digest(canon_blocks: bytes) -> str:
    return hashlib.sha256(canon_blocks).hexdigest()


def request_digest(blocks_sha: str, properties: dict[str, Any] | None) -> str:
    """Dedup key: normaliser tag, blocks digest, canonical properties."""
    h = hashlib.sha256(NORMALISER.encode() + b"\x1f")
    h.update(bytes.fromhex(blocks_sha))
    h.update(canonical_properties_bytes(properties))
    return h.hexdigest()


def content_rev(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def revision_id(digest: str, rev: str) -> str:
    return f"{NORMALISER}:{digest}:{rev}"


def is_pushed_revision(value: str | None) -> bool:
    """Whether an ``externalRevisionId`` was written by a push (any normaliser tag)."""
    return isinstance(value, str) and re.match(r"pb[0-9]+:", value) is not None


def parse_revision_id(value: str | None) -> tuple[str, str, str] | None:
    """``(tag, digest, contentRev)`` of a pushed record's ``externalRevisionId``, else None."""
    m = re.fullmatch(r"(pb[0-9]+):([0-9a-f]{64}):([0-9a-f]{16})", value) if isinstance(value, str) else None
    return (m.group(1), m.group(2), m.group(3)) if m else None


# ---- parse and error translation -------------------------------------------

_TAGS = {"text", "image", "table_row", "table", "text_section", "list", "ordered_list"}


def format_location(loc: tuple) -> str:
    """``blocks[3].data.uri`` from a pydantic loc, dropping the union tag segment."""
    out = ""
    prev_int = False
    for seg in loc:
        if isinstance(seg, int):
            out += f"[{seg}]"
            prev_int = True
            continue
        if prev_int and seg in _TAGS:
            prev_int = False
            continue
        prev_int = False
        out += ("." if out else "") + str(seg)
    return out


def refusal_from_validation_error(e: ValidationError) -> PushRefusal:
    errors = e.errors(include_url=False, include_context=False, include_input=False)[:MAX_ISSUES]
    issues = [{"location": format_location(x["loc"]), "type": x["type"], "message": x["msg"]} for x in errors]
    first = errors[0]
    if any(x["type"] == "json_invalid" for x in errors):
        return PushRefusal(400, "INVALID_JSON", "The body is not valid JSON.", issues=issues[:1])
    roots = {x["loc"][0] if x["loc"] else "" for x in errors}
    if roots - {"blocks", "metadata"}:
        bad = next(x for x in errors if (x["loc"][0] if x["loc"] else "") not in ("blocks", "metadata"))
        return PushRefusal(400, "ENVELOPE_INVALID", "The request envelope is invalid.", format_location(bad["loc"]), issues)
    if "blocks" in roots:
        bad = next(x for x in errors if x["loc"] and x["loc"][0] == "blocks")
        return PushRefusal(422, "BLOCKS_INVALID", "The blocks are invalid.", format_location(bad["loc"]), issues)
    loc = first["loc"]
    name = str(loc[2]) if len(loc) > 2 and loc[1] == "values" else None
    return PushRefusal(422, "PROPERTY_INVALID", "A metadata value is invalid.", name or format_location(loc), issues)


def parse_envelope(body: bytes) -> Envelope:
    try:
        return Envelope.model_validate_json(body)
    except ValidationError as e:
        raise refusal_from_validation_error(e) from None
