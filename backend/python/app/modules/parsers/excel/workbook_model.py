# ruff: noqa: ANN401
"""Loading an Excel workbook in a memory-limited worker, and the compact model it hands back.

openpyxl builds one Python object per cell, merged cell, comment and image
anchor, and what a small file expands to cannot be known from its XML (QA found
inputs of a few KB that load to 4 GB). So nothing here estimates: the load runs
in a worker process whose growth is capped (``load_workbook_model``), and a
workbook that does not fit is refused, whatever it contains.

The parent never builds openpyxl objects. The worker copies what the parser
reads (cell values, data types, number formats, the few style fields written
into blocks, merged ranges and sheet extents) into the model below and sends
only that. The model is columnar: one sorted ``array`` of cell positions, one of
style ids and one list of values per sheet, about 20 bytes plus the value per
cell against ~430 bytes for an openpyxl cell. ``SheetModel`` answers the calls
the parser makes of a worksheet (``cell``, ``iter_rows``, merged cells, the
extent of the data), so table finding and its LLM steps run in the parent on it
unchanged.

Keep this module's imports to the standard library and openpyxl: the worker
imports it by name and must not pay for the service's own modules.
"""
from __future__ import annotations

import contextlib
import gc
import io
import pickle
import signal
import threading
import zipfile
import zlib
from array import array
from bisect import bisect_left
from typing import TYPE_CHECKING, Any
from xml.parsers import expat

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from openpyxl.worksheet.cell_range import CellRange

from app.modules.parsers.excel.limits import CELL_DICT_BYTES, OUTPUT_BUDGET_FACTOR

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

try:  # POSIX only
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]

__all__ = [
    "ModelCell",
    "LoaderFailure",
    "ModelMergedCell",
    "SheetModel",
    "WorkbookModel",
    "WorkbookRefused",
    "get_cell_background_color",
    "get_cell_font_color",
    "load_workbook_model",
    "max_merged_positions",
]

_CHUNK_BYTES = 1024 * 1024
_COLUMN_BITS = 20  # a position is (row << 20) | column; columns stop at 18278
_COLUMN_MASK = (1 << _COLUMN_BITS) - 1
_TALL_MERGE_ROWS = 32  # taller merged ranges are searched, shorter ones are indexed by row
_MAX_NUMBER_FORMAT_CHARS = 255  # Excel's own limit; openpyxl does not enforce it
_NEAR_LIMIT = 0.8  # an error with the worker this close to its cap is the cap


class WorkbookRefused(Exception):
    """The workbook was not loaded. ``reason`` is ``too_large`` or ``unreadable``."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason, detail)
        self.reason = reason
        self.detail = detail

    @property
    def too_large(self) -> bool:
        return self.reason == "too_large"


# ---------------------------------------------------------------------------
# Colours (shared with the parser, which re-exports them)
# ---------------------------------------------------------------------------


def _color_to_str(color: object) -> str | None:
    """Return a colour's ``rgb`` only when it is a real string.

    Theme and indexed colours expose ``rgb`` as an error descriptor rather than
    a string, so anything that is not a ``str`` is treated as "no colour".
    """
    rgb = getattr(color, "rgb", None)
    return rgb if isinstance(rgb, str) else None


def get_cell_background_color(cell: object) -> str | None:
    """Background colour of a cell as a string, or None.

    Handles ``PatternFill`` (foreground colour) and ``GradientFill`` (first
    colour stop). Styling is decorative, so a failure reading it yields None
    instead of failing the cell.
    """
    try:
        # cell.fill is a StyleProxy, so isinstance() against the fill classes
        # is always False; tell the two kinds apart by their attributes.
        fill = getattr(cell, "fill", None)
        if fill is None:
            return None
        start_color = getattr(fill, "start_color", None)
        if start_color is not None:  # PatternFill
            return _color_to_str(start_color)
        stops = getattr(fill, "stop", None)  # GradientFill: colour stops
        if not stops:
            return None
        first = stops[0]
        return _color_to_str(getattr(first, "color", first))
    except Exception:
        return None


def get_cell_font_color(cell: object) -> str | None:
    """Font colour of a cell as a string, or None (theme/indexed give None)."""
    try:
        return _color_to_str(cell.font.color)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


class _Rgb:
    """What ``_color_to_str`` reads of an openpyxl colour."""

    __slots__ = ("rgb",)

    def __init__(self, rgb: str | None) -> None:
        self.rgb = rgb


class _Font:
    __slots__ = ("bold", "color", "italic", "size")

    def __init__(self, bold: Any, italic: Any, size: Any, color: str | None) -> None:
        self.bold = bold
        self.italic = italic
        self.size = size
        self.color = _Rgb(color)


class _Fill:
    __slots__ = ("start_color",)

    def __init__(self, background: str | None) -> None:
        self.start_color = _Rgb(background)


class _Alignment:
    __slots__ = ("horizontal", "vertical")

    def __init__(self, horizontal: Any, vertical: Any) -> None:
        self.horizontal = horizontal
        self.vertical = vertical


# A style is (data_type, number_format, bold, italic, size, font colour,
# background colour, horizontal, vertical), interned once per workbook.
_DEFAULT_STYLE_ID = 0


class ModelCell:
    """A cell of a ``SheetModel``, read-only, with the attributes the parser reads of an openpyxl cell."""

    __slots__ = ("column", "parent", "row", "_style", "value")

    def __init__(self, parent: SheetModel, row: int, column: int, value: Any, style: tuple) -> None:
        self.parent = parent
        self.row = row
        self.column = column
        self.value = value
        self._style = style

    @property
    def data_type(self) -> str:
        return self._style[0]

    @property
    def number_format(self) -> str:
        return self._style[1]

    @property
    def column_letter(self) -> str:
        return get_column_letter(self.column)

    @property
    def coordinate(self) -> str:
        return f"{get_column_letter(self.column)}{self.row}"

    @property
    def font(self) -> _Font:
        s = self._style
        return _Font(s[2], s[3], s[4], s[5])

    @property
    def fill(self) -> _Fill:
        return _Fill(self._style[6])

    @property
    def alignment(self) -> _Alignment:
        return _Alignment(self._style[7], self._style[8])


class ModelMergedCell:
    """A position inside a merged range other than its top-left cell (openpyxl's ``MergedCell``)."""

    __slots__ = ("anchor", "column", "parent", "row")
    value = None
    data_type = "n"

    def __init__(self, parent: SheetModel, row: int, column: int, anchor: tuple[int, int]) -> None:
        self.parent = parent
        self.row = row
        self.column = column
        self.anchor = anchor

    @property
    def column_letter(self) -> str:
        return get_column_letter(self.column)

    @property
    def coordinate(self) -> str:
        return f"{get_column_letter(self.column)}{self.row}"

    def anchor_cell(self) -> ModelCell | ModelMergedCell:
        return self.parent.cell(*self.anchor)


class _ValuePositions:
    """The positions that hold a value, as the set the parser's table scan needs."""

    def __init__(self, sheet: SheetModel) -> None:
        self._sheet = sheet
        values = sheet.values
        max_row = max_col = 0
        count = 0
        rows: set[int] = set()
        last_row = 0
        for key, value in zip(sheet.keys, values, strict=True):
            if value is not None:
                count += 1
                row, col = key >> _COLUMN_BITS, key & _COLUMN_MASK
                if row != last_row:  # keys are sorted, so a row's cells are together
                    rows.add(row)
                    last_row = row
                max_row = max(max_row, row)
                max_col = max(max_col, col)
        self.count = count
        # Rows that hold a value: lets the table scan step over empty rows instead of probing every
        # position of the bounding box (a stray cell far from the data would cost rows x columns).
        self.rows = rows
        self.max_row = max_row
        self.max_col = max_col

    def __bool__(self) -> bool:
        return self.count > 0

    def __len__(self) -> int:
        return self.count

    def __contains__(self, position: object) -> bool:
        row, col = position  # type: ignore[misc]
        return self._sheet.raw_value(row, col) is not None


class SheetModel:
    """One worksheet: cell positions (sorted), their style ids and values, and the merged ranges."""

    def __init__(
        self,
        title: str,
        max_row: int,
        max_column: int,
        keys: array,
        style_ids: array,
        values: list,
        styles: list[tuple],
        merges: list[tuple[int, int, int, int]],
    ) -> None:
        self.title = title
        self.max_row = max_row
        self.max_column = max_column
        self.keys = keys
        self.style_ids = style_ids
        self.values = values
        self.styles = styles
        self.merges = merges
        self._merge_rows: dict[int, list[int]] | None = None
        self._merge_tall: list[int] = []
        self._positions: _ValuePositions | None = None

    # -- lookups ---------------------------------------------------------

    def _find(self, row: int, col: int) -> int:
        key = (row << _COLUMN_BITS) | col
        i = bisect_left(self.keys, key)
        return i if i < len(self.keys) and self.keys[i] == key else -1

    def raw_value(self, row: int, col: int) -> Any:
        i = self._find(row, col)
        return self.values[i] if i >= 0 else None

    def _merge_anchor(self, row: int, col: int) -> tuple[int, int] | None:
        """Top-left cell of the first merged range holding (row, col), when that is another cell."""
        if not self.merges:
            return None
        if self._merge_rows is None:
            index: dict[int, list[int]] = {}
            tall: list[int] = []
            for n, (min_row, _min_col, max_row, _max_col) in enumerate(self.merges):
                if max_row - min_row < _TALL_MERGE_ROWS:
                    for r in range(min_row, max_row + 1):
                        index.setdefault(r, []).append(n)
                else:
                    tall.append(n)
            self._merge_rows, self._merge_tall = index, tall
        candidates = self._merge_rows.get(row, ())
        best = -1
        for n in (*candidates, *self._merge_tall):
            min_row, min_col, max_row, max_col = self.merges[n]
            if min_row <= row <= max_row and min_col <= col <= max_col and (best < 0 or n < best):
                best = n
        if best < 0:
            return None
        min_row, min_col, _, _ = self.merges[best]
        return None if (row, col) == (min_row, min_col) else (min_row, min_col)

    def cell(self, row: int, column: int) -> ModelCell | ModelMergedCell:
        if row < 1 or column < 1:
            raise ValueError("Row or column values must be at least 1")
        if not 0 < row < 1048577:
            raise ValueError(f"Row numbers must be between 1 and 1048576. Row number supplied was {row}")
        anchor = self._merge_anchor(row, column)
        if anchor is not None:
            return ModelMergedCell(self, row, column, anchor)
        i = self._find(row, column)
        if i < 0:
            return ModelCell(self, row, column, None, self.styles[_DEFAULT_STYLE_ID])
        return ModelCell(self, row, column, self.values[i], self.styles[self.style_ids[i]])

    def __getitem__(self, coordinate: str) -> ModelCell | ModelMergedCell:
        letters, row = coordinate_from_string(coordinate)
        return self.cell(row, column_index_from_string(letters))

    def iter_rows(
        self,
        min_row: int | None = None,
        max_row: int | None = None,
        min_col: int | None = None,
        max_col: int | None = None,
        *,
        values_only: bool = False,
    ) -> Iterator[tuple]:
        """Rows of cells between the bounds, every position included (as openpyxl yields)."""
        min_col = min_col or 1
        min_row = min_row or 1
        max_col = max_col or self.max_column
        max_row = max_row or self.max_row
        for row in range(min_row, max_row + 1):
            cells = tuple(self.cell(row, col) for col in range(min_col, max_col + 1))
            yield tuple(c.value for c in cells) if values_only else cells

    def value_positions(self) -> _ValuePositions:
        if self._positions is None:
            self._positions = _ValuePositions(self)
        return self._positions

    def __getstate__(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state.update(_merge_rows=None, _merge_tall=[], _positions=None)
        return state


class WorkbookModel:
    """The sheets of a workbook, in order, with what the load cost the worker."""

    def __init__(self, sheets: list[SheetModel], worker_peak_kb: int = 0, text_bytes: int = 0) -> None:
        self._sheets = {s.title: s for s in sheets}
        self.sheetnames = [s.title for s in sheets]
        self.worker_peak_kb = worker_peak_kb
        # UTF-8 bytes of text the blocks would hold: every use of a shared string counts, and
        # the first row (the headers) once for every other row it is written into
        self.text_bytes = text_bytes

    def __getitem__(self, name: str) -> SheetModel:
        return self._sheets[name]

    def __contains__(self, name: object) -> bool:
        return name in self._sheets

    @property
    def active(self) -> SheetModel:
        return self._sheets[self.sheetnames[0]]

    def close(self) -> None:
        """Nothing to release: the model holds no file or openpyxl object."""


# ---------------------------------------------------------------------------
# The worker side
# ---------------------------------------------------------------------------


def _status_kb(field: str) -> int:
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith(field + ":"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0


def _enforce_growth_limit(limit_bytes: int) -> tuple[Callable[[], None] | None, int | None]:
    """Cap how much this process's data segment may grow, until the returned callable runs.

    RLIMIT_DATA (Linux 4.7 and later) counts the private writable mappings a
    Python process allocates from, so an allocation past the cap fails with
    ``MemoryError`` instead of the kernel's OOM killer picking a victim. The cap
    is the segment's size now plus ``limit_bytes``, so a long-lived pool worker
    that earlier parsed something large still has the full allowance, and the
    old limit is put back afterwards for the jobs that follow. Where the limit
    cannot be set (not Linux) nothing is enforced here and the byte cap in
    ``_precheck`` is the only bound; the container's own memory limit remains.
    """
    base_kb = _status_kb("VmData")
    if resource is None or not base_kb or not hasattr(resource, "RLIMIT_DATA"):
        return None, None
    soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
    target = base_kb * 1024 + limit_bytes
    if hard != resource.RLIM_INFINITY:
        target = min(target, hard)
    try:
        resource.setrlimit(resource.RLIMIT_DATA, (target, hard))
    except (ValueError, OSError):
        return None, None

    def restore() -> None:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_DATA, (soft, hard))

    return restore, target


class _GrowthWatcher:
    """Records how far this process's data segment grows while a load runs.

    The peak is what tells a load that died of the cap from one that failed for its own
    reasons, when openpyxl turned the MemoryError into another error. It is sampled here
    because the kernel's own peak marks (VmHWM) cover the whole life of a long-lived worker
    and cannot be reset by a non-dumpable process.
    """

    _INTERVAL_SECONDS = 0.02

    def __init__(self) -> None:
        self._start_kb = _status_kb("VmData")
        self._start_vm_peak_kb = _status_kb("VmPeak")
        self.peak_kb = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="excel-growth-watch", daemon=True)

    def __enter__(self) -> _GrowthWatcher:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.wait(self._INTERVAL_SECONDS):
            self.sample()

    def sample(self) -> int:
        self.peak_kb = max(self.peak_kb, _status_kb("VmData") - self._start_kb)
        return self.peak_kb

    def peak_growth_kb(self) -> int:
        """The most this load grew the data segment, sampled or not.

        A peak allocated and freed between two samples is still in VmPeak (the kernel's
        high-water mark of address space, readable by a non-dumpable process), but only when
        it went past every earlier peak of this worker: a spike under an earlier, larger job
        is not seen, which can only misname the file's own error, never admit more.
        """
        return max(self.sample(), _status_kb("VmPeak") - self._start_vm_peak_kb)


def max_merged_positions(max_text_bytes: int) -> int:
    """Positions the declared merged ranges may add up to: as many cells as the output budget could
    hold. openpyxl builds one ``MergedCell`` for every position of every merged range, overlaps
    included, so the time grows with the sum of the areas while memory stays flat. This is the fast
    early refusal for that shape; the load's CPU bound covers everything else."""
    return OUTPUT_BUDGET_FACTOR * max_text_bytes // CELL_DICT_BYTES


def _range_area(ref: str) -> int:
    """Positions openpyxl expands for one ``ref``: it reads it with ``CellRange`` (``Sheet!``
    prefixes, quotes, ``$`` and case included). A ref it rejects raises there and expands nothing."""
    try:
        size = CellRange(ref).size
    except (ValueError, TypeError):
        return 0
    return size["rows"] * size["columns"]


# Every encoding a worksheet part may use that the XML parser reads, as the byte patterns of the
# tokens the scan looks for.
_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")


def _patterns(*tokens: str) -> tuple[bytes, ...]:
    return tuple(token.encode(encoding) for token in tokens for encoding in _ENCODINGS)


_DOCTYPE_PATTERNS = _patterns("<!DOCTYPE", "<!ENTITY")
_RANGE_PATTERNS = _patterns("<mergeCell")
_TAIL = max(len(p) for p in (*_DOCTYPE_PATTERNS, *_RANGE_PATTERNS))
# A part that starts with none of these (an ASCII '<', a UTF-8 or UTF-16/32 byte-order mark, a
# BOM-less UTF-16/32 '<') is in some other encoding the parser may still accept (EBCDIC): scan it.
_KNOWN_STARTS = frozenset(b"<\xef\xff\xfe\x00")


def _declared_positions(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> int:
    """Stream one sheet part through expat (which decodes whatever encoding it declares), adding up
    the areas of the ``mergeCell`` elements under ``mergeCells`` (the only place openpyxl reads
    them), and stopping once the total passes *budget*."""
    total = 0
    inside = False

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal total, inside
        local = name.rpartition("}")[2]
        if local == "mergeCells":
            inside = True
        elif local == "mergeCell" and inside:
            total += _range_area(attrs.get("ref", ""))
            if total > budget:
                raise _StopScan

    def end(name: str) -> None:
        nonlocal inside
        if name.rpartition("}")[2] == "mergeCells":
            inside = False

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        with archive.open(info) as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except _StopScan:
        pass
    except (expat.ExpatError, zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError, RuntimeError, ValueError):
        pass  # the loader reports a part it cannot read
    return total


class _StopScan(Exception):
    pass


def _precheck(file_binary: bytes, max_xml_bytes: int, max_merged: int | None = None) -> None:
    """The exact, cheap refusals: a DOCTYPE or entity declaration anywhere (in any encoding), a
    total of decompressed bytes over the cap, and declared merged ranges that add up to more than
    *max_merged* positions. Streams every entry in 1 MB chunks (constant memory), skips an entry it cannot read
    (the loader reads only what it needs), and never trusts the sizes the zip declares."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(file_binary))
        entries = [info for info in archive.infolist() if not info.is_dir()]
    except (zipfile.BadZipFile, OSError, ValueError):
        return  # not a zip: the loader reports it
    total = 0
    positions = 0
    with archive:
        for info in entries:
            tail = b""
            has_ranges = False
            first = True
            try:
                with archive.open(info) as stream:
                    while chunk := stream.read(_CHUNK_BYTES):
                        total += len(chunk)
                        if total > max_xml_bytes:
                            raise WorkbookRefused("too_large", f"{total} decompressed bytes")
                        if first:
                            first = False
                            has_ranges = chunk[0] not in _KNOWN_STARTS and info.filename.endswith(".xml")
                        window = tail + chunk
                        if any(p in window for p in _DOCTYPE_PATTERNS):
                            raise WorkbookRefused("unreadable", "DOCTYPE or entity declaration")
                        has_ranges = has_ranges or any(p in window for p in _RANGE_PATTERNS)
                        tail = window[-_TAIL:]
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError, RuntimeError, ValueError):
                continue
            if has_ranges and max_merged is not None:
                positions += _declared_positions(archive, info, max_merged - positions)
                if positions > max_merged:
                    raise WorkbookRefused("too_large", f"{positions} positions in merged ranges")


def _intern(table: list[tuple], index: dict[tuple, int], style: tuple) -> int:
    found = index.get(style)
    if found is None:
        found = index[style] = len(table)
        table.append(style)
    return found


def _style_of(cell: Cell) -> tuple:
    font = cell.font
    alignment = cell.alignment
    return (
        cell.data_type,
        cell.number_format[:_MAX_NUMBER_FORMAT_CHARS],
        font.bold,
        font.italic,
        font.size,
        get_cell_font_color(cell),
        get_cell_background_color(cell),
        alignment.horizontal,
        alignment.vertical,
    )


def _extract_sheet(ws: Any, table: list[tuple], index: dict[tuple, int]) -> SheetModel:
    raw = getattr(ws, "_cells", None)
    if raw is None:
        raw = {(c.row, c.column): c for row in ws.iter_rows() for c in row}
    keys = array("Q")
    style_ids = array("I")
    values: list = []
    for position in sorted(raw):
        cell = raw[position]
        if isinstance(cell, MergedCell):
            continue  # derived from the merged ranges
        keys.append((position[0] << _COLUMN_BITS) | position[1])
        style_ids.append(_intern(table, index, _style_of(cell)))
        values.append(cell.value)
    merges = sorted(
        (r.min_row, r.min_col, r.max_row, r.max_col) for r in ws.merged_cells.ranges
    )
    return SheetModel(ws.title, ws.max_row, ws.max_column, keys, style_ids, values, table, merges)


def _utf8_len(text: str) -> int:
    return len(text) if text.isascii() else len(text.encode("utf-8"))


def _sheet_text_bytes(sheet: SheetModel) -> int:
    """UTF-8 bytes the blocks built from *sheet* would hold: every string cell once per use,
    plus the first row once for each further row (each row's text repeats its headers)."""
    sizes: dict[int, int] = {}
    total = first_row = rows = 0
    current = 0
    for key, value in zip(sheet.keys, sheet.values, strict=True):
        row = key >> _COLUMN_BITS
        if row != current:
            current = row
            rows += 1
        if isinstance(value, str):
            size = sizes.get(id(value))
            if size is None:
                size = sizes[id(value)] = _utf8_len(value)
            total += size
            if row == 1:
                first_row += size
    return total + first_row * max(0, rows - 1) + _utf8_len(sheet.title) * (2 + rows)


def _extract(file_binary: bytes) -> WorkbookModel:
    workbook = load_workbook(io.BytesIO(file_binary), data_only=True)
    try:
        table: list[tuple] = []
        index: dict[tuple, int] = {}
        # Style 0 is what a position without a stored cell reads as (openpyxl creates it
        # with the workbook's first style); it must be the first entry.
        sheets_for_default = workbook.worksheets
        probe = Cell(sheets_for_default[0], row=1, column=1) if sheets_for_default else None
        _intern(table, index, _style_of(probe) if probe is not None else ("n", "General", None, None, None, None, None, None, None))
        sheets = []
        for ws in list(workbook.worksheets):
            sheets.append(_extract_sheet(ws, table, index))
            ws._cells = {}  # hand the sheet's objects back as we go
        return WorkbookModel(sheets, _status_kb("VmHWM"), sum(_sheet_text_bytes(sh) for sh in sheets))
    finally:
        workbook.close()


class LoaderFailure(Exception):
    """The loader's own error, when the original cannot be pickled (lxml's syntax errors)."""

    def __init__(self, module: str, name: str, message: str) -> None:
        super().__init__(module, name, message)
        self.module = module
        self.name = name
        self.message = message


def _portable_failure(exc: Exception) -> Exception:
    try:
        pickle.loads(pickle.dumps(exc))
    except Exception:
        return LoaderFailure(type(exc).__module__, type(exc).__name__, str(exc))
    return exc


class _CpuLimit(BaseException):
    """Raised by the SIGXCPU handler; a BaseException so no ``except Exception`` in openpyxl or
    lxml callers swallows it."""


# The in-worker CPU limit sits this many seconds above the parent's wall-clock limit, so the
# parent's timeout is the normal stop and RLIMIT_CPU the backstop inside the worker.
_CPU_SLACK_SECONDS = 2


@contextlib.contextmanager
def _cpu_limited(max_seconds: float | None) -> Iterator[None]:
    """Refuse the load as too large once the worker has used *max_seconds* of CPU, however little
    memory it holds: SIGXCPU from ``RLIMIT_CPU`` (counted from this process's CPU time now, since a
    pool worker lives across jobs) raises ``_CpuLimit`` in the main thread. The soft limit is put
    back afterwards; the hard limit is never touched. Where it cannot be set, only the parent's
    timeout bounds the load."""
    if max_seconds is None or resource is None or not hasattr(resource, "RLIMIT_CPU"):
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
    used = resource.getrusage(resource.RUSAGE_SELF)
    target = int(used.ru_utime + used.ru_stime) + int(max_seconds) + _CPU_SLACK_SECONDS
    if hard != resource.RLIM_INFINITY:
        target = min(target, hard)

    def on_cpu_limit(_signum: int, _frame: object) -> None:
        raise _CpuLimit

    try:
        previous = signal.signal(signal.SIGXCPU, on_cpu_limit)
        resource.setrlimit(resource.RLIMIT_CPU, (target, hard))
    except (ValueError, OSError):
        yield
        return
    try:
        yield
    finally:
        # Both must be undone for the worker's next job, even if one of them fails.
        try:
            with contextlib.suppress(ValueError, OSError, MemoryError):
                resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
        finally:
            signal.signal(signal.SIGXCPU, previous)


def load_workbook_model(
    file_binary: bytes,
    limit_bytes: int,
    max_xml_bytes: int,
    max_text_bytes: int | None = None,
    max_seconds: float | None = None,
) -> WorkbookModel:
    """Load a workbook and return its compact model, growing by at most *limit_bytes*.

    Runs in a worker process (``parse_pool``). Raises ``WorkbookRefused``
    (``too_large`` when the load needed more than the allowance, the decompressed
    bytes passed *max_xml_bytes*, or the text the blocks would hold (every use of a
    shared string counted, in UTF-8 bytes) passed *max_text_bytes*, the merged ranges
    declare more positions than ``max_merged_positions(max_text_bytes)``, or the load used more than
    *max_seconds* of CPU (the work openpyxl does on a flat amount of memory: ranges that overlap,
    hyperlinks on merged cells, anything not yet known); ``unreadable`` for a DOCTYPE or
    entity declaration). Anything else is the loader's own error for a file it
    cannot read, as the caller would have seen it before.
    """
    restore, cap = _enforce_growth_limit(limit_bytes)
    over = False
    with _GrowthWatcher() as growth:
        try:
            with _cpu_limited(max_seconds):
                try:
                    _precheck(
                        file_binary,
                        max_xml_bytes,
                        None if max_text_bytes is None else max_merged_positions(max_text_bytes),
                    )
                    model = _extract(file_binary)
                finally:
                    # The memory cap comes off first: undoing the CPU limit under it can itself
                    # fail with MemoryError and leave the next job on this worker CPU-limited.
                    if restore is not None:
                        restore()
                        restore = None
            if max_text_bytes is not None and model.text_bytes > max_text_bytes:
                raise WorkbookRefused("too_large", f"{model.text_bytes} bytes of text in the blocks")
            return model
        except _CpuLimit:
            raise WorkbookRefused("too_large", f"the load used more than {max_seconds} s of CPU") from None
        except MemoryError:
            over = True
        except WorkbookRefused:
            raise
        except Exception as exc:
            # openpyxl catches MemoryError in places and re-raises it as another error (a
            # TypeError from a half-built range); a load that came near the allowance died of it.
            if cap is not None and growth.peak_growth_kb() * 1024 >= limit_bytes * _NEAR_LIMIT:
                over = True
            else:
                raise _portable_failure(exc) from exc
        finally:
            if restore is not None:
                restore()
    if over:
        gc.collect()
        raise WorkbookRefused("too_large", f"the load needed more than {limit_bytes} bytes; worker peak {_status_kb('VmHWM')} kB")
    raise AssertionError("unreachable")  # pragma: no cover
