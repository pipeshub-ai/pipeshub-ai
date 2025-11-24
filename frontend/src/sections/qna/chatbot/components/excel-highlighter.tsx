import type { Theme } from '@mui/material/styles';
import type { CustomCitation } from 'src/types/chat-bot';
import type {
  SearchResult,
  DocumentContent,
} from 'src/sections/knowledgebase/types/search-response';

import * as XLSX from 'xlsx';
import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import tableRowIcon from '@iconify-icons/mdi/table-row';
import fullScreenIcon from '@iconify-icons/mdi/fullscreen';
import citationIcon from '@iconify-icons/mdi/format-quote-close';
import fileExcelIcon from '@iconify-icons/mdi/file-excel-outline';
import fullScreenExitIcon from '@iconify-icons/mdi/fullscreen-exit';
import React, { useRef, useMemo, useState, useEffect, useCallback, useReducer, memo } from 'react';

import {
  Box,
  Tab,
  List,
  Tabs,
  Fade,
  Table,
  Alert,
  alpha,
  styled,
  Button,
  Tooltip,
  TableRow,
  ListItem,
  useTheme,
  Backdrop,
  TableBody,
  TableCell,
  TableHead,
  Typography,
  IconButton,
  TableContainer,
  CircularProgress,
} from '@mui/material';

import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

type CitationUnion = SearchResult | CustomCitation;

type ExcelViewerProps = {
  citations: DocumentContent[] | CustomCitation[];
  fileUrl: string | null;
  excelBuffer?: ArrayBuffer | null;
  highlightCitation?: CitationUnion | null;
  onClosePdf: () => void;
};

/**
 * Safely extracts the ID from a citation object.
 * Handles both SearchResult and CustomCitation types.
 */
function getCitationId(citation: CitationUnion | null | undefined): string | undefined {
  if (!citation) return undefined;
  
  // CustomCitation has both id and _id directly
  if ('_id' in citation && typeof citation._id === 'string') {
    return citation._id;
  }
  
  // Both types may have id
  if ('id' in citation && typeof citation.id === 'string') {
    return citation.id;
  }
  
  // SearchResult may have citationId
  if ('citationId' in citation && typeof citation.citationId === 'string') {
    return citation.citationId;
  }
  
  // Both types have metadata._id
  if (citation.metadata && '_id' in citation.metadata && typeof citation.metadata._id === 'string') {
    return citation.metadata._id;
  }
  
  return undefined;
}

interface StyleProps {
  theme?: Theme;
  highlighted?: boolean;
  show?: boolean;
  isHeaderRow?: boolean;
}

interface TableRowType {
  [key: string]: React.ReactNode;
  __rowNum?: number;
  __isHeaderRow?: boolean;
  __sheetName?: string;
}

interface WorkbookData {
  [sheetName: string]: {
    headers: string[];
    data: TableRowType[];
    headerRowIndex: number;
    totalColumns: number;
    hiddenColumns: number[];
    visibleColumns: number[];
    columnMapping: { [key: number]: number };
    originalTotalColumns: number;
  };
}

// State management with useReducer for better performance
type ViewerState = {
  loading: boolean;
  sheetTransition: boolean;
  error: string | null;
  workbookData: WorkbookData | null;
  selectedSheet: string;
  availableSheets: string[];
  highlightedRow: number | null;
  selectedCitation: string | null;
  isInitialized: boolean;
  isFullscreen: boolean;
  loadingMessage: string;
  highlightPulse: boolean;
  isHighlighting: boolean;
  rowMapping: Map<number, number>;
};

type ViewerAction =
  | { type: 'SET_LOADING'; loading: boolean; message?: string }
  | { type: 'SET_ERROR'; error: string | null }
  | {
      type: 'SET_WORKBOOK_DATA';
      data: WorkbookData;
      sheets: string[];
      rowMapping: Map<number, number>;
    }
  | { type: 'SET_SELECTED_SHEET'; sheet: string; programmatic?: boolean }
  | { type: 'SET_HIGHLIGHT'; row: number | null; citationId: string | null; pulse: boolean }
  | { type: 'SET_INITIALIZED'; initialized: boolean }
  | { type: 'SET_FULLSCREEN'; fullscreen: boolean }
  | { type: 'SET_SHEET_TRANSITION'; transitioning: boolean }
  | { type: 'SET_HIGHLIGHTING'; highlighting: boolean }
  | { type: 'RESET' };

const initialState: ViewerState = {
  loading: true,
  sheetTransition: false,
  error: null,
  workbookData: null,
  selectedSheet: '',
  availableSheets: [],
  highlightedRow: null,
  selectedCitation: null,
  isInitialized: false,
  isFullscreen: false,
  loadingMessage: 'Loading Excel file...',
  highlightPulse: false,
  isHighlighting: false,
  rowMapping: new Map(),
};

function viewerReducer(state: ViewerState, action: ViewerAction): ViewerState {
  switch (action.type) {
    case 'SET_LOADING':
      return {
        ...state,
        loading: action.loading,
        loadingMessage: action.message || state.loadingMessage,
      };
    case 'SET_ERROR':
      return { ...state, error: action.error, loading: false };
    case 'SET_WORKBOOK_DATA':
      return {
        ...state,
        workbookData: action.data,
        availableSheets: action.sheets,
        rowMapping: action.rowMapping,
        selectedSheet: action.sheets[0] || '',
        isInitialized: true,
        loading: false,
      };
    case 'SET_SELECTED_SHEET':
      return { ...state, selectedSheet: action.sheet };
    case 'SET_HIGHLIGHT':
      return {
        ...state,
        highlightedRow: action.row,
        selectedCitation: action.citationId,
        highlightPulse: action.pulse,
      };
    case 'SET_INITIALIZED':
      return { ...state, isInitialized: action.initialized };
    case 'SET_FULLSCREEN':
      return { ...state, isFullscreen: action.fullscreen };
    case 'SET_SHEET_TRANSITION':
      return { ...state, sheetTransition: action.transitioning };
    case 'SET_HIGHLIGHTING':
      return { ...state, isHighlighting: action.highlighting };
    case 'RESET':
      return { ...initialState };
    default:
      return state;
  }
}

// Styled components (unchanged for compatibility)
const HeaderRowNumberCell = styled(TableCell)(({ theme }) => ({
  backgroundColor: theme.palette.mode === 'dark' ? '#2a2d32' : '#f8f9fa',
  minWidth: '80px',
  maxWidth: '160px',
  width: '100px',
  padding: '6px 4px',
  borderBottom: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  borderRight: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  position: 'sticky',
  left: 0,
  top: 0,
  zIndex: 3,
  textAlign: 'center',
  fontWeight: 500,
  fontSize: '11px',
  color: theme.palette.mode === 'dark' ? '#b8bcc8' : '#495057',
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
}));

const RowNumberCell = styled(TableCell)(({ theme }) => ({
  backgroundColor: theme.palette.mode === 'dark' ? '#2a2d32' : '#f8f9fa',
  minWidth: '50px',
  maxWidth: '90px',
  width: '50px',
  padding: '6px 4px',
  borderBottom: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  borderRight: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  position: 'sticky',
  left: 0,
  zIndex: 2,
  textAlign: 'center',
  fontWeight: 500,
  fontSize: '11px',
  color: theme.palette.mode === 'dark' ? '#b8bcc8' : '#495057',
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
}));

const ResizableTableCell = styled(TableCell, {
  shouldForwardProp: (prop) => prop !== 'isHeaderRow' && prop !== 'highlighted',
})<StyleProps>(({ theme, isHeaderRow, highlighted }) => ({
  backgroundColor: isHeaderRow
    ? theme.palette.mode === 'dark'
      ? '#3a3d42'
      : '#e9ecef'
    : highlighted
      ? theme.palette.mode === 'dark'
        ? alpha(theme.palette.primary.dark, 0.2)
        : 'rgba(46, 125, 50, 0.1)'
      : theme.palette.mode === 'dark'
        ? '#1e2125'
        : '#ffffff',
  padding: '6px 8px',
  borderBottom: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  borderRight: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  verticalAlign: 'top',
  fontSize: '12px',
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  color: theme.palette.mode === 'dark' ? '#e8eaed' : '#212529',
  fontWeight: isHeaderRow ? 600 : 400,
  minWidth: '80px',
  maxWidth: '280px',
  width: '120px',
  transition: 'background-color 0.3s ease',
  '&:hover': {
    backgroundColor: isHeaderRow
      ? theme.palette.mode === 'dark'
        ? '#484b52'
        : '#dee2e6'
      : highlighted
        ? theme.palette.mode === 'dark'
          ? alpha(theme.palette.primary.dark, 0.3)
          : 'rgba(46, 125, 50, 0.2)'
        : theme.palette.mode === 'dark'
          ? '#2a2d32'
          : '#f8f9fa',
  },
}));

const StyledTableRow = styled(TableRow, {
  shouldForwardProp: (prop) => prop !== 'highlighted' && prop !== 'pulse',
})<StyleProps & { pulse?: boolean }>(({ theme, highlighted, pulse }) => ({
  backgroundColor: highlighted
    ? theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.dark, 0.15)
      : 'rgba(46, 125, 50, 0.1)'
    : 'transparent',
  transition: 'background-color 0.3s ease, box-shadow 0.3s ease',
  boxShadow:
    highlighted && pulse
      ? theme.palette.mode === 'dark'
        ? `0 0 8px ${alpha(theme.palette.primary.main, 0.4)}`
        : `0 0 8px ${alpha(theme.palette.primary.main, 0.3)}`
      : 'none',
  animation: highlighted && pulse ? 'pulseHighlight 1.5s ease-in-out 2' : 'none',
  '@keyframes pulseHighlight': {
    '0%, 100%': {
      backgroundColor: highlighted
        ? theme.palette.mode === 'dark'
          ? alpha(theme.palette.primary.dark, 0.15)
          : 'rgba(46, 125, 50, 0.1)'
        : 'transparent',
    },
    '50%': {
      backgroundColor: highlighted
        ? theme.palette.mode === 'dark'
          ? alpha(theme.palette.primary.main, 0.25)
          : 'rgba(46, 125, 50, 0.2)'
        : 'transparent',
    },
  },
  '&:hover': {
    backgroundColor: highlighted
      ? theme.palette.mode === 'dark'
        ? alpha(theme.palette.primary.dark, 0.25)
        : 'rgba(46, 125, 50, 0.2)'
      : theme.palette.mode === 'dark'
        ? '#2a2d32'
        : '#f8f9fa',
  },
}));

const ResizableHeaderCell = styled(TableCell)(({ theme }) => ({
  fontWeight: 600,
  backgroundColor: theme.palette.mode === 'dark' ? '#3a3d42' : '#e9ecef',
  borderBottom: theme.palette.mode === 'dark' ? '2px solid #484b52' : '2px solid #dee2e6',
  borderRight: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  padding: '8px',
  position: 'sticky',
  top: '32px',
  zIndex: 2,
  fontSize: '11px',
  color: theme.palette.mode === 'dark' ? '#b8bcc8' : '#495057',
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  overflow: 'hidden',
  whiteSpace: 'nowrap',
  textOverflow: 'ellipsis',
  minWidth: '80px',
  maxWidth: '280px',
  width: '120px',
}));

const ViewerContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  height: '100%',
  width: '100%',
  overflow: 'hidden',
  position: 'relative',
  backgroundColor: theme.palette.mode === 'dark' ? '#1e2125' : '#ffffff',
  border: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  '&:fullscreen': {
    backgroundColor: theme.palette.mode === 'dark' ? '#1e2125' : '#ffffff',
    padding: 8,
  },
}));

const MainContainer = styled(Box)(({ theme }) => ({
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: theme.palette.mode === 'dark' ? '#1e2125' : '#ffffff',
  '& .MuiTableContainer-root': {
    overflow: 'auto',
    border: 'none',
  },
  '& table': {
    width: 'max-content',
    minWidth: '100%',
    backgroundColor: theme.palette.mode === 'dark' ? '#1e2125' : '#ffffff',
    borderCollapse: 'collapse',
    tableLayout: 'fixed',
    fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  },
}));

const StyledSidebar = styled(Box)(({ theme }) => ({
  width: '300px',
  borderLeft: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: theme.palette.mode === 'dark' ? '#1e2125' : '#ffffff',
  overflow: 'hidden',
  flexShrink: 0,
  '& *::-webkit-scrollbar': {
    width: '8px',
    height: '8px',
    display: 'block',
  },
  '& *::-webkit-scrollbar-track': {
    background: 'transparent',
  },
  '& *::-webkit-scrollbar-thumb': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.grey[600], 0.48)
        : alpha(theme.palette.grey[600], 0.28),
    borderRadius: '4px',
  },
  '& *::-webkit-scrollbar-thumb:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.grey[600], 0.58)
        : alpha(theme.palette.grey[600], 0.38),
  },
}));

const SidebarHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderBottom: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
  backgroundColor: theme.palette.mode === 'dark' ? '#2a2d32' : '#f8f9fa',
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
}));

const StyledListItem = styled(ListItem)(({ theme }) => ({
  cursor: 'pointer',
  position: 'relative',
  padding: theme.spacing(1.5, 2),
  borderRadius: '4px',
  margin: theme.spacing(0.5, 1),
  transition: 'all 0.2s ease',
  backgroundColor: 'transparent',
  border: theme.palette.mode === 'dark' ? '1px solid transparent' : '1px solid transparent',
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  '&:hover': {
    backgroundColor: theme.palette.mode === 'dark' ? '#2a2d32' : '#f8f9fa',
    borderColor: theme.palette.mode === 'dark' ? '#404448' : '#e1e5e9',
  },
}));

const CitationContent = styled(Typography)(({ theme }) => ({
  color: theme.palette.mode === 'dark' ? '#e8eaed' : '#495057',
  fontSize: '12px',
  lineHeight: 1.5,
  marginTop: theme.spacing(0.5),
  position: 'relative',
  paddingLeft: theme.spacing(1),
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  fontWeight: 400,
  '&::before': {
    content: '""',
    position: 'absolute',
    left: 0,
    top: 0,
    bottom: 0,
    width: '2px',
    backgroundColor: theme.palette.mode === 'dark' ? '#0066cc' : '#0066cc',
    borderRadius: '1px',
  },
}));

const MetaLabel = styled(Typography)(({ theme }) => ({
  marginTop: theme.spacing(1),
  display: 'inline-flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
  fontSize: '10px',
  padding: theme.spacing(0.25, 0.75),
  borderRadius: '4px',
  backgroundColor: theme.palette.mode === 'dark' ? '#3a3d42' : '#e9ecef',
  color: theme.palette.mode === 'dark' ? '#b8bcc8' : '#495057',
  fontWeight: 500,
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  border: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #dee2e6',
  marginRight: theme.spacing(1),
}));

const FullLoadingOverlay = memo(
  ({ isVisible, message = 'Loading...' }: { isVisible: boolean; message?: string }) => {
    const theme = useTheme();

    if (!isVisible) return null;

    return (
      <Backdrop
        open={isVisible}
        sx={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor:
            theme.palette.mode === 'dark' ? 'rgba(0, 0, 0, 0.3)' : 'rgba(255, 255, 255, 0.1)',
          backdropFilter: 'blur(0.5px)',
          zIndex: 1500,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 2,
        }}
      >
        <CircularProgress size={40} sx={{ color: '#0066cc' }} />
        <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 500 }}>
          {message}
        </Typography>
      </Backdrop>
    );
  }
);

const HighlightIndicator = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: 12,
  right: 12,
  zIndex: 1000,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.75),
  padding: theme.spacing(0.5, 1),
  borderRadius: '6px',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.paper, 0.95)
      : alpha(theme.palette.background.paper, 0.98),
  backdropFilter: 'blur(8px)',
  boxShadow: theme.shadows[2],
  border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  fontSize: '11px',
  fontWeight: 500,
  color: theme.palette.mode === 'dark' ? theme.palette.text.secondary : theme.palette.text.primary,
  opacity: 0,
  transform: 'translateY(-4px)',
  transition: 'opacity 0.2s ease, transform 0.2s ease',
  pointerEvents: 'none',
  '&.visible': {
    opacity: 1,
    transform: 'translateY(0)',
  },
}));

// Memoized cell component for better rendering performance
const TableCellMemo = memo(
  ({
    value,
    columnWidth,
    isHeaderRow,
    highlighted,
    isFullscreen,
  }: {
    value: any;
    columnWidth: string;
    isHeaderRow: boolean;
    highlighted: boolean;
    isFullscreen: boolean;
  }) => {
    const renderValue = useCallback((val: any, returnFull = false): string => {
      if (val == null || val === undefined) return '';
      if (val instanceof Date) return val.toLocaleDateString();
      if (typeof val === 'object') return JSON.stringify(val);

      const stringValue = String(val).trim();
      if (returnFull) return stringValue;
      return stringValue.length > 50 ? `${stringValue.substring(0, 47)}...` : stringValue;
    }, []);

    const displayValue = renderValue(value);
    const fullValue = renderValue(value, true);
    const isTruncated = displayValue !== fullValue || displayValue.includes('...');

    return (
      <ResizableTableCell
        sx={{ width: columnWidth, minWidth: columnWidth }}
        isHeaderRow={isHeaderRow}
        highlighted={highlighted}
      >
        {isTruncated ? (
          <Tooltip
            title={fullValue}
            arrow
            placement="top"
            enterDelay={300}
            leaveDelay={200}
            PopperProps={{
              sx: {
                zIndex: isFullscreen ? 2000 : 1500,
                '& .MuiTooltip-tooltip': {
                  maxWidth: '400px',
                  fontSize: '12px',
                  backgroundColor: 'rgba(0, 0, 0, 0.9)',
                  color: '#ffffff',
                  wordBreak: 'break-word',
                  whiteSpace: 'pre-wrap',
                  padding: '8px 12px',
                  borderRadius: '4px',
                },
                '& .MuiTooltip-arrow': {
                  color: 'rgba(0, 0, 0, 0.9)',
                },
              },
              container: document.fullscreenElement || document.body,
            }}
          >
            <span>{displayValue}</span>
          </Tooltip>
        ) : (
          displayValue
        )}
      </ResizableTableCell>
    );
  }
);

const ExcelViewer = ({
  citations,
  fileUrl,
  excelBuffer,
  highlightCitation,
  onClosePdf,
}: ExcelViewerProps) => {
  const [state, dispatch] = useReducer(viewerReducer, initialState);

  const tableRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef<boolean>(true);
  const workerRef = useRef<Worker | null>(null);
  const lastSelectedSheet = useRef<string>('');
  const programmaticSheetChangeRef = useRef<boolean>(false);
  const isInitialLoadRef = useRef<boolean>(true);
  const scrollTimeoutRef = useRef<number | null>(null);
  const lastHighlightCitationRef = useRef<CitationUnion | null>(null);
  const isManualSheetChangeRef = useRef<boolean>(false);

  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const scrollableStyles = createScrollableContainerStyle(theme);

  // Memoized column width calculator
  const getColumnWidth = useCallback((columnIndex: number, headerValue: string) => {
    if (!headerValue || typeof headerValue !== 'string') return '120px';
    const len = headerValue.length;
    if (len > 20) return '180px';
    if (len > 15) return '150px';
    if (len < 8) return '100px';
    return '120px';
  }, []);

  // Optimized cell value renderer
  const renderCellValue = useCallback((value: any, returnFullValue = false): string => {
    if (value == null || value === undefined) return '';
    if (value instanceof Date) return value.toLocaleDateString();
    if (typeof value === 'object') return JSON.stringify(value);

    const stringValue = String(value).trim();
    if (returnFullValue) return stringValue;
    return stringValue.length > 50 ? `${stringValue.substring(0, 47)}...` : stringValue;
  }, []);

  // Initialize Web Worker
  useEffect(() => {
    // Only initialize worker if Web Workers are supported
    if (typeof Worker !== 'undefined') {
      try {
        // Note: In production, you'd need to properly bundle the worker
        // For now, we'll fall back to synchronous processing
        workerRef.current = new Worker(new URL('./excel-worker.ts', import.meta.url));
      } catch (err) {
        console.warn('Web Worker not available, using synchronous processing');
      }
    }

    return () => {
      if (workerRef.current) {
        workerRef.current.terminate();
      }
    };
  }, []);

  // Process Excel data (fallback to synchronous if no worker)
  const processExcelData = useCallback(
    async (workbook: XLSX.WorkBook): Promise<void> => {
      try {
        dispatch({ type: 'SET_LOADING', loading: true, message: 'Processing Excel data...' });

        const processedWorkbook: WorkbookData = {};
        const totalSheets = workbook.SheetNames.length;
        const newRowMapping = new Map<number, number>();

        for (let sheetIndex = 0; sheetIndex < totalSheets; sheetIndex += 1) {
          if (!mountedRef.current) break;

          const sheetName = workbook.SheetNames[sheetIndex];
          const worksheet = workbook.Sheets[sheetName];
          if (worksheet['!ref']) {
            const range = XLSX.utils.decode_range(worksheet['!ref']);
            const headerRowIndex = range.s.r;

            const hiddenRows = new Set<number>();
            if (worksheet['!rows']) {
              worksheet['!rows'].forEach((row, index) => {
                if (row?.hidden) hiddenRows.add(index + range.s.r);
              });
            }

            const actualCols = range.e.c + 1;
            const totalCols = Math.min(Math.max(actualCols, 13), 500);
            const visibleColumns = Array.from({ length: totalCols }, (_, i) => i);

            // Optimized header processing with pre-allocation
            const headers = new Array(visibleColumns.length);
            for (let i = 0; i < visibleColumns.length; i += 1) {
              const colIndex = visibleColumns[i];
              const cellAddress = XLSX.utils.encode_cell({ r: headerRowIndex, c: colIndex });
              const cell = worksheet[cellAddress];
              const cellValue = cell?.w || cell?.v;
              headers[i] =
                cellValue?.toString().trim() || `Column ${String.fromCharCode(65 + colIndex)}`;
            }

            // Optimized row processing with batch operations
            const data: TableRowType[] = [];
            const MAX_ROWS = 100000;
            const maxRows = Math.min(Math.max(range.e.r + 2, 30), headerRowIndex + MAX_ROWS);
            let displayIndex = 0;

            for (let rowIndex = headerRowIndex; rowIndex < maxRows; rowIndex += 1) {
              if (!hiddenRows.has(rowIndex)) {
                const excelRowNum = rowIndex + 1;
                const rowData: TableRowType = {
                  __rowNum: excelRowNum,
                  __isHeaderRow: rowIndex === headerRowIndex,
                  __sheetName: sheetName,
                };

                // Batch cell processing
                for (let i = 0; i < visibleColumns.length; i += 1) {
                  const colIndex = visibleColumns[i];
                  const cellAddress = XLSX.utils.encode_cell({ r: rowIndex, c: colIndex });
                  const cell = worksheet[cellAddress];
                  const cellValue = cell?.w || cell?.v;
                  rowData[headers[i]] = cellValue?.toString().trim() || '';
                }

                newRowMapping.set(excelRowNum, displayIndex);
                displayIndex += 1;
                data.push(rowData);
              }
            }

            processedWorkbook[sheetName] = {
              headers,
              data,
              headerRowIndex: headerRowIndex + 1,
              totalColumns: visibleColumns.length,
              hiddenColumns: [],
              visibleColumns,
              columnMapping: Object.fromEntries(visibleColumns.map((col, idx) => [idx, col])),
              originalTotalColumns: totalCols,
            };
          }
        }

        if (mountedRef.current) {
          const sheets = Object.keys(processedWorkbook);
          dispatch({
            type: 'SET_WORKBOOK_DATA',
            data: processedWorkbook,
            sheets,
            rowMapping: newRowMapping,
          });
        }
      } catch (err: any) {
        if (mountedRef.current) {
          dispatch({ type: 'SET_ERROR', error: `Error processing Excel data: ${err.message}` });
        }
      }
    },
    [dispatch]
  );

  // Load Excel file with optimization
  const loadExcelFile = useCallback(async (): Promise<void> => {
    if (!fileUrl && !excelBuffer) return;

    try {
      dispatch({ type: 'SET_LOADING', loading: true, message: 'Downloading Excel file...' });

      let workbook: XLSX.WorkBook;
      const xlsxOptions = {
        type: 'array' as const,
        cellFormula: false,
        cellHTML: false,
        cellStyles: false, // Disable for performance
        cellText: false,
        cellDates: true,
        cellNF: false,
        sheetStubs: false,
        WTF: false,
        raw: false,
        dense: false,
      };

      if (excelBuffer) {
        workbook = XLSX.read(new Uint8Array(excelBuffer.slice(0)), xlsxOptions);
      } else if (fileUrl) {
        const response = await fetch(fileUrl);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const arrayBuffer = await response.arrayBuffer();
        if (!mountedRef.current) return;
        workbook = XLSX.read(arrayBuffer, xlsxOptions);
      } else {
        throw new Error('No data source provided');
      }

      if (!mountedRef.current) return;
      await processExcelData(workbook);

      // Fast completion
      requestAnimationFrame(() => {
        if (mountedRef.current) {
          isInitialLoadRef.current = false;
        }
      });
    } catch (err: any) {
      if (mountedRef.current) {
        dispatch({ type: 'SET_ERROR', error: `Error loading Excel file: ${err.message}` });
      }
    }
  }, [fileUrl, excelBuffer, processExcelData]);

  // Current sheet data with memoization
  const currentSheetData = useMemo(() => {
    if (!state.workbookData || !state.selectedSheet || !state.workbookData[state.selectedSheet]) {
      return { headers: [], data: [] };
    }
    return state.workbookData[state.selectedSheet];
  }, [state.workbookData, state.selectedSheet]);

  // Optimized scroll to row with direct DOM manipulation
  const scrollToRow = useCallback(
    (originalRowNum: number, targetSheetName?: string): void => {
      if (!mountedRef.current) return;

      // Don't scroll if user manually switched sheets (unless it's a programmatic scroll)
      if (isManualSheetChangeRef.current && !programmaticSheetChangeRef.current) {
        return;
      }

      // Clear any pending scroll
      if (scrollTimeoutRef.current) {
        window.clearTimeout(scrollTimeoutRef.current);
      }

      // Switch sheet if needed
      if (targetSheetName && targetSheetName !== state.selectedSheet) {
        programmaticSheetChangeRef.current = true;
        isManualSheetChangeRef.current = false;
        dispatch({ type: 'SET_SELECTED_SHEET', sheet: targetSheetName, programmatic: true });

        // Schedule scroll after sheet change
        scrollTimeoutRef.current = window.setTimeout(() => {
          scrollToRow(originalRowNum);
        }, 50);
        return;
      }

      // Find display index
      const displayIndex = state.rowMapping.get(originalRowNum);
      if (displayIndex === undefined) {
        // Retry if mapping not ready
        scrollTimeoutRef.current = window.setTimeout(() => {
          if (mountedRef.current) scrollToRow(originalRowNum);
        }, 50);
        return;
      }

      const tableRows = tableRef.current?.getElementsByTagName('tr');
      if (!tableRows) {
        scrollTimeoutRef.current = window.setTimeout(() => {
          if (mountedRef.current) scrollToRow(originalRowNum);
        }, 50);
        return;
      }

      const rowIndex = displayIndex + 2;
      const targetRow = tableRows[rowIndex];

      if (targetRow) {
        // Instant position, then smooth center
        targetRow.scrollIntoView({ behavior: 'auto', block: 'nearest' });

        requestAnimationFrame(() => {
          if (mountedRef.current && targetRow) {
            targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // Hide indicator after scroll and reset programmatic flag
            scrollTimeoutRef.current = window.setTimeout(() => {
              if (mountedRef.current) {
                dispatch({ type: 'SET_HIGHLIGHTING', highlighting: false });
                // Reset programmatic flag after scroll completes
                programmaticSheetChangeRef.current = false;
              }
            }, 600);
          }
        });
      }
    },
    [state.rowMapping, state.selectedSheet]
  );

  // Apply highlight immediately
  const applyHighlight = useCallback(
    (rowNum: number, citationId?: string, sheetName?: string) => {
      if (!mountedRef.current) return;

      // Don't apply highlight if user manually switched sheets (unless it's programmatic)
      if (isManualSheetChangeRef.current && !programmaticSheetChangeRef.current) {
        return;
      }

      dispatch({ type: 'SET_HIGHLIGHTING', highlighting: true });
      dispatch({ type: 'SET_HIGHLIGHT', row: rowNum, citationId: citationId || null, pulse: true });

      // Auto-dismiss pulse
      // setTimeout(() => {
      //   if (mountedRef.current) {
      //     dispatch({ type: 'SET_HIGHLIGHT', row: rowNum, citationId: citationId || null, pulse: false });
      //   }
      // }, 3000);

      if (sheetName && sheetName !== state.selectedSheet) {
        programmaticSheetChangeRef.current = true;
        isManualSheetChangeRef.current = false;
        dispatch({ type: 'SET_SELECTED_SHEET', sheet: sheetName });
      }
    },
    [state.selectedSheet]
  );

  // Handle citation click
  const handleCitationClick = useCallback(
    (citation: DocumentContent): void => {
      if (!mountedRef.current) return;

      // Clear any pending scroll operations
      if (scrollTimeoutRef.current) {
        window.clearTimeout(scrollTimeoutRef.current);
        scrollTimeoutRef.current = null;
      }

      // Mark as programmatic change (citation click)
      programmaticSheetChangeRef.current = true;
      isManualSheetChangeRef.current = false;

      const { blockNum, extension, sheetName, _id } = citation.metadata;

      if (blockNum && blockNum[0]) {
        const highlightedRowNum = extension === 'csv' ? blockNum[0] + 1 : blockNum[0];
        const citationId = _id || citation.id;

        applyHighlight(highlightedRowNum, citationId, sheetName);
        scrollToRow(highlightedRowNum, sheetName);
      }
    },
    [applyHighlight, scrollToRow]
  );

  // Handle sheet change
  const handleSheetChange = useCallback(
    (event: React.SyntheticEvent, newValue: string) => {
      if (newValue !== state.selectedSheet) {
        // Clear any pending scroll operations
        if (scrollTimeoutRef.current) {
          window.clearTimeout(scrollTimeoutRef.current);
          scrollTimeoutRef.current = null;
        }

        // Mark as manual sheet change (not programmatic)
        // This flag will remain true until a citation is explicitly clicked
        programmaticSheetChangeRef.current = false;
        isManualSheetChangeRef.current = true;
        lastSelectedSheet.current = state.selectedSheet;

        // Clear highlight when manually switching sheets
        // This prevents auto-scrolling to citations when user wants to browse
        dispatch({ type: 'SET_HIGHLIGHT', row: null, citationId: null, pulse: false });
        dispatch({ type: 'SET_HIGHLIGHTING', highlighting: false });

        dispatch({ type: 'SET_SELECTED_SHEET', sheet: newValue });
      }
    },
    [state.selectedSheet]
  );

  // Handle fullscreen
  const handleFullscreenChange = useCallback((): void => {
    dispatch({ type: 'SET_FULLSCREEN', fullscreen: !!document.fullscreenElement });
  }, []);

  const toggleFullscreen = useCallback(async (): Promise<void> => {
    try {
      if (!document.fullscreenElement && containerRef.current) {
        await containerRef.current.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (err) {
      console.error('Error toggling fullscreen:', err);
    }
  }, []);

  // Header row info
  const getHeaderRowInfo = useMemo(() => {
    if (!state.workbookData || !state.selectedSheet || !state.workbookData[state.selectedSheet]) {
      return null;
    }
    return state.workbookData[state.selectedSheet].headerRowIndex || null;
  }, [state.workbookData, state.selectedSheet]);

  // Setup and cleanup
  useEffect(() => {
    mountedRef.current = true;
    document.addEventListener('fullscreenchange', handleFullscreenChange);

    return () => {
      mountedRef.current = false;
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      if (scrollTimeoutRef.current) {
        window.clearTimeout(scrollTimeoutRef.current);
      }
    };
  }, [handleFullscreenChange]);

  // Load file on mount or change
  useEffect(() => {
    dispatch({ type: 'RESET' });
    isInitialLoadRef.current = true;
    programmaticSheetChangeRef.current = false;
    isManualSheetChangeRef.current = false;
    lastSelectedSheet.current = '';
    lastHighlightCitationRef.current = null;
    loadExcelFile();
  }, [fileUrl, excelBuffer, loadExcelFile]);

  // Handle initial citation highlight
  useEffect(() => {
    if (!state.workbookData || !citations.length || state.highlightedRow || !mountedRef.current) {
      return;
    }

    // Don't auto-highlight if user just manually switched sheets
    if (isManualSheetChangeRef.current) {
      return;
    }

    const sourceCitation = highlightCitation?.metadata || citations[0].metadata;
    const { blockNum, extension, sheetName, _id } = sourceCitation;

    if (!blockNum || !blockNum.length) return;

    const highlightedRowNum = extension === 'csv' ? blockNum[0] + 1 : blockNum[0];
    const citationId = _id || highlightCitation?.citationId || highlightCitation?.id;

    dispatch({ type: 'SET_HIGHLIGHTING', highlighting: true });
    applyHighlight(highlightedRowNum, citationId, sheetName);

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (mountedRef.current) {
          scrollToRow(highlightedRowNum, sheetName);
        }
      });
    });
  }, [
    state.workbookData,
    citations,
    state.highlightedRow,
    highlightCitation,
    applyHighlight,
    scrollToRow,
  ]);

  // Handle highlightCitation changes
  useEffect(() => {
    if (
      !state.isInitialized ||
      !citations.length ||
      !mountedRef.current ||
      !highlightCitation ||
      !state.workbookData
    ) {
      lastHighlightCitationRef.current = highlightCitation || null;
      return;
    }

    // Only react to actual highlightCitation changes, not sheet changes
    // Check if highlightCitation actually changed
    const prevCitation = lastHighlightCitationRef.current;
    const prevId = getCitationId(prevCitation);
    const currentId = getCitationId(highlightCitation);
    const citationChanged =
      !prevCitation || prevCitation !== highlightCitation || prevId !== currentId;

    if (!citationChanged) {
      return;
    }

    // Don't auto-scroll if user just manually switched sheets
    if (isManualSheetChangeRef.current) {
      lastHighlightCitationRef.current = highlightCitation;
      return;
    }

    const sourceCitation = highlightCitation.metadata;
    if (!sourceCitation) {
      lastHighlightCitationRef.current = highlightCitation;
      return;
    }

    const { blockNum, extension, sheetName } = sourceCitation;
    const _id = '_id' in sourceCitation && typeof sourceCitation._id === 'string' 
      ? sourceCitation._id 
      : undefined;
    if (!blockNum || !blockNum.length) {
      lastHighlightCitationRef.current = highlightCitation;
      return;
    }

    // Don't auto-scroll if user manually switched sheets (user is browsing)
    // Only allow auto-scroll if it's a programmatic change (citation was explicitly clicked)
    if (isManualSheetChangeRef.current && !programmaticSheetChangeRef.current) {
      lastHighlightCitationRef.current = highlightCitation;
      return;
    }

    const highlightedRowNum = extension === 'csv' ? blockNum[0] + 1 : blockNum[0];
    const citationId = _id || getCitationId(highlightCitation);

    // Update the ref to track this citation
    lastHighlightCitationRef.current = highlightCitation;

    dispatch({ type: 'SET_HIGHLIGHTING', highlighting: true });
    applyHighlight(highlightedRowNum, citationId, sheetName);
    scrollToRow(highlightedRowNum, sheetName);
  }, [
    highlightCitation,
    state.isInitialized,
    citations.length,
    state.workbookData,
    applyHighlight,
    scrollToRow,
  ]);

  if (state.loading) {
    return (
      <Box
        display="flex"
        flexDirection="column"
        justifyContent="center"
        alignItems="center"
        minHeight={400}
        gap={2}
      >
        <CircularProgress size={32} thickness={3} />
        <Typography variant="body2" color="text.secondary">
          {state.loadingMessage}
        </Typography>
      </Box>
    );
  }

  if (state.error) {
    return (
      <Alert
        severity="error"
        sx={{
          mt: 2,
          backgroundColor: isDarkMode ? alpha(theme.palette.error.dark, 0.15) : undefined,
          color: isDarkMode ? theme.palette.error.light : undefined,
          '& .MuiAlert-icon': {
            color: isDarkMode ? theme.palette.error.light : undefined,
          },
        }}
      >
        {state.error}
      </Alert>
    );
  }

  return (
    <Box
      ref={containerRef}
      sx={{
        position: 'relative',
        height: '100%',
        width: '100%',
        backgroundColor: isDarkMode ? '#1e2125' : '#ffffff',
        overflow: 'hidden',
        '&:fullscreen': {
          backgroundColor: isDarkMode ? '#1e2125' : '#ffffff',
          padding: 2,
        },
      }}
    >
      <FullLoadingOverlay isVisible={state.loading} message={state.loadingMessage} />

      <ViewerContainer>
        <MainContainer sx={{ width: '100%', overflow: 'hidden', position: 'relative' }}>
          {state.isHighlighting && !state.loading && (
            <HighlightIndicator className="visible">
              <CircularProgress size={12} thickness={4} sx={{ color: 'inherit' }} />
              <Typography variant="caption" sx={{ fontSize: '11px', fontWeight: 500 }}>
                Locating citation...
              </Typography>
            </HighlightIndicator>
          )}
          {state.workbookData && Object.keys(state.workbookData).length > 1 && (
            <Box sx={{ borderBottom: isDarkMode ? '1px solid #404448' : '1px solid #e1e5e9' }}>
              <Tabs
                value={state.selectedSheet || ''}
                onChange={handleSheetChange}
                variant="scrollable"
                scrollButtons="auto"
                sx={{
                  minHeight: '36px',
                  '& .MuiTab-root': {
                    minHeight: '36px',
                    fontSize: '12px',
                    textTransform: 'none',
                    padding: '6px 16px',
                    fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                  },
                }}
              >
                {Object.keys(state.workbookData).map((sheetName) => (
                  <Tab key={sheetName} label={sheetName} value={sheetName} />
                ))}
              </Tabs>
            </Box>
          )}

          <Fade in={!state.sheetTransition && !state.loading} timeout={300}>
            <Box sx={{ height: '100%', width: '100%' }}>
              <TableContainer
                ref={tableRef}
                sx={{ overflow: 'auto', height: '100%', width: '100%', ...scrollableStyles }}
              >
                <Table
                  stickyHeader
                  sx={{ width: 'max-content', minWidth: '100%', tableLayout: 'fixed' }}
                >
                  <TableHead>
                    <TableRow>
                      <HeaderRowNumberCell sx={{ width: '50px' }}>
                        <Typography sx={{ fontSize: '10px', fontWeight: 600 }}>Col</Typography>
                      </HeaderRowNumberCell>
                      {currentSheetData.headers.map((header, index) => (
                        <HeaderRowNumberCell
                          key={index}
                          sx={{ width: getColumnWidth(index, header) }}
                        >
                          {String.fromCharCode(65 + index)}
                        </HeaderRowNumberCell>
                      ))}
                    </TableRow>

                    <TableRow>
                      <ResizableHeaderCell sx={{ width: '50px' }}>
                        <Typography sx={{ fontSize: '11px', fontWeight: 600 }}>
                          Header Row {getHeaderRowInfo ? `(${getHeaderRowInfo})` : ''}
                        </Typography>
                      </ResizableHeaderCell>
                      {currentSheetData.headers.map((header, index) => {
                        const columnWidth = getColumnWidth(index, header);
                        const displayHeaderValue = renderCellValue(header);
                        const fullHeaderValue = renderCellValue(header, true);
                        const isHeaderTruncated =
                          displayHeaderValue !== fullHeaderValue ||
                          displayHeaderValue.includes('...');
                        const hasHeaderContent = fullHeaderValue && fullHeaderValue.trim() !== '';
                        return (
                          <ResizableHeaderCell
                            key={index}
                            sx={{
                              width: columnWidth,
                              minWidth: columnWidth,
                              backgroundColor: !hasHeaderContent
                                ? isDarkMode
                                  ? '#2a2d32'
                                  : '#f0f0f0'
                                : isDarkMode
                                  ? '#3a3d42'
                                  : '#e9ecef',
                            }}
                          >
                            {hasHeaderContent ? (
                              <Tooltip
                                title={isHeaderTruncated ? fullHeaderValue : displayHeaderValue}
                                arrow
                                placement="top"
                                enterDelay={200}
                                leaveDelay={200}
                                PopperProps={{
                                  sx: {
                                    zIndex: state.isFullscreen ? 2000 : 1500,
                                    '& .MuiTooltip-tooltip': {
                                      maxWidth: '300px',
                                      fontSize: '11px',
                                      fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                                      backgroundColor: 'rgba(0, 0, 0, 0.9)',
                                      color: '#ffffff',
                                      wordBreak: 'break-word',
                                      whiteSpace: 'pre-wrap',
                                      padding: '6px 10px',
                                      borderRadius: '4px',
                                    },
                                    '& .MuiTooltip-arrow': {
                                      color: 'rgba(0, 0, 0, 0.9)',
                                    },
                                  },
                                  container: document.fullscreenElement || document.body,
                                }}
                              >
                                <Typography
                                  noWrap
                                  sx={{
                                    fontSize: '11px',
                                    fontWeight: 500,
                                    cursor: isHeaderTruncated ? 'help' : 'default',
                                    '&:hover': {
                                      backgroundColor: 'rgba(255, 255, 255, 0.1)',
                                    },
                                  }}
                                >
                                  {displayHeaderValue}
                                </Typography>
                              </Tooltip>
                            ) : (
                              <Typography
                                sx={{
                                  fontSize: '11px',
                                  fontWeight: 500,
                                  color: '#999',
                                  fontStyle: 'italic',
                                }}
                              >
                                (Empty)
                              </Typography>
                            )}
                          </ResizableHeaderCell>
                        );
                      })}
                    </TableRow>
                  </TableHead>

                  <TableBody>
                    {currentSheetData.data.map((row, displayIndex) => {
                      const isHeaderRow = row.__isHeaderRow;
                      const isHighlighted = row.__rowNum === state.highlightedRow;

                      return (
                        <StyledTableRow
                          key={`${state.selectedSheet}-${displayIndex}`}
                          highlighted={isHighlighted}
                          pulse={isHighlighted && state.highlightPulse}
                        >
                          <RowNumberCell sx={{ width: '50px' }}>{row.__rowNum}</RowNumberCell>
                          {currentSheetData.headers.map((header, colIndex) => (
                            <TableCellMemo
                              key={`${state.selectedSheet}-${displayIndex}-${colIndex}`}
                              value={row[header]}
                              columnWidth={getColumnWidth(colIndex, header)}
                              isHeaderRow={!!isHeaderRow}
                              highlighted={isHighlighted}
                              isFullscreen={state.isFullscreen}
                            />
                          ))}
                        </StyledTableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          </Fade>
        </MainContainer>

        {citations.length > 0 && (
          <StyledSidebar>
            <SidebarHeader>
              <Icon
                icon={citationIcon}
                width={20}
                height={20}
                style={{
                  color: isDarkMode ? theme.palette.primary.light : theme.palette.primary.main,
                }}
              />
              <Typography
                variant="h6"
                sx={{
                  fontSize: '1rem',
                  fontWeight: 600,
                  color: isDarkMode
                    ? alpha(theme.palette.text.primary, 0.9)
                    : theme.palette.text.primary,
                }}
              >
                Citations
              </Typography>
              <Tooltip
                placement="top"
                title={state.isFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'}
              >
                <IconButton
                  onClick={toggleFullscreen}
                  size="small"
                  sx={{
                    ml: 1,
                    backgroundColor: isDarkMode
                      ? alpha(theme.palette.background.paper, 0.9)
                      : '#ffffff',
                    boxShadow: isDarkMode
                      ? '0 4px 12px rgba(0, 0, 0, 0.3)'
                      : '0 2px 4px rgba(0,0,0,0.1)',
                    border: isDarkMode ? '1px solid #404448' : '1px solid #e1e5e9',
                    color: theme.palette.primary.main,
                    '&:hover': {
                      backgroundColor: isDarkMode
                        ? alpha(theme.palette.background.paper, 1)
                        : '#f8f9fa',
                    },
                  }}
                >
                  <Icon
                    icon={state.isFullscreen ? fullScreenExitIcon : fullScreenIcon}
                    width="18"
                    height="18"
                  />
                </IconButton>
              </Tooltip>
              <Button
                onClick={onClosePdf}
                size="small"
                sx={{
                  backgroundColor: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.background.paper, 0.8)
                      : alpha(themeVal.palette.grey[50], 0.9),
                  color: (themeVal) => themeVal.palette.error.main,
                  textTransform: 'none',
                  padding: '8px 16px',
                  fontSize: '11px',
                  fontWeight: 600,
                  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
                  // zIndex: 9999,
                  ml: 1,
                  borderRadius: '6px',
                  border: '1px solid',
                  borderColor: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.divider, 0.3)
                      : alpha(themeVal.palette.divider, 0.5),
                  boxShadow: (themeVal) =>
                    themeVal.palette.mode === 'dark' ? themeVal.shadows[4] : themeVal.shadows[2],
                  transition: 'all 0.2s ease',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  '&:hover': {
                    backgroundColor: (themeVal) =>
                      themeVal.palette.mode === 'dark'
                        ? alpha(themeVal.palette.error.dark, 0.1)
                        : alpha(themeVal.palette.error.light, 0.1),
                    borderColor: (themeVal) => alpha(themeVal.palette.error.main, 0.3),
                    color: (themeVal) =>
                      themeVal.palette.mode === 'dark'
                        ? themeVal.palette.error.light
                        : themeVal.palette.error.dark,
                    boxShadow: (themeVal) =>
                      themeVal.palette.mode === 'dark' ? themeVal.shadows[8] : themeVal.shadows[4],
                    transform: 'translateY(-1px)',
                  },
                  '& .MuiSvgIcon-root': {
                    color: 'inherit',
                  },
                }}
              >
                Close
                <Icon icon={closeIcon} width="16" height="16" />
              </Button>
            </SidebarHeader>

            <List sx={{ flex: 1, overflow: 'auto', p: 1.5, ...scrollableStyles }}>
              {citations.map((citation, index) => (
                <StyledListItem
                  key={citation.id || index}
                  onClick={() => handleCitationClick(citation)}
                  sx={{
                    bgcolor:
                      state.selectedCitation === citation.id
                        ? isDarkMode
                          ? alpha(theme.palette.primary.dark, 0.15)
                          : alpha(theme.palette.primary.lighter, 0.3)
                        : 'transparent',
                    boxShadow:
                      state.selectedCitation === citation.id
                        ? isDarkMode
                          ? `0 0 0 1px ${alpha(theme.palette.primary.main, 0.3)}`
                          : `0 0 0 1px ${alpha(theme.palette.primary.main, 0.3)}`
                        : 'none',
                  }}
                >
                  <Box>
                    <Typography
                      variant="subtitle2"
                      sx={{
                        fontWeight: 600,
                        fontSize: '0.875rem',
                        color:
                          state.selectedCitation === citation.id
                            ? theme.palette.primary.main
                            : theme.palette.text.primary,
                      }}
                    >
                      Citation {citation.chunkIndex ? citation.chunkIndex : index + 1}
                    </Typography>

                    <CitationContent>
                      {' '}
                      {citation.metadata?.blockText &&
                      typeof citation.metadata?.blockText === 'string' &&
                      citation.metadata?.blockText.length > 0
                        ? citation.metadata?.blockText
                        : citation.content}{' '}
                    </CitationContent>

                    <Box sx={{ mt: 1.5, display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                      {citation.metadata.sheetName && (
                        <MetaLabel>
                          <Icon
                            icon={fileExcelIcon}
                            width={12}
                            height={12}
                            style={{
                              color: isDarkMode
                                ? theme.palette.primary.light
                                : theme.palette.primary.dark,
                            }}
                          />
                          {citation.metadata.sheetName}
                        </MetaLabel>
                      )}
                      {citation.metadata.blockNum && citation.metadata.blockNum[0] && (
                        <MetaLabel>
                          <Icon
                            icon={tableRowIcon}
                            width={12}
                            height={12}
                            style={{
                              color: isDarkMode
                                ? theme.palette.primary.light
                                : theme.palette.primary.dark,
                            }}
                          />
                          {citation.metadata.extension === 'csv'
                            ? `Row ${citation.metadata.blockNum[0] + 1}`
                            : `Row ${citation.metadata.blockNum[0]}`}
                        </MetaLabel>
                      )}
                    </Box>
                  </Box>
                </StyledListItem>
              ))}
            </List>
          </StyledSidebar>
        )}
      </ViewerContainer>
    </Box>
  );
};

export default ExcelViewer;
