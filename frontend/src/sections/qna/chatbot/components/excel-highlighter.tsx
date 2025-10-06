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
import React, { useRef, useMemo, useState, useEffect, useCallback } from 'react';

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

type ExcelViewerProps = {
  citations: DocumentContent[] | CustomCitation[];
  fileUrl: string | null;
  excelBuffer?: ArrayBuffer | null;
  highlightCitation?: SearchResult | CustomCitation | null;
  onClosePdf: () => void;
};

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

interface RichTextStyle {
  fontWeight?: 'bold';
  fontStyle?: 'italic';
  textDecoration?: 'underline' | 'line-through';
  color?: string;
}

interface RichTextFragment {
  t: string;
  s?: {
    bold?: boolean;
    italic?: boolean;
    underline?: boolean;
    strike?: boolean;
    color?: {
      rgb: string;
    };
  };
}

interface CellData {
  r?: RichTextFragment[];
  w?: string;
  v?: string | number;
  s?: any;
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

// Styled components for Excel-like appearance (from paste 1)
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
  shouldForwardProp: (prop) => prop !== 'highlighted',
})<StyleProps>(({ theme, highlighted }) => ({
  backgroundColor: highlighted
    ? theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.dark, 0.15)
      : 'rgba(46, 125, 50, 0.1)'
    : 'transparent',
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

// Sidebar components
// Excel viewer inspired styling
const StyledSidebar = styled(Box)(({ theme }) => ({
  width: '300px',
  borderLeft: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #e1e5e9',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: theme.palette.mode === 'dark' ? '#1e2125' : '#ffffff',
  overflow: 'hidden',
  flexShrink: 0,
  // Scrollbar styling to match Excel viewer
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

const FullLoadingOverlay = ({
  isVisible,
  message = 'Loading...',
}: {
  isVisible: boolean;
  message?: string;
}) => {
  const theme = useTheme();

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
};

const ExcelViewer = ({
  citations,
  fileUrl,
  excelBuffer,
  highlightCitation,
  onClosePdf,
}: ExcelViewerProps) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [sheetTransition, setSheetTransition] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [workbookData, setWorkbookData] = useState<WorkbookData | null>(null);
  const [selectedSheet, setSelectedSheet] = useState<string>('');
  const [availableSheets, setAvailableSheets] = useState<string[]>([]);
  const [highlightedRow, setHighlightedRow] = useState<number | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const [isInitialized, setIsInitialized] = useState<boolean>(false);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [loadingMessage, setLoadingMessage] = useState<string>('Loading Excel file...');

  const tableRef = useRef<HTMLDivElement>(null);
  const processingRef = useRef<boolean>(false);
  const mountedRef = useRef<boolean>(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const workbookRef = useRef<XLSX.WorkBook | null>(null);
  const lastSelectedSheet = useRef<string>(selectedSheet);
  const animationFrameRef = useRef<number | null>(null);

  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const scrollableStyles = createScrollableContainerStyle(theme);

  // Store mapping between original Excel row numbers and displayed indices
  const [rowMapping, setRowMapping] = useState<Map<number, number>>(new Map());

  const getColumnWidth = useCallback((columnIndex: number, headerValue: string) => {
    let width = 120;
    if (headerValue && typeof headerValue === 'string') {
      const headerLength = headerValue.length;
      if (headerLength > 20) width = 180;
      else if (headerLength > 15) width = 150;
      else if (headerLength < 8) width = 100;
    }
    return `${Math.min(Math.max(width, 80), 200)}px`;
  }, []);

  // Handle sheet transitions
  useEffect(() => {
    if (selectedSheet !== lastSelectedSheet.current && workbookData && isInitialized) {
      setSheetTransition(true);
      setLoadingMessage('Switching sheets...');

      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }

      animationFrameRef.current = requestAnimationFrame(() => {
        setTimeout(() => {
          setSheetTransition(false);
          lastSelectedSheet.current = selectedSheet;
        }, 150);
      });
    }
  }, [selectedSheet, workbookData, isInitialized]);

  const handleFullscreenChange = useCallback((): void => {
    setIsFullscreen(!!document.fullscreenElement);
  }, []);

  useEffect(() => {
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, [handleFullscreenChange]);

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

  const renderCellValue = useCallback((value: any, returnFullValue = false): string => {
    if (value == null || value === undefined) return '';
    if (value instanceof Date) return value.toLocaleDateString();
    if (typeof value === 'object') return JSON.stringify(value);

    const stringValue = String(value).trim();
    if (returnFullValue) return stringValue;
    return `${stringValue.length > 50 ? `${stringValue.substring(0, 47)}...` : stringValue}`;
  }, []);

  // Type guard function for Date checking
  const isDate = (value: any): value is Date =>
    value instanceof Date ||
    (typeof value === 'object' && Object.prototype.toString.call(value) === '[object Date]');

  const processRichText = useCallback(
    (cell: CellData): React.ReactNode => {
      if (!cell) return '';

      try {
        if (cell.r && Array.isArray(cell.r)) {
          return (
            <div className="rich-text-container">
              {cell.r.map((fragment: RichTextFragment, index: number) => {
                const styles: RichTextStyle = {};
                if (fragment.s) {
                  if (fragment.s.bold) styles.fontWeight = 'bold';
                  if (fragment.s.italic) styles.fontStyle = 'italic';
                  if (fragment.s.underline) styles.textDecoration = 'underline';
                  if (fragment.s.strike) styles.textDecoration = 'line-through';
                  if (fragment.s.color?.rgb) {
                    if (isDarkMode && fragment.s.color.rgb.toLowerCase() === '000000') {
                      styles.color = theme.palette.text.primary;
                    } else {
                      styles.color = `#${fragment.s.color.rgb}`;
                    }
                  }
                }
                return (
                  <span key={index} style={styles}>
                    {fragment.t}
                  </span>
                );
              })}
            </div>
          );
        }

        if (cell.w) return String(cell.w).trim();
        if (cell.v !== undefined && cell.v !== null) {
          // Fix: Type-safe date check using type guard
          if (isDate(cell.v)) {
            return cell.v.toLocaleDateString();
          }
          if (typeof cell.v === 'number') return cell.v.toString();
          return String(cell.v).trim();
        }
      } catch (err) {
        console.warn('Error processing rich text:', err);
      }

      return '';
    },
    [isDarkMode, theme.palette.text.primary]
  );

  const processExcelData = useCallback(
    async (workbook: XLSX.WorkBook): Promise<void> => {
      if (processingRef.current || !mountedRef.current) return;
      processingRef.current = true;

      try {
        setLoadingMessage('Processing Excel data...');
        const processedWorkbook: WorkbookData = {};
        const totalSheets = workbook.SheetNames.length;
        const newRowMapping = new Map<number, number>();

        for (let sheetIndex = 0; sheetIndex < totalSheets; sheetIndex += 1) {
          if (!mountedRef.current) break;

          const sheetName = workbook.SheetNames[sheetIndex];
          setLoadingMessage(`Processing sheet ${sheetIndex + 1} of ${totalSheets}...`);

          const worksheet = workbook.Sheets[sheetName];
          // eslint-disable-next-line
          if (!worksheet['!ref']) continue;

          const range = XLSX.utils.decode_range(worksheet['!ref']);
          const headerRowIndex = range.s.r;

          // Track hidden rows for this sheet
          const hiddenRows = new Set<number>();
          if (worksheet['!rows']) {
            worksheet['!rows'].forEach((row, index) => {
              if (row?.hidden) hiddenRows.add(index + range.s.r);
            });
          }

          const actualCols = range.e.c + 1;
          const maxAllowedCols = 1000;
          const totalCols = Math.min(Math.max(actualCols, 13), maxAllowedCols);

          // Get visible columns (no hidden column detection for now)
          const visibleColumns = Array.from({ length: totalCols }, (_, i) => i);
          const hiddenColumns: number[] = [];

          // Read headers
          const headers = visibleColumns.map((colIndex) => {
            const cellAddress = XLSX.utils.encode_cell({
              r: headerRowIndex,
              c: colIndex,
            });
            const cell = worksheet[cellAddress] as CellData;
            const actualCellValue = processRichText(cell);

            return actualCellValue && actualCellValue.toString().trim() !== ''
              ? actualCellValue.toString().trim()
              : `Column ${String.fromCharCode(65 + colIndex)}`;
          });

          // Process all rows including the header row
          const data: TableRowType[] = [];
          const MAX_ROWS_TO_DISPLAY = 100000; // Adjust as needed
          const maxRows = Math.min(range.e.r + 2, headerRowIndex + MAX_ROWS_TO_DISPLAY);
          let displayIndex = 0;

          for (let rowIndex = headerRowIndex; rowIndex < maxRows; rowIndex += 1) {
            const excelRowNum = rowIndex + 1;

            // Skip hidden rows
            // eslint-disable-next-line
            if (hiddenRows.has(rowIndex)) continue;

            const rowData: TableRowType = {
              __rowNum: excelRowNum,
              __isHeaderRow: rowIndex === headerRowIndex,
              __sheetName: sheetName,
            };

            visibleColumns.forEach((colIndex, visibleIndex) => {
              const cellAddress = XLSX.utils.encode_cell({
                r: rowIndex,
                c: colIndex,
              });
              const cell = worksheet[cellAddress] as CellData;
              const cellValue = processRichText(cell);
              const headerKey = headers[visibleIndex];
              rowData[headerKey] = cellValue;
            });

            // Map the original Excel row number to the display index for this sheet
            newRowMapping.set(excelRowNum, displayIndex);
            displayIndex += 1;

            data.push(rowData);
          }

          processedWorkbook[sheetName] = {
            headers,
            data,
            headerRowIndex: headerRowIndex + 1,
            totalColumns: visibleColumns.length,
            hiddenColumns,
            visibleColumns,
            columnMapping: Object.fromEntries(visibleColumns.map((col, idx) => [idx, col])),
            originalTotalColumns: totalCols,
          };
        }

        if (mountedRef.current) {
          setWorkbookData(processedWorkbook);
          const sheets = Object.keys(processedWorkbook);
          setAvailableSheets(sheets);
          if (sheets.length > 0 && !selectedSheet) {
            setSelectedSheet(sheets[0]);
          }
          setRowMapping(newRowMapping);
          setIsInitialized(true);
          setLoadingMessage('Excel data loaded successfully!');
        }
      } catch (err: any) {
        if (mountedRef.current) {
          console.error('Excel processing error:', err);
          throw new Error(`Error processing Excel data: ${err.message}`);
        }
      } finally {
        processingRef.current = false;
      }
    },
    // eslint-disable-next-line
    [processRichText]
  );

  const loadExcelFile = useCallback(async (): Promise<void> => {
    if (!fileUrl && !excelBuffer) return;

    try {
      setLoading(true);
      setError(null);
      setLoadingMessage('Downloading Excel file...');

      let workbook: XLSX.WorkBook;
      const xlsxOptions = {
        type: 'array' as const,
        cellFormula: false,
        cellHTML: false,
        cellStyles: true,
        cellText: false,
        cellDates: true,
        cellNF: false,
        sheetStubs: false,
        WTF: false,
        raw: false,
        dense: false,
      };

      if (excelBuffer) {
        setLoadingMessage('Processing Excel buffer...');
        const bufferCopy = excelBuffer.slice(0);
        workbook = XLSX.read(new Uint8Array(bufferCopy), xlsxOptions);
      } else if (fileUrl) {
        setLoadingMessage('Fetching Excel file...');
        const response = await fetch(fileUrl);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const arrayBuffer = await response.arrayBuffer();
        if (!mountedRef.current) return;

        setLoadingMessage('Reading Excel file...');
        workbook = XLSX.read(arrayBuffer, xlsxOptions);
      } else {
        throw new Error('No data source provided');
      }

      if (!mountedRef.current) return;

      workbookRef.current = workbook;
      await processExcelData(workbook);
    } catch (err: any) {
      if (mountedRef.current) {
        setError(`Error loading Excel file: ${err.message}`);
        setLoadingMessage('Failed to load Excel file');
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [fileUrl, excelBuffer, processExcelData]);

  const currentSheetData = useMemo(() => {
    if (!workbookData || !selectedSheet || !workbookData[selectedSheet]) {
      return { headers: [], data: [] };
    }
    return workbookData[selectedSheet];
  }, [workbookData, selectedSheet]);

  // Updated to use rowMapping to find the correct row to scroll to
  const scrollToRow = useCallback(
    (originalRowNum: number, targetSheetName?: string): void => {
      if (!tableRef.current || !mountedRef.current) return;

      // If target sheet is different from current, switch sheets first
      if (targetSheetName && targetSheetName !== selectedSheet) {
        setSelectedSheet(targetSheetName);
        // Schedule the scroll after sheet switch
        setTimeout(() => scrollToRow(originalRowNum), 300);
        return;
      }

      // Find the index of the row element in the table that corresponds to this original row number
      const displayIndex = Array.from(rowMapping.entries()).find(
        ([orig, index]) => orig === originalRowNum
      )?.[1];

      if (displayIndex === undefined) {
        console.warn(`Could not find table row for Excel row number ${originalRowNum}`);
        return;
      }

      const tableRows = tableRef.current.getElementsByTagName('tr');
      // Account for header row (+1) and zero-indexing adjustment (+1)
      const rowIndex = displayIndex + 2;

      if (tableRows[rowIndex]) {
        requestAnimationFrame(() => {
          if (!mountedRef.current) return;

          tableRows[rowIndex].scrollIntoView({
            behavior: 'smooth',
            block: 'center',
          });
        });
      }
    },
    [rowMapping, selectedSheet]
  );

  const handleCitationClick = useCallback(
    (citation: DocumentContent): void => {
      if (!mountedRef.current) return;
      const { blockNum, extension, sheetName } = citation.metadata;
      if (blockNum[0]) {
        const highlightedRowNum = extension === 'csv' ? blockNum[0] + 1 : blockNum[0];

        setSelectedCitation(citation.metadata._id);
        setHighlightedRow(highlightedRowNum);
        scrollToRow(highlightedRowNum, sheetName);
      }
    },
    [scrollToRow]
  );

  const handleSheetChange = useCallback(
    (event: React.SyntheticEvent, newValue: string) => {
      if (newValue !== selectedSheet) {
        setSheetTransition(true);
        setSelectedSheet(newValue);
      }
    },
    [selectedSheet]
  );

  const getHeaderRowInfo = useMemo(() => {
    if (!workbookData || !selectedSheet || !workbookData[selectedSheet]) {
      return null;
    }
    return workbookData[selectedSheet].headerRowIndex || null;
  }, [workbookData, selectedSheet]);

  // Handle initial load and cleanup
  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
      workbookRef.current = null;
    };
  }, []);

  // Handle file URL or buffer changes
  useEffect(() => {
    setWorkbookData(null);
    setError(null);
    setIsInitialized(false);
    setAvailableSheets([]);
    setSelectedSheet('');
    setHighlightedRow(null);
    setSelectedCitation(null);
    setRowMapping(new Map());
    loadExcelFile();
  }, [fileUrl, excelBuffer, loadExcelFile]);

  // Handle initial citation highlight
  useEffect(() => {
    if (!isInitialized || !citations.length || highlightedRow || !mountedRef.current) {
      return;
    }
    const sourceCitation = highlightCitation?.metadata || citations[0].metadata;
    const { blockNum, extension, sheetName } = sourceCitation;

    if (!blockNum || !blockNum.length) {
      return;
    }

    const highlightedRowNum = extension === 'csv' ? blockNum[0] + 1 : blockNum[0];

    setHighlightedRow(highlightedRowNum);
    setSelectedCitation(sourceCitation._id);

    // Switch to the correct sheet if needed, then scroll
    if (sheetName && sheetName !== selectedSheet) {
      setSelectedSheet(sheetName);
      setTimeout(() => scrollToRow(highlightedRowNum), 300);
    } else {
      scrollToRow(highlightedRowNum);
    }
  }, [citations, isInitialized, highlightedRow, scrollToRow, highlightCitation, selectedSheet]);

  if (loading) {
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
          Loading Excel data...
        </Typography>
      </Box>
    );
  }

  if (error) {
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
        {error}
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
      <FullLoadingOverlay isVisible={loading} message={loadingMessage} />
      <FullLoadingOverlay isVisible={sheetTransition && !loading} message="Switching sheets..." />

      <ViewerContainer>
        <MainContainer sx={{ width: '100%', overflow: 'auto' }}>
          {workbookData && Object.keys(workbookData).length > 1 && (
            <Box
              sx={{
                borderBottom: isDarkMode ? '1px solid #404448' : '1px solid #e1e5e9',
                mb: 0,
              }}
            >
              <Tabs
                value={selectedSheet || ''}
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
                    color: isDarkMode ? '#b8bcc8' : '#495057',
                    '&.Mui-selected': {
                      color: '#0066cc',
                      fontWeight: 600,
                    },
                  },
                  '& .MuiTabs-indicator': {
                    backgroundColor: '#0066cc',
                    height: '2px',
                  },
                }}
              >
                {Object.keys(workbookData).map((sheetName) => (
                  <Tab
                    key={sheetName}
                    label={sheetName}
                    value={sheetName}
                    sx={{
                      minWidth: 'auto',
                      whiteSpace: 'nowrap',
                    }}
                  />
                ))}
              </Tabs>
            </Box>
          )}

          <Fade in={!sheetTransition && !loading} timeout={300}>
            <Box sx={{ height: '100%', width: '100%' }}>
              <TableContainer
                ref={tableRef}
                sx={{
                  overflow: 'auto',
                  height: '100%',
                  width: '100%',
                  '& .MuiTable-root': {
                    minWidth: 'max-content',
                  },
                  ...scrollableStyles,
                }}
              >
                <Table
                  stickyHeader
                  sx={{
                    width: 'max-content',
                    minWidth: '100%',
                    tableLayout: 'fixed',
                  }}
                >
                  <TableHead>
                    {/* Column Letter Headers */}
                    <TableRow>
                      <HeaderRowNumberCell sx={{ width: '50px' }}>
                        <Typography sx={{ fontSize: '10px', fontWeight: 600 }}>Col</Typography>
                      </HeaderRowNumberCell>
                      {currentSheetData.headers.map((header, index) => {
                        const columnWidth = getColumnWidth(index, header);
                        const actualColumnLetter = String.fromCharCode(65 + index);

                        return (
                          <HeaderRowNumberCell
                            key={index}
                            sx={{ width: columnWidth, minWidth: columnWidth }}
                          >
                            {actualColumnLetter}
                          </HeaderRowNumberCell>
                        );
                      })}
                    </TableRow>

                    {/* Actual Header Row */}
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
                                    zIndex: isFullscreen ? 2000 : 1500,
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
                      const isHighlighted = row.__rowNum === highlightedRow;

                      return (
                        <StyledTableRow
                          key={`${selectedSheet}-${displayIndex}`}
                          highlighted={isHighlighted}
                        >
                          <RowNumberCell
                            sx={{
                              width: '50px',
                              ...(isHeaderRow
                                ? {
                                    backgroundColor: isDarkMode ? '#3a3d42' : '#e9ecef',
                                    fontWeight: 600,
                                  }
                                : {}),
                            }}
                          >
                            {row.__rowNum}
                          </RowNumberCell>
                          {currentSheetData.headers.map((header, colIndex) => {
                            const columnWidth = getColumnWidth(colIndex, header);
                            const cellValue = row[header];
                            const displayValue = renderCellValue(cellValue);
                            const fullValue = renderCellValue(cellValue, true);
                            const isTruncated =
                              displayValue !== fullValue || displayValue.includes('...');

                            return (
                              <ResizableTableCell
                                key={`${selectedSheet}-${displayIndex}-${colIndex}`}
                                sx={{
                                  width: columnWidth,
                                  minWidth: columnWidth,
                                }}
                                isHeaderRow={isHeaderRow}
                                highlighted={isHighlighted}
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
                          })}
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
                title={isFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'}
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
                    icon={isFullscreen ? fullScreenExitIcon : fullScreenIcon}
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
                  key={citation.metadata._id || index}
                  onClick={() => handleCitationClick(citation)}
                  sx={{
                    bgcolor:
                      selectedCitation === citation.metadata._id
                        ? isDarkMode
                          ? alpha(theme.palette.primary.dark, 0.15)
                          : alpha(theme.palette.primary.lighter, 0.3)
                        : 'transparent',
                    boxShadow:
                      selectedCitation === citation.metadata._id
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
                          selectedCitation === citation.metadata._id
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
