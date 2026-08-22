from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from app.exceptions.indexing_exceptions import BlockContainerValidationError
from app.models.blocks import BlockType, BlocksContainer, DataFormat, GroupType, IndexRange
import base64
 
# ── image magic signatures ──────────────────────────────────────────
# Each entry: (prefix_bytes, secondary_bytes_at_offset_8_or_None)
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", None),   # PNG
    (b"\xff\xd8\xff",       None),   # JPEG / JFIF / EXIF
    (b"GIF87a",             None),   # GIF 87a
    (b"GIF89a",             None),   # GIF 89a
    (b"RIFF",               b"WEBP"),# WEBP  (RIFF....WEBP)
    (b"II\x2a\x00",        None),   # TIFF little-endian
    (b"MM\x00\x2a",        None),   # TIFF big-endian
    (b"\x00\x00\x01\x00",  None),   # ICO
    (b"\x00\x00\x02\x00",  None),   # CUR
)
 
 
def _is_image_data(data: bytes) -> bool:
    """Return True if *data* starts with a known image magic signature."""
    if len(data) < 4:
        return False
 
    for prefix, secondary in _IMAGE_SIGNATURES:
        if data[: len(prefix)] == prefix:
            if secondary is not None:
                # RIFF container: verify the format marker at byte 8
                return len(data) >= 12 and data[8:12] == secondary
            return True
 
    # AVIF / HEIC / HEIF — ISO BMFF ftyp box
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis", b"heic", b"heix", b"mif1"):
            return True
 
    return False
 
 
# ── regex helpers ────────────────────────────────────────────────────
_B64_CHARS = r"A-Za-z0-9+/\-_"
 
# data:image/<subtype>;base64,<payload>
_DATA_URI_RE = re.compile(
    rf"data:image/[a-zA-Z0-9.+_-]+;base64,([{_B64_CHARS}=\s]+)",
    re.ASCII,
)
 
# Raw base64 runs — at least 16 contiguous base64 chars (decodes to ≥12
# bytes, enough for every magic signature) optionally followed by more
# base64, whitespace, or padding.
_RAW_B64_RE = re.compile(
    rf"[{_B64_CHARS}]{{16,}}[{_B64_CHARS}=\s]*",
    re.ASCII,
)
 
 
def _decode_b64_prefix(raw: str, decoded_bytes_needed: int = 32) -> Optional[bytes]:
    """Decode just enough of a base64 string to check magic bytes.
 
    Strips whitespace, normalises base64url → standard base64, fixes
    padding, and returns the first *decoded_bytes_needed* decoded bytes
    (or fewer if the input is short).  Returns ``None`` on failure.
    """
    cleaned = re.sub(r"\s+", "", raw)
    if len(cleaned) < 8:          # too short to be any image
        return None
 
    # base64url → standard
    cleaned = cleaned.replace("-", "+").replace("_", "/")
 
    # Only decode enough chars for the prefix we need.
    # 4 base64 chars → 3 bytes
    chars_needed = ((decoded_bytes_needed + 2) // 3) * 4
    snippet = cleaned[:chars_needed]
 
    # Fix padding
    pad = (4 - len(snippet) % 4) % 4
    snippet += "=" * pad
 
    try:
        return base64.b64decode(snippet, validate=True)
    except Exception:
        # Retry without strict validation (ignores stray characters)
        try:
            return base64.b64decode(snippet, validate=False)
        except Exception:
            return None
 
 
# ── public API ───────────────────────────────────────────────────────
_MAX_CANDIDATES = 64  # safety cap to avoid pathological inputs
 
 
def contains_base64_image(text: str) -> bool:
    """Return ``True`` if *text* contains a base64-encoded image substring.
 
    Detection covers two forms:
 
    1. **Data-URI** — ``data:image/<type>;base64,<payload>``
       The payload is decoded and its leading bytes are checked against
       known image magic signatures.
 
    2. **Raw base64** — a bare base64 (or base64url) string whose decoded
       bytes begin with a recognised image header.
 
    Supported formats: PNG, JPEG, GIF, BMP, WEBP, TIFF, ICO, CUR,
    AVIF, HEIC.
 
    The function is designed to be robust against:
      • missing or excess ``=`` padding
      • base64url encoding (``-_`` instead of ``+/``)
      • embedded whitespace / line-breaks (MIME-style)
      • very large input strings (only a prefix is decoded per candidate)
    """
    tried = 0
 
    # ── pass 1: data URIs ────────────────────────────────────────
    for m in _DATA_URI_RE.finditer(text):
        if tried >= _MAX_CANDIDATES:
            break
        tried += 1
        decoded = _decode_b64_prefix(m.group(1))
        if decoded and _is_image_data(decoded):
            return True
 
    # ── pass 2: raw base64 ───────────────────────────────────────
    for m in _RAW_B64_RE.finditer(text):
        if tried >= _MAX_CANDIDATES:
            break
        tried += 1
        decoded = _decode_b64_prefix(m.group())
        if decoded and _is_image_data(decoded):
            return True
 
    return False

class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    location: str


class BlockContainerValidator:
    """
    Runs structural and content validations on a BlocksContainer before embedding.

    All checks always run; errors are raised together as a BlockContainerValidationError.
    Warnings are logged and returned so callers can surface them without aborting.

    Adding a new validation: implement a ``_check_*`` method and call it inside
    ``validate()`` — no other changes required.
    """

    _TEXT_FORMATS = {
        DataFormat.TXT.value,
        DataFormat.MARKDOWN.value,
        DataFormat.CODE.value,
        DataFormat.HTML.value,
        DataFormat.JSON.value,
        DataFormat.YAML.value,
    }

    def __init__(
        self,
        logger=None,
        record_id: Optional[str] = None,
        virtual_record_id: Optional[str] = None,
        record_name: Optional[str] = None,
    ) -> None:
        self._logger = logger
        self._record_id = record_id
        self._virtual_record_id = virtual_record_id
        self._record_name = record_name

    # ──────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────

    def validate(self, container: BlocksContainer) -> List[ValidationIssue]:
        """Run all validations.

        Raises:
            BlockContainerValidationError: if any ERROR-severity issue is found.
        Returns:
            List of WARNING-severity issues (already logged).
        """
        issues: List[ValidationIssue] = []

        blocks = container.blocks or []
        block_groups = container.block_groups or []

        self._check_index_contiguity(blocks, block_groups, issues)
        self._check_parent_child_linkage(blocks, block_groups, issues)
        self._check_text_blocks(blocks, issues)
        self._check_image_blocks(blocks, issues)
        self._check_table_row_blocks(blocks, block_groups, issues)
        self._check_table_groups(block_groups, blocks, issues)

        warnings = [i for i in issues if i.severity == Severity.WARNING]
        errors = [i for i in issues if i.severity == Severity.ERROR]

        record_context = self._format_record_context()

        for w in warnings:
            if self._logger:
                self._logger.warning(
                    "[block-validation]%s [%s] %s: %s",
                    record_context, w.location, w.code, w.message
                )

        if errors:
            raise BlockContainerValidationError(
                errors=errors,
                record_id=self._record_id,
                virtual_record_id=self._virtual_record_id,
                record_name=self._record_name,
            )

        return warnings

    # ──────────────────────────────────────────────────────────────────────
    # 1. Index contiguity & uniqueness
    # ──────────────────────────────────────────────────────────────────────

    def _check_index_contiguity(self, blocks, block_groups, issues) -> None:
        self._check_list_indices("block", blocks, issues)
        self._check_list_indices("block_group", block_groups, issues)

    @staticmethod
    def _check_list_indices(label: str, items, issues: list) -> None:
        seen: dict[int, int] = {}
        for pos, item in enumerate(items):
            idx = item.index
            loc = f"{label}[{pos}]"

            if idx is None:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="INDEX_NULL",
                    message=f"{label}.index is null",
                    location=loc,
                ))
                continue

            if idx != pos:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="INDEX_MISMATCH",
                    message=f"{label}.index={idx} does not match array position {pos}",
                    location=loc,
                ))

            if idx in seen:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="INDEX_DUPLICATE",
                    message=f"{label}.index={idx} already used at position {seen[idx]}",
                    location=loc,
                ))
            else:
                seen[idx] = pos

    # ──────────────────────────────────────────────────────────────────────
    # 2. Parent / child linkage
    # ──────────────────────────────────────────────────────────────────────

    def _check_parent_child_linkage(self, blocks, block_groups, issues) -> None:
        n_blocks = len(blocks)
        n_groups = len(block_groups)

        # block.parent_index must point at a valid group
        for i, block in enumerate(blocks):
            p = block.parent_index
            if p is not None and not (0 <= p < n_groups):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="PARENT_INDEX_OUT_OF_RANGE",
                    message=f"block.parent_index={p} out of range [0, {n_groups})",
                    location=f"block[{i}]",
                ))

        # block_group.parent_index must point at a valid group with no cycles
        for i, group in enumerate(block_groups):
            p = group.parent_index
            if p is None:
                continue
            if not (0 <= p < n_groups):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="PARENT_INDEX_OUT_OF_RANGE",
                    message=f"block_group.parent_index={p} out of range [0, {n_groups})",
                    location=f"block_group[{i}]",
                ))
                continue

            visited = {i}
            curr = p
            cycle_detected = False
            while curr is not None:
                if curr in visited:
                    cycle_detected = True
                    break
                if not (0 <= curr < n_groups):
                    break
                visited.add(curr)
                curr = block_groups[curr].parent_index

            if cycle_detected:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="PARENT_INDEX_CYCLE",
                    message=f"block_group[{i}].parent_index={p} introduces a cyclic reference",
                    location=f"block_group[{i}]",
                ))

        # Forward children: ranges must be in-bounds and cross-reference parent_index correctly
        children_block_membership: set[tuple[int, int]] = set()
        children_group_membership: set[tuple[int, int]] = set()

        for gi, group in enumerate(block_groups):
            if group.children is None:
                continue
            loc = f"block_group[{gi}]"

            for r in (group.children.block_ranges or []):
                if not self._append_if_block_range_invalid(r, n_blocks, loc, issues):
                    continue
                for bi in range(r.start, r.end + 1):
                    children_block_membership.add((gi, bi))
                    child = blocks[bi]
                    if child.parent_index is not None and child.parent_index != gi:
                        issues.append(ValidationIssue(
                            severity=Severity.WARNING,
                            code="CHILDREN_PARENT_INDEX_MISMATCH",
                            message=(
                                f"block[{bi}].parent_index={child.parent_index} "
                                f"but block is listed as child of block_group[{gi}]"
                            ),
                            location=loc,
                        ))

            for r in (group.children.block_group_ranges or []):
                if not self._append_if_group_range_invalid(r, n_groups, loc, issues):
                    continue
                for cgi in range(r.start, r.end + 1):
                    children_group_membership.add((gi, cgi))
                    child_group = block_groups[cgi]
                    if child_group.parent_index is not None and child_group.parent_index != gi:
                        issues.append(ValidationIssue(
                            severity=Severity.WARNING,
                            code="CHILDREN_PARENT_INDEX_MISMATCH",
                            message=(
                                f"block_group[{cgi}].parent_index={child_group.parent_index} "
                                f"but group is listed as child of block_group[{gi}]"
                            ),
                            location=loc,
                        ))

        # Reverse linkage: a block/group declaring a parent should appear in that parent's children
        for i, block in enumerate(blocks):
            p = block.parent_index
            if p is not None and 0 <= p < n_groups and (p, i) not in children_block_membership:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="REVERSE_LINKAGE_MISSING",
                    message=(
                        f"block[{i}].parent_index={p} but block[{i}] is absent "
                        f"from block_group[{p}].children.block_ranges"
                    ),
                    location=f"block[{i}]",
                ))

        for i, group in enumerate(block_groups):
            p = group.parent_index
            if p is not None and 0 <= p < n_groups and (p, i) not in children_group_membership:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="REVERSE_LINKAGE_MISSING",
                    message=(
                        f"block_group[{i}].parent_index={p} but block_group[{i}] is absent "
                        f"from block_group[{p}].children.block_group_ranges"
                    ),
                    location=f"block_group[{i}]",
                ))

    # ──────────────────────────────────────────────────────────────────────
    # 3. TEXT blocks
    # ──────────────────────────────────────────────────────────────────────

    def _check_text_blocks(self, blocks, issues) -> None:
        for i, block in enumerate(blocks):
            if self._block_type(block) != BlockType.TEXT.value:
                continue
            loc = f"block[{i}](text)"

            # data must be a string — spaCy at vectorstore.py:1147 will crash on non-string
            if not isinstance(block.data, str):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TEXT_DATA_NOT_STRING",
                    message=(
                        f"data must be a string (empty string allowed), "
                        f"got {type(block.data).__name__}"
                    ),
                    location=loc,
                ))
            elif contains_base64_image(block.data):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TEXT_DATA_CONTAINS_BASE64_IMAGE",
                    message=(
                        "data contains an embedded base64 image; "
                        "images must be stored in IMAGE blocks, not TEXT blocks"
                    ),
                    location=loc,
                ))

            fmt = self._format(block.format)
            if fmt is None:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TEXT_FORMAT_MISSING",
                    message="format is missing; expected one of 'txt', 'markdown', 'code', 'html'",
                    location=loc,
                ))
            elif fmt not in self._TEXT_FORMATS:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TEXT_FORMAT_UNEXPECTED",
                    message=f"format='{fmt}' is unusual for a text block; expected one of 'txt', 'markdown', 'code', 'html'",
                    location=loc,
                ))

    # ──────────────────────────────────────────────────────────────────────
    # 4. IMAGE blocks
    # ──────────────────────────────────────────────────────────────────────

    def _check_image_blocks(self, blocks, issues) -> None:
        for i, block in enumerate(blocks):
            if self._block_type(block) != BlockType.IMAGE.value:
                continue
            loc = f"block[{i}](image)"

            # format must be "base64"; document_extraction.py:198 does block.format.value
            # which crashes if format is None
            fmt = self._format(block.format)
            if fmt != DataFormat.BASE64.value:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="IMAGE_FORMAT_NOT_BASE64",
                    message=f"image block format must be 'base64', got {fmt!r}",
                    location=loc,
                ))

            data = block.data
            if data is None:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="IMAGE_DATA_MISSING",
                    message="image block data is missing; block will not be embedded",
                    location=loc,
                ))
                continue

            if isinstance(data, str):
                # Legacy raw base64 string — silently skipped by vectorstore (no .get("uri"))
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="IMAGE_DATA_NOT_NORMALIZED",
                    message='image data is a raw string; normalize to {"uri": "..."} so the block is embedded',
                    location=loc,
                ))
            elif isinstance(data, dict):
                uri = data.get("uri")
                if not uri:
                    issues.append(ValidationIssue(
                        severity=Severity.WARNING,
                        code="IMAGE_URI_EMPTY",
                        message="image data.uri is empty or missing; block will not be embedded",
                        location=loc,
                    ))
                elif not isinstance(uri, str):
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="IMAGE_URI_INVALID_TYPE",
                        message=f"image data.uri must be a string, got {type(uri).__name__}",
                        location=loc,
                    ))
                elif not self._is_valid_image_uri(uri):
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="IMAGE_URI_INVALID",
                        message="data.uri must be a data:image/...;base64,... URI",
                        location=loc,
                    ))
            else:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="IMAGE_DATA_INVALID_TYPE",
                    message=f"image data must be a dict or string, got {type(data).__name__}",
                    location=loc,
                ))

    @staticmethod
    def _is_valid_image_uri(uri: str) -> bool:
        uri = uri.strip()
        if uri.startswith("data:image/") and ";base64," in uri:
            return True
        return False

    # ──────────────────────────────────────────────────────────────────────
    # 5. TABLE_ROW blocks
    # ──────────────────────────────────────────────────────────────────────

    def _check_table_row_blocks(self, blocks, block_groups, issues) -> None:
        n_groups = len(block_groups)
        fragment_parent_indices = self._block_indices_with_fragment_children(blocks)
        for i, block in enumerate(blocks):
            if self._block_type(block) != BlockType.TABLE_ROW.value:
                continue
            loc = f"block[{i}](table_row)"

            fmt = self._format(block.format)
            if fmt != DataFormat.JSON.value:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_FORMAT_NOT_JSON",
                    message=f"table_row block format must be 'json', got {fmt!r}",
                    location=loc,
                ))

            data = block.data
            if data is None:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_DATA_MISSING",
                    message="table_row block data is missing; block cannot be embedded",
                    location=loc,
                ))
                self._check_table_row_parent(block, block_groups, n_groups, i, loc, issues)
                continue

            if not isinstance(data, dict):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_DATA_NOT_DICT",
                    message=f"table_row block data must be a dict, got {type(data).__name__}",
                    location=loc,
                ))
                # Skip content checks — data shape is wrong
                self._check_table_row_parent(block, block_groups, n_groups, i, loc, issues)
                continue

            self._check_table_row_parent(block, block_groups, n_groups, i, loc, issues)

            # Embedding content: cells OR row_natural_language_text must be present
            cells = data.get("cells")
            row_text = data.get("row_natural_language_text")
            has_cells = isinstance(cells, list) and len(cells) > 0

            if row_text is not None and not isinstance(row_text, str):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_TEXT_NOT_STRING",
                    message=(
                        f"row_natural_language_text must be a string, "
                        f"got {type(row_text).__name__}"
                    ),
                    location=loc,
                ))
            elif not has_cells:
                row_container_index = block.index if block.index is not None else i
                has_fragment_children = row_container_index in fragment_parent_indices
                if not has_fragment_children and (
                    not isinstance(row_text, str) or not row_text.strip()
                ):
                    issues.append(ValidationIssue(
                        severity=Severity.WARNING,
                        code="TABLE_ROW_NO_EMBEDDABLE_CONTENT",
                        message=(
                            "table_row has no non-empty cells list and "
                            "row_natural_language_text is absent or empty; block cannot be embedded"
                        ),
                        location=loc,
                    ))

            # row_number optional but must be int >= 1
            row_number = data.get("row_number")
            if row_number is not None and (not isinstance(row_number, int) or row_number < 1):
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TABLE_ROW_NUMBER_INVALID",
                    message=f"row_number should be int >= 1, got {row_number!r}",
                    location=loc,
                ))

            # cells must be list of strings
            if cells is not None:
                if not isinstance(cells, list):
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="TABLE_ROW_CELLS_NOT_LIST",
                        message=f"cells must be a list, got {type(cells).__name__}",
                        location=loc,
                    ))
                else:
                    for j, cell in enumerate(cells):
                        if not isinstance(cell, str):
                            issues.append(ValidationIssue(
                                severity=Severity.ERROR,
                                code="TABLE_ROW_CELL_NOT_STRING",
                                message=f"cells[{j}] must be a string, got {type(cell).__name__}",
                                location=loc,
                            ))

    @staticmethod
    def _check_table_row_parent(block, block_groups, n_groups, i, loc, issues) -> None:
        """Validate that table_row.parent_index points at a table-type group."""
        if block.parent_index is None:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="TABLE_ROW_PARENT_INDEX_MISSING",
                message="table_row block must have parent_index pointing to a table group",
                location=loc,
            ))
        elif 0 <= block.parent_index < n_groups:
            parent = block_groups[block.parent_index]
            parent_type = (
                parent.type.value if hasattr(parent.type, "value") else str(parent.type)
            )
            if parent_type != GroupType.TABLE.value:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_PARENT_NOT_TABLE",
                    message=(
                        f"table_row parent block_group[{block.parent_index}] "
                        f"has type '{parent_type}', expected 'table'"
                    ),
                    location=loc,
                ))

    # ──────────────────────────────────────────────────────────────────────
    # 6. TABLE block groups
    # ──────────────────────────────────────────────────────────────────────

    def _check_table_groups(self, block_groups, blocks, issues) -> None:
        n_blocks = len(blocks)
        for gi, group in enumerate(block_groups):
            if self._group_type(group) != GroupType.TABLE.value:
                continue
            loc = f"block_group[{gi}](table)"

            # Every child block must be table_row
            if group.children:
                for r in (group.children.block_ranges or []):
                    if not self._is_index_range_in_bounds(r, n_blocks):
                        continue
                    for bi in range(r.start, r.end + 1):
                        child_type = self._block_type(blocks[bi])
                        if child_type != BlockType.TABLE_ROW.value:
                            issues.append(ValidationIssue(
                                severity=Severity.ERROR,
                                code="TABLE_CHILD_NOT_TABLE_ROW",
                                message=(
                                    f"table group child block[{bi}] has type '{child_type}', "
                                    "expected 'table_row'"
                                ),
                                location=loc,
                            ))

            # table_metadata presence and num_of_cells
            tm = group.table_metadata
            if tm is None:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TABLE_METADATA_MISSING",
                    message="table group has no table_metadata",
                    location=loc,
                ))
            else:
                num_cells: Optional[int] = (
                    tm.get("num_of_cells") if isinstance(tm, dict)
                    else getattr(tm, "num_of_cells", None)
                )
                if num_cells is None:
                    issues.append(ValidationIssue(
                        severity=Severity.WARNING,
                        code="TABLE_METADATA_NUM_CELLS_MISSING",
                        message="table_metadata.num_of_cells is missing; table will be treated as 'large' during indexing",
                        location=loc,
                    ))
                elif not isinstance(num_cells, int) or num_cells < 0:
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="TABLE_METADATA_NUM_CELLS_INVALID",
                        message=f"table_metadata.num_of_cells must be int >= 0, got {num_cells!r}",
                        location=loc,
                    ))

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _block_indices_with_fragment_children(blocks) -> set[int]:
        """Block indices referenced by image-split fragment children."""
        parents: set[int] = set()
        for block in blocks:
            if block.parent_block_index is not None:
                parents.add(block.parent_block_index)
        return parents

    @staticmethod
    def _is_index_range_in_bounds(r: IndexRange, upper_bound: int) -> bool:
        """True when [start, end] is non-inverted and fully within [0, upper_bound)."""
        if r.start < 0 or r.start > r.end or r.end >= upper_bound:
            return False
        return True

    @staticmethod
    def _append_if_block_range_invalid(
        r: IndexRange,
        n_blocks: int,
        location: str,
        issues: List[ValidationIssue],
    ) -> bool:
        """Return True when the range is safe to iterate; append one error otherwise."""
        if BlockContainerValidator._is_index_range_in_bounds(r, n_blocks):
            return True
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            code="CHILDREN_BLOCK_INDEX_OUT_OF_RANGE",
            message=(
                f"children.block_ranges [{r.start}, {r.end}] is out of range "
                f"for n_blocks={n_blocks}"
            ),
            location=location,
        ))
        return False

    @staticmethod
    def _append_if_group_range_invalid(
        r: IndexRange,
        n_groups: int,
        location: str,
        issues: List[ValidationIssue],
    ) -> bool:
        """Return True when the range is safe to iterate; append one error otherwise."""
        if BlockContainerValidator._is_index_range_in_bounds(r, n_groups):
            return True
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            code="CHILDREN_GROUP_INDEX_OUT_OF_RANGE",
            message=(
                f"children.block_group_ranges [{r.start}, {r.end}] is out of range "
                f"for n_groups={n_groups}"
            ),
            location=location,
        ))
        return False

    def _format_record_context(self) -> str:
        """Build a log-friendly record context string."""
        parts = []
        if self._record_id:
            parts.append(f"record_id={self._record_id}")
        if self._virtual_record_id:
            parts.append(f"vrid={self._virtual_record_id}")
        if self._record_name:
            parts.append(f"name={self._record_name!r}")
        return f" [{', '.join(parts)}]" if parts else ""

    @staticmethod
    def _block_type(block) -> Optional[str]:
        t = getattr(block, "type", None)
        if t is None:
            return None
        return t.value if hasattr(t, "value") else str(t)

    @staticmethod
    def _group_type(group) -> Optional[str]:
        t = getattr(group, "type", None)
        if t is None:
            return None
        return t.value if hasattr(t, "value") else str(t)

    @staticmethod
    def _format(fmt) -> Optional[str]:
        if fmt is None:
            return None
        return fmt.value if hasattr(fmt, "value") else str(fmt)

