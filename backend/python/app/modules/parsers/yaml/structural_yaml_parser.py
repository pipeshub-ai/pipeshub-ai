"""Structural chunker for YAML that PyYAML cannot load.

Used by :class:`YAMLParser` when ``yaml.safe_load_all`` fails: templated
YAML (Helm/Go, Jinja, ERB, Mustache -- see
:mod:`app.modules.parsers.yaml.template_dialect`) and hand-written files
with plain syntax errors (tabs, ``files: *.js``, ``desc: Note: x``).

The file is never deserialised. Blocks are verbatim slices of the source,
cut along the YAML structure that indentation reveals:

- each ``---`` document becomes a group (named ``kind name`` for k8s-style
  documents);
- consecutive small top-level keys are merged into one block, large keys
  become a group whose interior is chunked one indent level deeper;
- a template scope (``{{- if }}`` ... ``{{- end }}``) is kept whole when it
  fits in a block, otherwise it becomes a group named after its condition;
- every block is prefixed with a comment carrying its dotted path and the
  template scopes open at its first line, so a chunk taken from inside
  ``{{- else }}`` still says which condition it belongs to.

Nothing is rewritten, so the meaning of the template is exactly what is in
the file; values are references (``{{ .Values.replicaCount }}``), not the
numbers Helm would render.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.modules.parsers.yaml.template_dialect import (
    DIALECTS,
    ControlTag,
    DialectSpec,
    TemplateDialect,
)

__all__ = ["StructuralParse", "StructuralYAMLParser"]

# A unit longer than this is split one indent level deeper (when it has
# nested content); consecutive smaller units are merged up to the target.
MAX_BLOCK_LINES = 60
TARGET_BLOCK_LINES = 40
MAX_NESTING = 8

_BLOCK_SCALAR = re.compile(r"^[|>][-+0-9]*(\s+#.*)?$")
_MASK_CHAR = ""


@dataclass
class _Line:
    number: int          # 1-based physical line of the first row of this unit
    text: str            # contains "\n" when a template tag spans lines
    indent: int
    kind: str            # blank | comment | tag | content | doc_sep
    control: ControlTag | None = None
    path: str = ""       # dotted YAML path the line belongs to / defines
    value: str = ""      # scalar after ``key:``, verbatim
    scopes: tuple[tuple[str, int], ...] = ()  # (label, opener index) open before this line
    match: int | None = None  # for scope openers: index of the matching closer


@dataclass
class _Frame:
    indent: int
    segment: str
    is_seq: bool = False
    count: int = 0


@dataclass
class _Unit:
    start: int
    end: int
    is_scope: bool = False


@dataclass
class _Context:
    record_name: str
    lines: list[_Line]
    blocks: list[Block] = field(default_factory=list)
    groups: list[BlockGroup] = field(default_factory=list)


@dataclass
class StructuralParse:
    container: BlocksContainer
    document_count: int


def _content_hash(text: str) -> str:
    data = text.encode("utf-8")
    return f"{hashlib.sha256(data).hexdigest()}:{hashlib.md5(data).hexdigest()}"


def _indent_of(text: str) -> int:
    first = text.split("\n", 1)[0]
    lead = first[: len(first) - len(first.lstrip(" \t"))]
    return len(lead.expandtabs(2))


def _split_key(masked: str) -> tuple[int, int] | None:
    """Offsets ``(key_end, value_start)`` of a ``key: value`` line, else None."""
    if not masked or masked[0] in "[{":
        return None
    if masked[0] in "\"'":
        quote = masked[0]
        j = 1
        while j < len(masked) and masked[j] != quote:
            j += 2 if masked[j] == "\\" and quote == '"' else 1
        if j >= len(masked):
            return None
        m = re.match(r"\s*:(?:\s+|$)", masked[j + 1:])
        if not m:
            return None
        return j + 1, j + 1 + m.end()
    m = re.search(r":(?=\s|$)", masked)
    if not m or re.search(r"(^|\s)#", masked[: m.start()]):
        return None
    value_start = m.end()
    while value_start < len(masked) and masked[value_start] == " ":
        value_start += 1
    return m.start(), value_start


class StructuralYAMLParser:
    """Verbatim, indentation-driven chunker for unloadable YAML."""

    def parse_text(
        self, text: str, record_name: str, dialect: TemplateDialect | None
    ) -> StructuralParse:
        spec = DIALECTS[dialect] if dialect else None
        lines = self._analyze(text, spec)
        ctx = _Context(record_name=record_name, lines=lines)
        root = BlockGroup(
            index=0,
            type=GroupType.KEY_VALUE_AREA,
            sub_type=GroupSubType.RECORD,
            name=record_name,
            format=DataFormat.YAML,
        )
        ctx.groups.append(root)

        documents = self._documents(lines)
        if len(documents) <= 1:
            start, end = documents[0] if documents else (0, len(lines))
            self._emit(ctx, start, end, root, "", 0)
        else:
            group_indices = []
            for n, (start, end) in enumerate(documents, 1):
                group = self._new_group(ctx, self._document_name(lines, start, end, n), root, "")
                self._emit(ctx, start, end, group, "", 1)
                group_indices.append(group.index)
            root.children = BlockGroupChildren.from_indices([], group_indices)

        flavour = f"{dialect.value}-templated YAML" if dialect else "YAML (not loadable, chunked by structure)"
        root.data = {
            "summary": (
                f"{flavour} record '{record_name}' with {max(len(documents), 1)} "
                f"document(s) and {len(ctx.blocks)} block(s)."
            )
        }
        root.content_hash = _content_hash(text)
        return StructuralParse(
            container=BlocksContainer(blocks=ctx.blocks, block_groups=ctx.groups),
            document_count=max(len(documents), 1),
        )

    # ------------------------------------------------------------------
    # Line analysis
    # ------------------------------------------------------------------

    def _analyze(self, text: str, spec: DialectSpec | None) -> list[_Line]:
        lines = self._classify(self._split_units(text, spec), spec)
        self._assign_paths(lines, spec)
        self._assign_scopes(lines)
        return lines

    @staticmethod
    def _split_units(text: str, spec: DialectSpec | None) -> list[tuple[int, str]]:
        """Physical lines, with a tag that spans lines joined into one unit."""
        units: list[tuple[int, str]] = []
        buffer: str | None = None
        first = 0
        for number, raw in enumerate(text.split("\n"), 1):
            if buffer is None:
                buffer, first = raw, number
            else:
                buffer += "\n" + raw
            balanced = spec is None or all(
                buffer.count(open_) <= buffer.count(close) for open_, close in spec.delimiters
            )
            if balanced:
                units.append((first, buffer))
                buffer = None
        if buffer is not None:
            units.append((first, buffer))
        return units

    @staticmethod
    def _classify(units: list[tuple[int, str]], spec: DialectSpec | None) -> list[_Line]:
        lines: list[_Line] = []
        block_scalar_indent: int | None = None
        for number, text in units:
            stripped = text.strip()
            indent = _indent_of(text)
            if block_scalar_indent is not None:
                if not stripped or indent > block_scalar_indent:
                    lines.append(_Line(number, text, indent, "content"))
                    continue
                block_scalar_indent = None

            if not stripped:
                kind = "blank"
            elif stripped in ("---", "...") or stripped.startswith("--- "):
                kind = "doc_sep"
            elif stripped.startswith("#"):
                kind = "comment"
            elif spec is not None and not spec.tag.sub("", text).strip():
                kind = "tag"
            else:
                kind = "content"

            line = _Line(number, text, indent, kind)
            if kind == "tag" and spec is not None:
                tags = spec.tag.findall(text)
                if len(tags) == 1:
                    line.control = spec.classify(tags[0])
            if kind == "content" and _opens_block_scalar(text, indent, spec):
                block_scalar_indent = indent
            lines.append(line)
        return lines

    @staticmethod
    def _assign_paths(lines: list[_Line], spec: DialectSpec | None) -> None:
        stack: list[_Frame] = []
        for line in lines:
            if line.kind == "doc_sep":
                stack.clear()
                continue
            if line.kind != "content":
                line.path = _path_of(stack)
                continue
            _analyze_content(line, stack, spec)

    @staticmethod
    def _assign_scopes(lines: list[_Line]) -> None:
        # (opener label, current branch label, opener index)
        stack: list[tuple[str, str, int]] = []
        for idx, line in enumerate(lines):
            line.scopes = tuple((current, opener) for _, current, opener in stack)
            control = line.control
            if control is None:
                continue
            if control.role == "open":
                stack.append((control.label, control.label, idx))
            elif control.role == "middle" and stack:
                opener_label, _, opener = stack[-1]
                stack[-1] = (opener_label, f"{control.label} (after: {opener_label})", opener)
            elif control.role == "close" and stack:
                _, _, opener = stack.pop()
                lines[opener].match = idx

    @staticmethod
    def _documents(lines: list[_Line]) -> list[tuple[int, int]]:
        documents: list[tuple[int, int]] = []
        start = 0
        for idx, line in enumerate([*lines, _Line(0, "", 0, "doc_sep")]):
            if line.kind != "doc_sep":
                continue
            if any(x.kind in ("content", "tag") for x in lines[start:idx]):
                documents.append((start, idx))
            start = idx + 1
        return documents

    @staticmethod
    def _document_name(lines: list[_Line], start: int, end: int, n: int) -> str:
        kind = name = ""
        for line in lines[start:end]:
            if line.kind != "content":
                continue
            if line.path == "kind":
                kind = line.value
            elif line.path == "metadata.name":
                name = line.value
        return " ".join(part for part in (kind, name) if part) or f"document {n}"

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _emit(
        self,
        ctx: _Context,
        start: int,
        end: int,
        parent: BlockGroup,
        parent_path: str,
        depth: int,
        header_block: int | None = None,
    ) -> None:
        lines = ctx.lines
        content_indents = [x.indent for x in lines[start:end] if x.kind == "content"]
        level = min(content_indents) if content_indents else 0
        units = self._units(lines, start, end, level)

        block_indices = [header_block] if header_block is not None else []
        group_indices: list[int] = []
        pending: list[_Unit] = []

        def flush() -> None:
            if pending:
                merged = len(pending) > 1 or pending[0].is_scope
                block_indices.append(
                    self._add_block(ctx, pending[0].start, pending[-1].end, parent, parent_path, level, merged=merged)
                )
                pending.clear()

        for unit in units:
            size = unit.end - unit.start
            if size > MAX_BLOCK_LINES and depth < MAX_NESTING:
                header_end = self._header_end(lines, unit, level)
                nested = header_end is not None and any(
                    x.kind == "content" for x in lines[header_end:unit.end]
                )
                if nested:
                    flush()
                    group_indices.append(
                        self._emit_nested(ctx, unit, header_end, parent, parent_path, level, depth)
                    )
                    continue
            if pending and (pending[-1].end - pending[0].start) + size > TARGET_BLOCK_LINES:
                flush()
            pending.append(unit)
        flush()
        parent.children = BlockGroupChildren.from_indices(block_indices, group_indices)

    @staticmethod
    def _units(lines: list[_Line], start: int, end: int, level: int) -> list[_Unit]:
        """Split ``[start, end)`` at content lines of ``level`` and at whole template scopes."""
        units: list[_Unit] = []
        unit_start = start
        idx = start

        def cut_before(boundary: int) -> int:
            # Blank lines, comments and non-closing tag lines directly above a
            # boundary describe what follows; give them to the next unit.
            cut = boundary
            while cut > unit_start:
                prev = lines[cut - 1]
                if prev.kind in ("blank", "comment") or (
                    prev.kind == "tag" and prev.indent <= level
                    and (prev.control is None or prev.control.role != "close")
                ):
                    cut -= 1
                else:
                    break
            return cut

        while idx < end:
            line = lines[idx]
            is_boundary = line.kind == "content" and line.indent <= level
            is_scope = (
                line.kind == "tag" and line.indent <= level
                and line.control is not None and line.control.role == "open"
                and line.match is not None and line.match < end
            )
            if not (is_boundary or is_scope):
                idx += 1
                continue
            cut = cut_before(idx)
            if cut > unit_start and any(x.kind != "blank" for x in lines[unit_start:cut]):
                units.append(_Unit(unit_start, cut))
            unit_start = cut
            if is_scope:
                units.append(_Unit(unit_start, line.match + 1, is_scope=True))
                unit_start = idx = line.match + 1
            else:
                idx += 1
        if unit_start < end and any(x.kind != "blank" for x in lines[unit_start:end]):
            units.append(_Unit(unit_start, end))
        return units

    @staticmethod
    def _header_end(lines: list[_Line], unit: _Unit, level: int) -> int | None:
        """Index just past the line that names *unit*: its scope opener or its key."""
        for i in range(unit.start, unit.end):
            line = lines[i]
            if unit.is_scope and line.control is not None and line.match is not None:
                return i + 1
            if not unit.is_scope and line.kind == "content" and line.indent <= level:
                return i + 1
        return None

    def _emit_nested(
        self,
        ctx: _Context,
        unit: _Unit,
        header_end: int,
        parent: BlockGroup,
        parent_path: str,
        level: int,
        depth: int,
    ) -> int:
        head = ctx.lines[header_end - 1]
        if unit.is_scope:
            name, path = head.control.label, parent_path
        else:
            name = path = head.path
        group = self._new_group(ctx, name, parent, path)
        header = self._add_block(ctx, unit.start, header_end, group, path, level, merged=False)
        self._emit(ctx, header_end, unit.end, group, path, depth + 1, header_block=header)
        return group.index

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    @staticmethod
    def _new_group(ctx: _Context, name: str, parent: BlockGroup, path: str) -> BlockGroup:
        group = BlockGroup(
            index=len(ctx.groups),
            type=GroupType.KEY_VALUE_AREA,
            name=name,
            format=DataFormat.YAML,
            parent_index=parent.index,
            citation_metadata=CitationMetadata(section_title=path or None),
        )
        ctx.groups.append(group)
        return group

    @staticmethod
    def _add_block(
        ctx: _Context,
        start: int,
        end: int,
        parent: BlockGroup,
        parent_path: str,
        level: int,
        *,
        merged: bool,
    ) -> int:
        lines = ctx.lines[start:end]
        text = "\n".join(x.text for x in lines).strip("\n")
        anchor = next((x for x in lines if x.kind == "content"), lines[0])
        own = next((x for x in lines if x.kind == "content" and x.indent <= level), None)
        path = parent_path if merged or own is None else own.path
        scopes = [label for label, opener in anchor.scopes if opener < start]

        # Top-level blocks are self-describing; nested ones need their path.
        shown_path = path if parent_path else ""
        context = [part for part in (shown_path, "scope: " + " > ".join(scopes) if scopes else "") if part]
        if context:
            text = "# " + " | ".join(context) + "\n" + text

        block = Block(
            index=len(ctx.blocks),
            type=BlockType.TEXT,
            format=DataFormat.YAML,
            data=text,
            parent_index=parent.index,
            content_hash=_content_hash(text),
            citation_metadata=CitationMetadata(
                section_title=path or ctx.record_name, line_number=anchor.number
            ),
        )
        ctx.blocks.append(block)
        return block.index


# ---------------------------------------------------------------------------
# Content-line helpers
# ---------------------------------------------------------------------------

def _mask_tags(text: str, spec: DialectSpec | None) -> str:
    """Replace each template tag with filler of the same length so offsets line up."""
    if spec is None:
        return text
    return spec.tag.sub(lambda m: _MASK_CHAR * len(m.group(0)), text)


def _opens_block_scalar(text: str, indent: int, spec: DialectSpec | None) -> bool:
    body = _mask_tags(text.split("\n", 1)[0], spec)[indent:]
    while body.startswith("- "):
        body = body[2:].lstrip(" ")
    split = _split_key(body)
    value = body[split[1]:] if split else body
    return bool(_BLOCK_SCALAR.match(value.strip()))


def _path_of(stack: list[_Frame]) -> str:
    path = ""
    for frame in stack:
        if frame.is_seq:
            path += f"[{frame.count}]"
        else:
            path = f"{path}.{frame.segment}" if path else frame.segment
    return path


def _analyze_content(line: _Line, stack: list[_Frame], spec: DialectSpec | None) -> None:
    first = line.text.split("\n", 1)[0]
    masked = _mask_tags(first, spec)
    offset = len(first) - len(first.lstrip(" \t"))
    indent = line.indent
    body = masked[offset:]

    while body.startswith("- ") or body == "-":
        while stack and stack[-1].indent > indent:
            stack.pop()
        if stack and stack[-1].indent == indent and stack[-1].is_seq:
            stack[-1].count += 1
        else:
            stack.append(_Frame(indent, "", is_seq=True))
        skip = 1
        while skip < len(body) and body[skip] == " ":
            skip += 1
        body, offset, indent = body[skip:], offset + skip, indent + skip

    is_item = indent != line.indent
    split = _split_key(body)
    if split is not None:
        key_end, value_start = split
        while stack and stack[-1].indent >= indent:
            stack.pop()
        # A list item's own path is the item (``containers[0]``); its first
        # key still goes on the stack so nested lines resolve under it.
        line.path = _path_of(stack) if is_item else ""
        stack.append(_Frame(indent, first[offset:offset + key_end].strip()))
        line.value = first[offset + value_start:].strip()
    if not line.path:
        line.path = _path_of(stack)
