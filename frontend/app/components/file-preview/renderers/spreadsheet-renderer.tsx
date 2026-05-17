'use client';

import React, { useEffect, useMemo, useRef, useCallback, useReducer, memo } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import * as XLSX from 'xlsx';
import { useThemeAppearance } from '@/app/components/theme-provider';
import type { PreviewCitation } from '../types';

// ─── Constants ──────────────────────────────────────────────────────
const MAX_ROWS = 5_000;
const COL_DEFAULT_WIDTH = 150;
const COL_MIN_WIDTH = 30;
const RESIZE_EDGE_PX = 5;
const ROW_NUM_COL_WIDTH = 50;

const XLSX_READ_OPTIONS = {
  type: 'array' as const,
  cellFormula: false,
  cellHTML: false,
  cellStyles: false,
  cellText: false,
  cellDates: true,
  cellNF: false,
  sheetStubs: false,
  WTF: false,
  raw: false,
  dense: false,
};

// ─── Types ──────────────────────────────────────────────────────────
interface TableRowType {
  [key: string]: React.ReactNode;
  __rowNum?: number;
  __isHeaderRow?: boolean;
  __sheetName?: string;
}

interface SheetData {
  headers: string[];
  data: TableRowType[];
  headerRowIndex: number;
  totalColumns: number;
  hiddenColumns: number[];
  visibleColumns: number[];
  /** Total visible (non-hidden) data rows excl. the header row, before the MAX_ROWS cap. */
  totalDataRows: number;
}

interface WorkbookData {
  [sheetName: string]: SheetData;
}

// ─── State Management ───────────────────────────────────────────────
type ViewerState = {
  loading: boolean;
  error: string | null;
  workbookData: WorkbookData | null;
  selectedSheet: string;
  availableSheets: string[];
  highlightedRow: number | null;
  selectedCitation: string | null;
  highlightPulse: boolean;
};

type ViewerAction =
  | { type: 'SET_LOADING'; loading: boolean }
  | { type: 'SET_ERROR'; error: string | null }
  | { type: 'SET_WORKBOOK_DATA'; data: WorkbookData; sheets: string[] }
  | { type: 'SET_SELECTED_SHEET'; sheet: string }
  | { type: 'SET_HIGHLIGHT'; row: number | null; citationId: string | null; pulse: boolean }
  | { type: 'RESET' };

const INITIAL_STATE: ViewerState = {
  loading: true,
  error: null,
  workbookData: null,
  selectedSheet: '',
  availableSheets: [],
  highlightedRow: null,
  selectedCitation: null,
  highlightPulse: false,
};

function viewerReducer(state: ViewerState, action: ViewerAction): ViewerState {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, loading: action.loading };
    case 'SET_ERROR':
      return { ...state, error: action.error, loading: false };
    case 'SET_WORKBOOK_DATA':
      return {
        ...state,
        workbookData: action.data,
        availableSheets: action.sheets,
        selectedSheet: action.sheets[0] || '',
        loading: false,
      };
    case 'SET_SELECTED_SHEET':
      return { ...state, selectedSheet: action.sheet, highlightedRow: null, selectedCitation: null };
    case 'SET_HIGHLIGHT':
      return {
        ...state,
        highlightedRow: action.row,
        selectedCitation: action.citationId,
        highlightPulse: action.pulse,
      };
    case 'RESET':
      return { ...INITIAL_STATE };
    default:
      return state;
  }
}

// ─── Styles injector ────────────────────────────────────────────────
function ensureSpreadsheetStyles(): void {
  if (typeof document === 'undefined') return;
  const id = 'ph-spreadsheet-styles';
  if (document.getElementById(id)) return;

  const style = document.createElement('style');
  style.id = id;
  style.textContent = `
    @keyframes phRowPulse {
      0%   { background-color: rgba(16, 185, 129, 0.25); }
      50%  { background-color: rgba(16, 185, 129, 0.45); }
      100% { background-color: rgba(16, 185, 129, 0.25); }
    }
    @media (prefers-reduced-motion: reduce) {
      .ph-row-pulse td { animation: none !important; }
    }
  `;
  document.head.appendChild(style);
}

// ─── Helpers ────────────────────────────────────────────────────────
function formatCellValue(value: unknown): string {
  if (value == null) return '';
  if (value instanceof Date) return value.toLocaleDateString();
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value).trim();
}

function colLetter(index: number): string {
  let result = '';
  let n = index;
  while (n >= 0) {
    result = String.fromCharCode(65 + (n % 26)) + result;
    n = Math.floor(n / 26) - 1;
  }
  return result;
}

// ─── Process workbook into structured data ──────────────────────────
function processWorkbook(workbook: XLSX.WorkBook): { data: WorkbookData; sheets: string[] } {
  const result: WorkbookData = {};
  const sheets = workbook.SheetNames;

  for (const sheetName of sheets) {
    const worksheet = workbook.Sheets[sheetName];
    if (!worksheet || !worksheet['!ref']) {
      result[sheetName] = { headers: [], data: [], headerRowIndex: 0, totalColumns: 0, hiddenColumns: [], visibleColumns: [], totalDataRows: 0 };
      continue;
    }

    const range = XLSX.utils.decode_range(worksheet['!ref']);

    // Detect hidden rows
    const hiddenRows = new Set<number>();
    if (worksheet['!rows']) {
      worksheet['!rows'].forEach((row: XLSX.RowInfo | undefined, index: number) => {
        if (row?.hidden) hiddenRows.add(index);
      });
    }

    // Detect hidden columns & build visible columns list
    const hiddenColumns: number[] = [];
    const visibleColumns: number[] = [];
    for (let col = range.s.c; col <= range.e.c; col++) {
      if (worksheet['!cols']?.[col]?.hidden) {
        hiddenColumns.push(col);
      } else {
        visibleColumns.push(col);
      }
    }

    // Use first row as header (matching demo behavior)
    const headerRowIndex = range.s.r;

    // Build headers from visible columns
    const headers: string[] = [];
    for (const colIndex of visibleColumns) {
      const addr = XLSX.utils.encode_cell({ r: headerRowIndex, c: colIndex });
      const cell = worksheet[addr];
      headers.push(cell?.w || cell?.v?.toString() || `Column ${colIndex + 1}`);
    }

    // Count total visible data rows (excl. header and hidden rows) before capping.
    let totalDataRows = 0;
    for (let rowIndex = headerRowIndex + 1; rowIndex <= range.e.r; rowIndex++) {
      if (!hiddenRows.has(rowIndex)) totalDataRows++;
    }

    // Build data rows (capped at MAX_ROWS display rows — header + MAX_ROWS data rows)
    const data: TableRowType[] = [];
    let dataRowsAdded = 0;

    for (let rowIndex = headerRowIndex; rowIndex <= range.e.r; rowIndex++) {
      if (hiddenRows.has(rowIndex)) continue;
      // Stop after MAX_ROWS data rows (not counting the header itself)
      if (rowIndex > headerRowIndex && dataRowsAdded >= MAX_ROWS) break;

      const excelRowNum = rowIndex + 1; // 1-based Excel row number
      const rowData: TableRowType = {
        __rowNum: excelRowNum,
        __isHeaderRow: rowIndex === headerRowIndex,
        __sheetName: sheetName,
      };

      // Use stable column-index keys to avoid collisions with duplicate/blank headers.
      for (let i = 0; i < visibleColumns.length; i++) {
        const colIndex = visibleColumns[i];
        const addr = XLSX.utils.encode_cell({ r: rowIndex, c: colIndex });
        const cell = worksheet[addr];
        rowData[`__col_${i}`] = cell?.w || cell?.v?.toString().trim() || '';
      }

      data.push(rowData);
      if (rowIndex > headerRowIndex) dataRowsAdded++;
    }

    result[sheetName] = {
      headers,
      data,
      headerRowIndex,
      totalColumns: range.e.c - range.s.c + 1,
      hiddenColumns,
      visibleColumns,
      totalDataRows,
    };
  }

  return { data: result, sheets };
}

// ─── Memoized table cell ────────────────────────────────────────────
const TableCellMemo = memo(function TableCellMemo({
  value,
  colIndex,
  isHeaderRow,
  highlighted,
  isDark,
}: {
  value: unknown;
  colIndex: number;
  isHeaderRow: boolean;
  highlighted: boolean;
  isDark: boolean;
}) {
  const text = formatCellValue(value);

  let bg = 'transparent';
  if (isHeaderRow) {
    bg = isDark ? '#2a2d32' : 'var(--olive-3)';
  } else if (highlighted) {
    bg = 'rgba(16, 185, 129, 0.15)';
  }

  return (
    <td
      title={text || undefined}
      style={{
        width: `var(--cw-${colIndex})`,
        minWidth: `${COL_MIN_WIDTH}px`,
        padding: '6px 10px',
        borderRight: `1px solid ${isDark ? '#404448' : 'var(--olive-4)'}`,
        borderBottom: `1px solid ${isDark ? '#404448' : 'var(--olive-4)'}`,
        fontSize: '12px',
        lineHeight: '1.4',
        fontWeight: isHeaderRow ? 600 : 400,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        backgroundColor: bg,
        color: isDark ? '#e1e3e7' : 'var(--olive-12)',
        fontFamily: "'Manrope', sans-serif",
      }}
    >
      {text || (isHeaderRow ? <span style={{ color: isDark ? '#666' : 'var(--olive-8)', fontStyle: 'italic' }}>(Empty)</span> : '')}
    </td>
  );
});

// ─── Main component ─────────────────────────────────────────────────
interface SpreadsheetRendererProps {
  fileUrl: string;
  fileName: string;
  /** MIME type, e.g. `text/csv` or `application/vnd.ms-excel`. Used to detect CSV
   *  when the filename doesn't carry a recognisable extension. */
  fileType?: string;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

export function SpreadsheetRenderer({ fileUrl, fileName, fileType, citations, activeCitationId, onHighlightClick }: SpreadsheetRendererProps) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const [state, dispatch] = useReducer(viewerReducer, INITIAL_STATE);
  const tableRef = useRef<HTMLDivElement>(null);
  const tableElRef = useRef<HTMLTableElement>(null);
  const resizingRef = useRef(false);
  const sheetColWidthsRef = useRef<Map<string, number[]>>(new Map());
  // Abort any outstanding scroll-retry loop so a newer citation click doesn't
  // race with an older pending scroll.
  const scrollTokenRef = useRef(0);

  // Backend block numbers for CSV files are 0-based while our __rowNum is
  // 1-based (Excel convention). Mirrors the old UI (excel-highlighter.tsx).
  // We detect CSV via both the filename extension AND the MIME type, because
  // some records (e.g. files named "colors (2)") arrive without a recognisable
  // file extension but carry `text/csv` as their MIME type.
  const rowOffset = useMemo(() => {
    const ext = fileName?.split('.').pop()?.toLowerCase();
    const mime = fileType?.toLowerCase();
    const isCsv = ext === 'csv' || mime === 'text/csv' || mime === 'application/csv';
    return isCsv ? 1 : 0;
  }, [fileName, fileType]);

  // ── Inject animation styles ─────────────────────────────────────
  useEffect(() => { ensureSpreadsheetStyles(); }, []);

  // ── Load & process file ─────────────────────────────────────────
  useEffect(() => {
    if (!fileUrl || fileUrl.trim() === '') {
      dispatch({ type: 'SET_ERROR', error: 'File URL not available' });
      return;
    }

    let cancelled = false;

    const load = async () => {
      try {
        dispatch({ type: 'SET_LOADING', loading: true });
        const response = await fetch(fileUrl);
        if (!response.ok) throw new Error('Failed to fetch spreadsheet');
        const arrayBuffer = await response.arrayBuffer();
        if (cancelled) return;

        const wb = XLSX.read(arrayBuffer, XLSX_READ_OPTIONS);
        if (cancelled) return;

        const { data, sheets } = processWorkbook(wb);
        dispatch({ type: 'SET_WORKBOOK_DATA', data, sheets });
      } catch (err) {
        if (!cancelled) {
          console.error('Error loading spreadsheet:', err);
          dispatch({ type: 'SET_ERROR', error: err instanceof Error ? err.message : 'Failed to load spreadsheet' });
        }
      }
    };

    dispatch({ type: 'RESET' });
    load();
    return () => { cancelled = true; };
  }, [fileUrl]);

  // ── Current sheet data ──────────────────────────────────────────
  const currentSheetData = useMemo(() => {
    if (!state.workbookData || !state.selectedSheet || !state.workbookData[state.selectedSheet]) {
      return { headers: [], data: [] as TableRowType[], totalDataRows: 0, visibleColumns: [] as number[] };
    }
    return state.workbookData[state.selectedSheet];
  }, [state.workbookData, state.selectedSheet]);

  // ── Initialize column widths as CSS variables on the <table> ────
  // Restores previously-saved widths when switching back to a sheet,
  // otherwise falls back to COL_DEFAULT_WIDTH for every column.
  useEffect(() => {
    const tableEl = tableElRef.current;
    if (!tableEl || !currentSheetData.headers.length) return;
    const numCols = currentSheetData.headers.length;

    const savedWidths = sheetColWidthsRef.current.get(state.selectedSheet);
    let totalColWidth = 0;

    for (let i = 0; i < numCols; i++) {
      const w = savedWidths?.[i] ?? COL_DEFAULT_WIDTH;
      tableEl.style.setProperty(`--cw-${i}`, `${w}px`);
      totalColWidth += w;
    }
    tableEl.style.width = `${ROW_NUM_COL_WIDTH + totalColWidth}px`;
  }, [currentSheetData.headers, state.selectedSheet]);

  // ── Column resize: cursor hint on hover near any cell border ───
  const handleResizeMove = useCallback((e: React.PointerEvent) => {
    if (resizingRef.current) return;
    const cell = (e.target as HTMLElement).closest('td, th') as HTMLTableCellElement | null;
    const container = tableRef.current;
    if (!cell || !container) {
      if (container) container.style.cursor = '';
      return;
    }
    if (cell.cellIndex < 1) {
      container.style.cursor = '';
      return;
    }
    const rect = cell.getBoundingClientRect();
    container.style.cursor = e.clientX > rect.right - RESIZE_EDGE_PX ? 'col-resize' : '';
  }, []);

  // ── Column resize: drag start from any cell's right edge ───────
  const handleResizeStart = useCallback((e: React.PointerEvent) => {
    const cell = (e.target as HTMLElement).closest('td, th') as HTMLTableCellElement | null;
    if (!cell) return;
    const cellIdx = cell.cellIndex;
    if (cellIdx < 1) return;
    const rect = cell.getBoundingClientRect();
    if (e.clientX <= rect.right - RESIZE_EDGE_PX) return;

    e.preventDefault();
    e.stopPropagation();
    resizingRef.current = true;

    const colIndex = cellIdx - 1;
    const tableEl = tableElRef.current;
    if (!tableEl) return;

    const currentColWidth = parseInt(
      tableEl.style.getPropertyValue(`--cw-${colIndex}`) || `${COL_DEFAULT_WIDTH}`, 10
    );
    const startX = e.clientX;
    let prevColWidth = currentColWidth;
    let runningTableWidth = parseInt(tableEl.style.width || '0', 10);

    const onMove = (ev: PointerEvent) => {
      const newWidth = Math.max(COL_MIN_WIDTH, currentColWidth + ev.clientX - startX);
      tableEl.style.setProperty(`--cw-${colIndex}`, `${newWidth}px`);
      runningTableWidth += newWidth - prevColWidth;
      tableEl.style.width = `${runningTableWidth}px`;
      prevColWidth = newWidth;
    };

    const onUp = () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (tableRef.current) tableRef.current.style.cursor = '';
      requestAnimationFrame(() => { resizingRef.current = false; });
    };

    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  // ── Row → citation mapping ──────────────────────────────────────
  const rowCitationMap = useMemo(() => {
    const map = new Map<number, string>();
    if (!citations?.length) return map;
    for (const c of citations) {
      if (c.paragraphNumbers?.length) {
        for (const row of c.paragraphNumbers) {
          // For CSV files, block numbers are 0-based — shift by +1 to match __rowNum.
          map.set(row + rowOffset, c.id);
        }
      }
    }
    return map;
  }, [citations, rowOffset]);

  // ── Find which sheet contains a given (1-based) row number ───────
  // We prefer the currently-selected sheet; otherwise scan other sheets
  // for a row with matching __rowNum so cross-sheet citations still work.
  const findSheetForRow = useCallback(
    (rowNum: number): string | null => {
      if (!state.workbookData) return null;
      const current = state.workbookData[state.selectedSheet];
      if (current?.data.some((r) => r.__rowNum === rowNum)) {
        return state.selectedSheet;
      }
      for (const [sheet, data] of Object.entries(state.workbookData)) {
        if (sheet === state.selectedSheet) continue;
        if (data.data.some((r) => r.__rowNum === rowNum)) return sheet;
      }
      return null;
    },
    [state.workbookData, state.selectedSheet],
  );

  // ── Apply highlight from citation ───────────────────────────────
  const applyHighlight = useCallback(
    (rowNum: number, citationId: string) => {
      dispatch({ type: 'SET_HIGHLIGHT', row: rowNum, citationId, pulse: true });

      // Auto-dismiss pulse after animation
      setTimeout(() => {
        dispatch({ type: 'SET_HIGHLIGHT', row: rowNum, citationId, pulse: false });
      }, 700);
    },
    [],
  );

  // ── Scroll to a row (retries until the row is rendered) ─────────
  const scrollToRow = useCallback((rowNum: number) => {
    scrollTokenRef.current += 1;
    const token = scrollTokenRef.current;
    const deadline = Date.now() + 2000; // retry for up to 2s while the table is rendering

    const attempt = () => {
      if (token !== scrollTokenRef.current) return; // superseded
      const container = tableRef.current;
      const row = container?.querySelector(`tr[data-row="${rowNum}"]`);
      if (row) {
        // Instant position first, then smooth center — matches old UI feel
        row.scrollIntoView({ behavior: 'auto', block: 'nearest' });
        requestAnimationFrame(() => {
          if (token !== scrollTokenRef.current) return;
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        return;
      }
      if (Date.now() < deadline) {
        setTimeout(attempt, 50);
      }
    };

    attempt();
  }, []);

  // ── Handle active citation changes ──────────────────────────────
  // Depends on workbookData + selectedSheet so we re-run once the file
  // finishes loading or the user switches to the sheet containing the row.
  useEffect(() => {
    if (!activeCitationId || !citations?.length) return;
    if (!state.workbookData) return; // wait for data

    const citation = citations.find((c) => c.id === activeCitationId);
    if (!citation?.paragraphNumbers?.length) return;

    const rowNum = citation.paragraphNumbers[0] + rowOffset;

    const targetSheet = findSheetForRow(rowNum);
    if (targetSheet && targetSheet !== state.selectedSheet) {
      // Switch sheet first — effect will re-run once selectedSheet updates
      dispatch({ type: 'SET_SELECTED_SHEET', sheet: targetSheet });
      return;
    }

    applyHighlight(rowNum, activeCitationId);
    scrollToRow(rowNum);
  }, [
    activeCitationId,
    citations,
    rowOffset,
    applyHighlight,
    scrollToRow,
    findSheetForRow,
    state.workbookData,
    state.selectedSheet,
  ]);

  // ── Handle row click for citation ───────────────────────────────
  const handleRowClick = useCallback(
    (rowNum: number) => {
      if (resizingRef.current) return;
      const citationId = rowCitationMap.get(rowNum);
      if (citationId) {
        onHighlightClick?.(citationId);
      }
    },
    [rowCitationMap, onHighlightClick],
  );

  // ── Sheet change ────────────────────────────────────────────────
  const handleSheetSelect = useCallback((sheet: string) => {
    const tableEl = tableElRef.current;
    if (tableEl && state.selectedSheet) {
      const widths: number[] = [];
      for (let i = 0; ; i++) {
        const val = tableEl.style.getPropertyValue(`--cw-${i}`);
        if (!val) break;
        widths.push(parseInt(val, 10));
      }
      if (widths.length > 0) {
        sheetColWidthsRef.current.set(state.selectedSheet, widths);
      }
    }
    dispatch({ type: 'SET_SELECTED_SHEET', sheet });
  }, [state.selectedSheet]);

  // ── Loading state ───────────────────────────────────────────────
  if (state.loading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <Text size="2" color="gray">Loading spreadsheet...</Text>
      </Flex>
    );
  }

  // ── Error state ─────────────────────────────────────────────────
  if (state.error || !state.workbookData) {
    return (
      <Flex direction="column" align="center" justify="center" gap="3" style={{ height: '100%', padding: 'var(--space-6)' }}>
        <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--red-9)' }}>error_outline</span>
        <Text size="3" weight="medium" color="red">{state.error || 'Failed to load spreadsheet'}</Text>
      </Flex>
    );
  }

  // ── Empty sheet state ───────────────────────────────────────────
  if (currentSheetData.data.length === 0) {
    return (
      <Box style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <Flex align="center" justify="center" direction="column" gap="3"
          style={{ flex: 1, backgroundColor: isDark ? '#1e2125' : 'white', borderRadius: 'var(--radius-3)', border: `1px solid ${isDark ? '#404448' : 'var(--olive-6)'}`, margin: 'var(--space-4)' }}
        >
          <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--olive-8)' }}>table_chart</span>
          <Text size="2" color="gray">This sheet is empty</Text>
        </Flex>
        {state.availableSheets.length > 1 && (
          <SheetTabs sheets={state.availableSheets} selected={state.selectedSheet} onSelect={handleSheetSelect} isDark={isDark} />
        )}
      </Box>
    );
  }

  const { totalDataRows } = currentSheetData;
  const displayedDataRows = currentSheetData.data.length > 0 ? currentSheetData.data.length - 1 : 0; // -1 for header
  const isTruncated = totalDataRows > MAX_ROWS;

  return (
    <Box
      style={{
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: isDark ? '#1e2125' : 'white',
        borderRadius: 'var(--radius-3)',
        border: `1px solid ${isDark ? '#404448' : 'var(--olive-6)'}`,
      }}
    >
      {/* Sheet tabs (top, like demo) */}
      {state.availableSheets.length > 1 && (
        <SheetTabs sheets={state.availableSheets} selected={state.selectedSheet} onSelect={handleSheetSelect} isDark={isDark} />
      )}

      {/* Table area */}
      <Box
        ref={tableRef}
        onPointerMove={handleResizeMove}
        onPointerDown={handleResizeStart}
        style={{
          flex: 1,
          overflow: 'auto',
        }}
        className="file-preview-scroll-area"
      >
        <table
          ref={tableElRef}
          style={{
            minWidth: '100%',
            tableLayout: 'fixed',
            borderCollapse: 'collapse',
            fontFamily: "'Manrope', sans-serif",
          }}
        >
          {/* Column letter header (A, B, C...) */}
          <thead style={{ position: 'sticky', top: 0, zIndex: 2 }}>
            <tr>
              {/* Row number column header */}
              <th
                style={{
                  width: `${ROW_NUM_COL_WIDTH}px`,
                  minWidth: `${ROW_NUM_COL_WIDTH}px`,
                  padding: '4px 6px',
                  fontSize: '10px',
                  fontWeight: 600,
                  textAlign: 'center',
                  backgroundColor: isDark ? '#2a2d32' : 'var(--olive-3)',
                  borderRight: `1px solid ${isDark ? '#404448' : 'var(--olive-5)'}`,
                  borderBottom: `1px solid ${isDark ? '#404448' : 'var(--olive-5)'}`,
                  color: isDark ? '#999' : 'var(--olive-9)',
                  position: 'sticky',
                  left: 0,
                  zIndex: 3,
                }}
              >
                #
              </th>
              {currentSheetData.headers.map((_header, index) => (
                <th
                  key={index}
                  style={{
                    width: `var(--cw-${index})`,
                    minWidth: `${COL_MIN_WIDTH}px`,
                    padding: '4px 6px',
                    fontSize: '10px',
                    fontWeight: 600,
                    textAlign: 'center',
                    backgroundColor: isDark ? '#2a2d32' : 'var(--olive-3)',
                    borderRight: `1px solid ${isDark ? '#404448' : 'var(--olive-5)'}`,
                    borderBottom: `1px solid ${isDark ? '#404448' : 'var(--olive-5)'}`,
                    color: isDark ? '#999' : 'var(--olive-9)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {colLetter(index)}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {currentSheetData.data.map((row, displayIndex) => {
              const rowNum = row.__rowNum ?? displayIndex + 1;
              const isHeaderRow = !!row.__isHeaderRow;
              const isHighlighted = rowNum === state.highlightedRow;
              const hasCitation = rowCitationMap.has(rowNum);
              const doPulse = isHighlighted && state.highlightPulse;

              return (
                <tr
                  key={`${state.selectedSheet}-${displayIndex}`}
                  data-row={rowNum}
                  data-citation-id={hasCitation ? rowCitationMap.get(rowNum) : undefined}
                  onClick={hasCitation ? () => handleRowClick(rowNum) : undefined}
                  style={{
                    cursor: hasCitation ? 'pointer' : undefined,
                    backgroundColor: isHighlighted
                      ? 'rgba(16, 185, 129, 0.25)'
                      : hasCitation
                        ? 'rgba(16, 185, 129, 0.08)'
                        : undefined,
                    animation: doPulse ? 'phRowPulse 0.7s ease-out 1' : undefined,
                    transition: 'background-color 0.2s ease',
                  }}
                >
                  {/* Row number cell (sticky left) */}
                  <td
                    style={{
                      width: `${ROW_NUM_COL_WIDTH}px`,
                      minWidth: `${ROW_NUM_COL_WIDTH}px`,
                      padding: '4px 6px',
                      fontSize: '10px',
                      fontWeight: 500,
                      textAlign: 'center',
                      backgroundColor: isDark ? '#2a2d32' : 'var(--olive-2)',
                      borderRight: `1px solid ${isDark ? '#404448' : 'var(--olive-5)'}`,
                      borderBottom: `1px solid ${isDark ? '#404448' : 'var(--olive-4)'}`,
                      color: isDark ? '#999' : 'var(--olive-9)',
                      position: 'sticky',
                      left: 0,
                      zIndex: 1,
                    }}
                  >
                    {rowNum}
                  </td>

                  {currentSheetData.headers.map((_header, colIndex) => (
                    <TableCellMemo
                      key={`${state.selectedSheet}-${displayIndex}-${colIndex}`}
                      value={row[`__col_${colIndex}`]}
                      colIndex={colIndex}
                      isHeaderRow={isHeaderRow}
                      highlighted={isHighlighted}
                      isDark={isDark}
                    />
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Box>

      {/* Bottom bar: truncation warning */}
      {isTruncated && (
        <Flex
          align="center"
          gap="2"
          style={{
            padding: 'var(--space-2) var(--space-4)',
            flexShrink: 0,
            borderTop: `1px solid ${isDark ? '#404448' : 'var(--olive-4)'}`,
          }}
        >
          <span className="material-icons-outlined" style={{ fontSize: '16px', color: 'var(--olive-9)' }}>info</span>
          <Text size="1" color="gray">
            Showing first {displayedDataRows.toLocaleString()} of {totalDataRows.toLocaleString()} rows. Download to view all data.
          </Text>
        </Flex>
      )}
    </Box>
  );
}

// ─── Sheet Tabs ─────────────────────────────────────────────────────
function SheetTabs({
  sheets,
  selected,
  onSelect,
  isDark,
}: {
  sheets: string[];
  selected: string;
  onSelect: (sheet: string) => void;
  isDark: boolean;
}) {
  return (
    <Flex
      gap="0"
      style={{
        overflow: 'auto',
        borderBottom: `1px solid ${isDark ? '#404448' : 'var(--olive-4)'}`,
        backgroundColor: isDark ? '#1e2125' : 'white',
        flexShrink: 0,
      }}
      className="file-preview-scroll-area"
    >
      {sheets.map((name) => {
        const isActive = name === selected;
        return (
          <Box
            key={name}
            onClick={() => onSelect(name)}
            style={{
              padding: '6px 16px',
              fontSize: '12px',
              fontWeight: isActive ? 500 : 400,
              cursor: 'pointer',
              userSelect: 'none',
              whiteSpace: 'nowrap',
              borderBottom: isActive
                ? `2px solid var(--accent-9)`
                : '2px solid transparent',
              color: isActive
                ? (isDark ? '#e1e3e7' : 'var(--olive-12)')
                : (isDark ? '#999' : 'var(--olive-10)'),
              backgroundColor: 'transparent',
              transition: 'color 0.15s ease, border-color 0.15s ease',
              fontFamily: "'Manrope', sans-serif",
            }}
            onMouseEnter={(e) => {
              if (!isActive) {
                (e.currentTarget as HTMLElement).style.backgroundColor = isDark ? '#2a2d32' : 'var(--olive-2)';
              }
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent';
            }}
          >
            {name}
          </Box>
        );
      })}
    </Flex>
  );
}
