import type { BoxProps } from '@mui/material';
import type { Theme } from '@mui/material/styles';
import type { CustomCitation } from 'src/types/chat-bot';
import type { DocumentContent } from 'src/sections/knowledgebase/types/search-response';

import * as XLSX from 'xlsx';
import { Icon } from '@iconify/react';
import fullScreenIcon from '@iconify-icons/mdi/fullscreen';
import fullScreenExitIcon from '@iconify-icons/mdi/fullscreen-exit';
import citationIcon from '@iconify-icons/mdi/format-quote-close';
import fileExcelIcon from '@iconify-icons/mdi/file-excel-outline';
import tableRowIcon from '@iconify-icons/mdi/table-row';
import React, { useRef, useState, useEffect, useCallback } from 'react';

import {
  Box,
  List,
  Table,
  Alert,
  alpha,
  styled,
  Tooltip,
  TableRow,
  ListItem,
  TableBody,
  TableCell,
  TableHead,
  Typography,
  IconButton,
  TableContainer,
  CircularProgress,
  useTheme,
} from '@mui/material';

type ExcelViewerprops = {
  citations: DocumentContent[] | CustomCitation[];
  fileUrl: string | null;
  excelBuffer?: ArrayBuffer | null;
};

interface StyleProps {
  theme?: Theme;
  highlighted?: boolean;
  show?: boolean;
}

interface TableRowType {
  [key: string]: React.ReactNode;
  __rowNum?: number; // Added to track original Excel row number
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

// Styled components for the table with improved dark mode support
const StyledTableCell = styled(TableCell, {
  shouldForwardProp: (prop) => prop !== 'highlighted',
})<StyleProps>(({ theme, highlighted }) => ({
  backgroundColor:
    theme.palette.mode === 'dark'
      ? highlighted
        ? alpha(theme.palette.primary.dark, 0.15)
        : alpha(theme.palette.background.paper, 0.8)
      : highlighted
        ? 'rgba(46, 125, 50, 0.1)'
        : 'inherit',
  minWidth: '100px',
  maxWidth: '400px',
  padding: theme.spacing(2),
  borderBottom: `1px solid ${
    theme.palette.mode === 'dark' ? alpha(theme.palette.divider, 0.1) : theme.palette.divider
  }`,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  verticalAlign: 'top',
  lineHeight: '1.5',
  fontSize: '14px',
  color:
    theme.palette.mode === 'dark'
      ? highlighted
        ? alpha(theme.palette.primary.light, 0.9)
        : theme.palette.text.primary
      : theme.palette.text.primary,
  '& p': {
    margin: 0,
  },
  '& .rich-text-container span': {
    display: 'inline',
  },
  transition: 'background-color 0.2s ease',
  '&:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? highlighted
          ? alpha(theme.palette.primary.dark, 0.25)
          : alpha(theme.palette.action.hover, 0.15)
        : highlighted
          ? 'rgba(46, 125, 50, 0.2)'
          : theme.palette.action.hover,
  },
}));

const HeaderRowNumberCell = styled(TableCell)(({ theme }) => ({
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.default, 0.5)
      : theme.palette.grey[100],
  color: theme.palette.mode === 'dark' ? theme.palette.text.secondary : theme.palette.text.primary,
  minWidth: '60px',
  maxWidth: '80px',
  padding: theme.spacing(2),
  borderBottom:
    theme.palette.mode === 'dark'
      ? `2px solid ${alpha(theme.palette.divider, 0.2)}`
      : `2px solid ${theme.palette.divider}`,
  position: 'sticky',
  left: 0,
  top: 0,
  zIndex: 3,
  textAlign: 'center',
  fontWeight: 600,
}));

const RowNumberCell = styled(TableCell)(({ theme }) => ({
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.default, 0.3)
      : theme.palette.grey[50],
  color: theme.palette.mode === 'dark' ? theme.palette.text.secondary : theme.palette.text.primary,
  minWidth: '60px',
  maxWidth: '80px',
  padding: theme.spacing(2),
  borderBottom:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.divider, 0.1)}`
      : `1px solid ${theme.palette.divider}`,
  position: 'sticky',
  left: 0,
  zIndex: 2,
  textAlign: 'center',
  fontWeight: 500,
}));

const StyledTableRow = styled(TableRow, {
  shouldForwardProp: (prop) => prop !== 'highlighted',
})<StyleProps>(({ theme, highlighted }) => ({
  backgroundColor:
    theme.palette.mode === 'dark'
      ? highlighted
        ? alpha(theme.palette.primary.dark, 0.15)
        : 'transparent'
      : highlighted
        ? 'rgba(46, 125, 50, 0.1)'
        : 'inherit',
  transition: 'background-color 0.2s ease',
  '&:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? highlighted
          ? alpha(theme.palette.primary.dark, 0.25)
          : alpha(theme.palette.action.hover, 0.15)
        : highlighted
          ? 'rgba(46, 125, 50, 0.2)'
          : theme.palette.action.hover,
  },
}));

const HeaderCell = styled(TableCell)(({ theme }) => ({
  fontWeight: 600,
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.paper, 0.8)
      : theme.palette.background.paper,
  color: theme.palette.mode === 'dark' ? theme.palette.primary.lighter : theme.palette.text.primary,
  borderBottom:
    theme.palette.mode === 'dark'
      ? `2px solid ${alpha(theme.palette.divider, 0.2)}`
      : `2px solid ${theme.palette.divider}`,
  padding: theme.spacing(2),
  position: 'sticky',
  top: 0,
  zIndex: 2,
  minWidth: '250px',
  maxWidth: '400px',
  fontSize: '14px',
}));

// Improved sidebar styling with dark mode support
// Instead of using BoxProps as a generic parameter, we should go back to the original approach
// but fix the issue with spreading scrollableContainerStyle

// Original format that works with styled-components
const StyledSidebar = styled(Box)(({ theme }) => ({
  borderLeft:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.divider, 0.1)}`
      : `1px solid ${theme.palette.divider}`,
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.paper, 0.8)
      : theme.palette.background.paper,
  overflow: 'auto',
  width: '300px',
  flexShrink: 0,
  // Instead of spreading scrollableContainerStyle, which may be causing type issues,
  // apply the specific styles from that object directly
  overflowY: 'auto',
  '&::-webkit-scrollbar': {
    width: '8px',
    height: '8px',
    display: 'block',
  },
  '&::-webkit-scrollbar-track': {
    background: 'transparent',
  },
  '&::-webkit-scrollbar-thumb': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.grey[600], 0.48)
        : alpha(theme.palette.grey[600], 0.28),
    borderRadius: '4px',
  },
  '&::-webkit-scrollbar-thumb:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.grey[600], 0.58)
        : alpha(theme.palette.grey[600], 0.38),
  },
}));

const SidebarHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderBottom:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.divider, 0.1)}`
      : `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
}));

const StyledListItem = styled(ListItem)(({ theme }) => ({
  cursor: 'pointer',
  position: 'relative',
  padding: theme.spacing(1.5, 2),
  borderRadius: theme.shape.borderRadius,
  margin: theme.spacing(0.5, 0),
  transition: 'all 0.2s ease',
  '&:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.action.hover, 0.1)
        : theme.palette.action.hover,
  },
}));

const CitationContent = styled(Typography)(({ theme }) => ({
  color:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.text.secondary, 0.9)
      : theme.palette.text.secondary,
  fontSize: '0.875rem',
  lineHeight: 1.5,
  marginTop: theme.spacing(0.5),
  position: 'relative',
  paddingLeft: theme.spacing(1),
  '&::before': {
    content: '""',
    position: 'absolute',
    left: 0,
    top: 0,
    bottom: 0,
    width: '2px',
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.primary.main, 0.4)
        : alpha(theme.palette.primary.main, 0.6),
  },
}));

const MetaLabel = styled(Typography)(({ theme }) => ({
  marginTop: theme.spacing(1),
  display: 'inline-flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
  fontSize: '0.75rem',
  padding: theme.spacing(0.25, 0.75),
  borderRadius: '4px',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.dark, 0.2)
      : alpha(theme.palette.primary.lighter, 0.4),
  color: theme.palette.mode === 'dark' ? theme.palette.primary.light : theme.palette.primary.dark,
  fontWeight: 500,
  marginRight: theme.spacing(1),
}));

const ViewerContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  height: '100%',
  width: '100%',
  overflow: 'hidden',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.paper, 0.7)
      : theme.palette.background.paper,
  '&:fullscreen': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.background.paper, 0.7)
        : theme.palette.background.paper,
    padding: theme.spacing(2),
  },
}));

const MainContainer = styled(Box)(({ theme }) => ({
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: theme.palette.mode === 'dark' ? 'transparent' : theme.palette.background.paper,
  '& .MuiTableContainer-root': {
    overflow: 'visible',
  },
  '& table': {
    width: 'max-content',
    maxWidth: 'none',
    backgroundColor: theme.palette.mode === 'dark' ? 'transparent' : theme.palette.background.paper,
  },
}));

const ControlsContainer = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: theme.spacing(4),
  right: theme.spacing(4),
  zIndex: 1000,
}));

const ExcelViewer = ({ citations, fileUrl, excelBuffer }: ExcelViewerprops) => {
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [tableData, setTableData] = useState<TableRowType[]>([]);
  const [headers, setHeaders] = useState<string[]>([]);
  const [highlightedRow, setHighlightedRow] = useState<number | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const [isInitialized, setIsInitialized] = useState<boolean>(false);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const tableRef = useRef<HTMLDivElement>(null);
  const processingRef = useRef<boolean>(false);
  const mountedRef = useRef<boolean>(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';

  // Store mapping between original Excel row numbers and displayed indices
  const [rowMapping, setRowMapping] = useState<Map<number, number>>(new Map());

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

  // Updated to use rowMapping to find the correct row to scroll to
  const scrollToRow = useCallback(
    (originalRowNum: number): void => {
      if (!tableRef.current || !mountedRef.current) return;

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

          // Remove any previous styling that might have been applied directly
          // (The highlighting will be handled by the React component's props)
        });
      }
    },
    [rowMapping]
  );

  const handleCitationClick = useCallback(
    (citation: DocumentContent): void => {
      if (!mountedRef.current) return;
      const { blockNum, extension } = citation.metadata;
      if (blockNum[0]) {
        // Use the exact blockNum from the citation - don't adjust for hidden rows
        // The row numbers from blockNum match the original Excel row numbers
        const highlightedRowNum = extension === 'csv' ? blockNum[0] + 1 : blockNum[0];

        setSelectedCitation(citation.metadata._id);
        setHighlightedRow(highlightedRowNum);
        scrollToRow(highlightedRowNum);
      }
    },
    [scrollToRow]
  );

  const processRichText = (cell: CellData): React.ReactNode => {
    if (!cell) return '';

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
                // Adjust color for dark mode if needed
                if (isDarkMode && fragment.s.color.rgb.toLowerCase() === '000000') {
                  // If text is black in dark mode, make it light
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

    return cell.w || cell.v || '';
  };

  const processExcelData = useCallback(
    async (workbook: XLSX.WorkBook): Promise<void> => {
      if (processingRef.current || !mountedRef.current) return;
      processingRef.current = true;

      try {
        const firstSheet = workbook.SheetNames[0];
        const worksheet = workbook.Sheets[firstSheet];
        const range = XLSX.utils.decode_range(worksheet['!ref'] || 'A1');

        // Track hidden rows
        const hiddenRows = new Set<number>();
        if (worksheet['!rows']) {
          worksheet['!rows'].forEach((row, index) => {
            if (row?.hidden) hiddenRows.add(index + range.s.r);
          });
        }

        const newHeaders = Array.from({ length: range.e.c - range.s.c + 1 }, (_, colIndex) => {
          const cellAddress = XLSX.utils.encode_cell({ r: range.s.r, c: colIndex + range.s.c });
          const cell = worksheet[cellAddress] as CellData;
          return processRichText(cell)?.toString() || `Column${colIndex + 1}`;
        });

        // Create a new mapping between original Excel row numbers and display indices
        const newRowMapping = new Map<number, number>();
        let displayIndex = 0;

        // Process data rows
        const newData = Array.from({ length: range.e.r - range.s.r }, (_, rowIndex) => {
          // Calculate the actual Excel row number (1-based)
          const excelRowNum = rowIndex + range.s.r + 1;

          // Skip hidden rows but keep tracking original row numbers
          if (hiddenRows.has(excelRowNum - 1)) return null;

          const rowData: Record<string, React.ReactNode> = {
            __rowNum: excelRowNum, // Store the original Excel row number
          };

          newHeaders.forEach((header, colIndex) => {
            const cellAddress = XLSX.utils.encode_cell({
              r: excelRowNum - 1, // Convert back to 0-based for cell lookup
              c: colIndex + range.s.c,
            });
            const cell = worksheet[cellAddress] as CellData;
            rowData[header] = processRichText(cell);
          });

          // Map the original Excel row number to the display index
          newRowMapping.set(excelRowNum, displayIndex);
          displayIndex += 1;

          return rowData;
        }).filter(Boolean) as TableRowType[];

        if (mountedRef.current) {
          setHeaders(newHeaders);
          setTableData(newData);
          setRowMapping(newRowMapping);
          setIsInitialized(true);
        }
      } catch (err) {
        if (mountedRef.current) {
          throw new Error(`Error processing Excel data: ${err.message}`);
        }
      } finally {
        processingRef.current = false;
      }
    },
    // eslint-disable-next-line
    [isDarkMode, theme.palette.text.primary]
  );

  const loadExcelFile = useCallback(async (): Promise<void> => {
    if (!fileUrl && !excelBuffer) return;

    try {
      setLoading(true);
      setError(null);

      let workbook: XLSX.WorkBook;

      if (excelBuffer) {
        // Use buffer directly if available - prevent detached buffer issues
        try {
          // Create a copy of the buffer to prevent detachment issues
          const bufferCopy = excelBuffer.slice(0);

          workbook = XLSX.read(new Uint8Array(bufferCopy), {
            type: 'array',
            cellFormula: true,
            cellHTML: true,
            cellStyles: true,
            cellText: true,
            cellDates: true,
            cellNF: true,
            sheetStubs: true,
            WTF: false,
          });
        } catch (bufferErr) {
          console.error('Error reading from buffer:', bufferErr);
          throw new Error(`Failed to read Excel data from buffer: ${bufferErr.message}`);
        }
      } else if (fileUrl) {
        // Fall back to URL loading
        const response = await fetch(fileUrl);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const arrayBuffer = await response.arrayBuffer();
        if (!mountedRef.current) return;

        workbook = XLSX.read(arrayBuffer, {
          type: 'array',
          cellFormula: true,
          cellHTML: true,
          cellStyles: true,
          cellText: true,
          cellDates: true,
          cellNF: true,
          sheetStubs: true,
          WTF: false,
        });
      } else {
        throw new Error('No data source provided');
      }

      if (!mountedRef.current) return;
      await processExcelData(workbook);
    } catch (err) {
      if (mountedRef.current) {
        setError(`Error loading Excel file: ${err.message}`);
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [fileUrl, excelBuffer, processExcelData]);

  // Handle initial load and cleanup
  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Handle file URL or buffer changes
  useEffect(() => {
    setTableData([]);
    setHeaders([]);
    setHighlightedRow(null);
    setSelectedCitation(null);
    setError(null);
    setIsInitialized(false);
    setRowMapping(new Map());
    loadExcelFile();
  }, [fileUrl, excelBuffer, loadExcelFile]);

  // Handle initial citation highlight
  useEffect(() => {
    if (isInitialized && citations.length > 0 && !highlightedRow && mountedRef.current) {
      const firstCitation = citations[0];

      const { blockNum, extension } = firstCitation.metadata;
      if (blockNum[0]) {
        // Use the exact blockNum from the citation - no adjustment needed
        const highlightedRowNum = extension === 'csv' ? blockNum[0] + 1 : blockNum[0];
        setHighlightedRow(highlightedRowNum);
        setSelectedCitation(firstCitation.metadata._id);
        scrollToRow(highlightedRowNum);
      }
    }
  }, [citations, isInitialized, highlightedRow, scrollToRow]);

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
        backgroundColor: isDarkMode
          ? alpha(theme.palette.background.paper, 0.7)
          : 'background.paper',
        '&:fullscreen': {
          backgroundColor: isDarkMode
            ? alpha(theme.palette.background.paper, 0.7)
            : 'background.paper',
          padding: 2,
        },
      }}
    >
      <ControlsContainer>
        <Tooltip title={isFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'}>
          <IconButton
            onClick={toggleFullscreen}
            size="large"
            sx={{
              backgroundColor: isDarkMode
                ? alpha(theme.palette.background.paper, 0.9)
                : 'background.paper',
              color: 'primary.main',
              boxShadow: isDarkMode ? '0 4px 12px rgba(0, 0, 0, 0.3)' : 2,
              '&:hover': {
                backgroundColor: isDarkMode
                  ? alpha(theme.palette.background.paper, 1)
                  : 'background.paper',
                opacity: 0.9,
              },
              mt: 2,
            }}
          >
            <Icon
              icon={isFullscreen ? fullScreenExitIcon : fullScreenIcon}
              width="24"
              height="24"
            />
          </IconButton>
        </Tooltip>
      </ControlsContainer>

      <ViewerContainer>
        <Box sx={{ maxHeight: '100%', overflow: 'auto' }}>
          <MainContainer>
            <TableContainer ref={tableRef} sx={{ overflow: 'auto' }}>
              <Table stickyHeader>
                <TableHead>
                  <TableRow>
                    <HeaderRowNumberCell>#</HeaderRowNumberCell>
                    {headers.map((header, index) => (
                      <HeaderCell key={index}>
                        <Tooltip
                          title={typeof header === 'string' ? header : ''}
                          arrow
                          placement="top"
                        >
                          <Typography noWrap>{header}</Typography>
                        </Tooltip>
                      </HeaderCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tableData.map((row, displayIndex) => {
                    const isHighlighted = row.__rowNum === highlightedRow;

                    return (
                      <StyledTableRow key={displayIndex} highlighted={isHighlighted}>
                        {/* Display the original Excel row number instead of the index */}
                        <RowNumberCell>{row.__rowNum}</RowNumberCell>
                        {headers.map((header, colIndex) => (
                          <StyledTableCell key={colIndex} highlighted={isHighlighted}>
                            {row[header]}
                          </StyledTableCell>
                        ))}
                      </StyledTableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          </MainContainer>
        </Box>

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
          </SidebarHeader>

          <List sx={{ flex: 1, overflow: 'auto', p: 1.5 }}>
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

                  <CitationContent>{citation.content}</CitationContent>

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

                    {citation.metadata.blockNum[0] && (
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
                        Row {citation.metadata.blockNum[0]}
                      </MetaLabel>
                    )}
                  </Box>
                </Box>
              </StyledListItem>
            ))}
          </List>
        </StyledSidebar>
      </ViewerContainer>
    </Box>
  );
};

export default ExcelViewer;
