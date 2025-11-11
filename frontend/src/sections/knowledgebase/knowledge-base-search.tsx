import { useRef, useState, useEffect, useCallback } from 'react';

import { Box, alpha, Alert, Button, styled, Divider, useTheme, Snackbar } from '@mui/material';

import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { KnowledgeBaseAPI } from './services/api';
import KnowledgeSearch from './knowledge-search';
import { ORIGIN } from './constants/knowledge-search';
import KnowledgeSearchSideBar from './knowledge-search-sidebar';
import DocxViewer from '../qna/chatbot/components/docx-highlighter';
import HtmlViewer from '../qna/chatbot/components/html-highlighter';
import TextViewer from '../qna/chatbot/components/text-highlighter';
import ExcelViewer from '../qna/chatbot/components/excel-highlighter';
import PdfHighlighterComp from '../qna/chatbot/components/pdf-highlighter';
import MarkdownViewer from '../qna/chatbot/components/markdown-highlighter';
import { createScrollableContainerStyle } from '../qna/chatbot/utils/styles/scrollbar';
import { getConnectorPublicUrl } from '../accountdetails/account-settings/services/utils/services-configuration-service';
import { useConnectors } from '../accountdetails/connectors/context';

import type { Filters } from './types/knowledge-base';
import type { PipesHub, SearchResult, AggregatedDocument } from './types/search-response';
import ImageHighlighter from '../qna/chatbot/components/image-highlighter';

// Constants for sidebar widths - must match with the sidebar component
const SIDEBAR_EXPANDED_WIDTH = 320;
const SIDEBAR_COLLAPSED_WIDTH = 64;
const INITIAL_TOP_K = 10;
const MAX_TOP_K = 100;

// Styled Close Button for the citation viewer
export const StyledCloseButton = styled(Button)(({ theme }) => ({
  position: 'absolute',
  top: 15,
  right: 15,
  backgroundColor: theme.palette.primary.main,
  color: theme.palette.primary.contrastText,
  textTransform: 'none',
  padding: '6px 12px',
  minWidth: 'auto',
  fontSize: '0.875rem',
  fontWeight: 600,
  zIndex: 10,
  borderRadius: theme.shape.borderRadius,
  boxShadow: theme.shadows[2],
  '&:hover': {
    backgroundColor: theme.palette.primary.dark,
    boxShadow: theme.shadows[4],
  },
}));

function getDocumentType(extension: string) {
  if (extension === 'pdf') return 'pdf';
  if (['xlsx', 'xls', 'csv'].includes(extension)) return 'excel';
  if (extension === 'docx') return 'docx';
  if (extension === 'html') return 'html';
  if (extension === 'txt') return 'text';
  if (extension === 'md') return 'md';
  if (extension === 'mdx') return 'mdx';
  if (['jpg', 'jpeg', 'png', 'webp', 'svg'].includes(extension)) return 'image';
  return 'other';
}

export default function KnowledgeBaseSearch() {
  const theme = useTheme();
  const [filters, setFilters] = useState<Filters>({
    department: [],
    moduleId: [],
    appSpecificRecordType: [],
    app: [],
    kb: [] 
  });
  const scrollableStyles = createScrollableContainerStyle(theme);

  // Get connector data from the hook at parent level for optimal performance
  const { activeConnectors, inactiveConnectors } = useConnectors();
  const allConnectors = [...activeConnectors, ...inactiveConnectors];
  
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [topK, setTopK] = useState<number>(INITIAL_TOP_K);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [canLoadMore, setCanLoadMore] = useState<boolean>(true);
  const [aggregatedCitations, setAggregatedCitations] = useState<AggregatedDocument[]>([]);
  const [openSidebar, setOpenSidebar] = useState<boolean>(true);
  const [isPdf, setIsPdf] = useState<boolean>(false);
  const [isExcel, setIsExcel] = useState<boolean>(false);
  const [isDocx, setIsDocx] = useState<boolean>(false);
  const [isHtml, setIsHtml] = useState<boolean>(false);
  const [isMarkdown, setIsMarkdown] = useState<boolean>(false);
  const [isTextFile, setIsTextFile] = useState<boolean>(false);
  const [isImage, setIsImage] = useState<boolean>(false);
  const [fileUrl, setFileUrl] = useState<string>('');
  const [recordCitations, setRecordCitations] = useState<AggregatedDocument | null>(null);
  const [hasSearched, setHasSearched] = useState<boolean>(false);
  const [recordsMap, setRecordsMap] = useState<Record<string, PipesHub.Record>>({});
  const [fileBuffer, setFileBuffer] = useState<ArrayBuffer | null>(null);
  const [highlightedCitation, setHighlightedCitation] = useState<SearchResult | null>();
  
  // Prevent rapid filter changes
  const isFilterChanging = useRef(false);

  // Snackbar state
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'error' as 'error' | 'warning' | 'info' | 'success',
  });

  // Add a state to track if citation viewer is open
  const isCitationViewerOpen = isPdf || isExcel || isDocx || isHtml || isTextFile || isMarkdown || isImage;

  const handleFilterChange = (newFilters: Filters) => {
    // If a filter operation is already in progress, return
    if (isFilterChanging.current) return;

    isFilterChanging.current = true;

    // Use requestAnimationFrame to batch updates
    requestAnimationFrame(() => {
      setFilters((prevFilters) => ({
        ...prevFilters,
        ...newFilters,
      }));

      // Reset the flag after a short delay
      setTimeout(() => {
        isFilterChanging.current = false;
      }, 50);
    });
  };

  const aggregateCitationsByRecordId = useCallback(
    (documents: SearchResult[]): AggregatedDocument[] => {
      const aggregationMap = documents.reduce(
        (acc, doc) => {
          const recordId = doc.metadata?.recordId || 'unknown';

          if (!acc[recordId]) {
            acc[recordId] = {
              recordId,
              documents: [],
            };
          }

          acc[recordId].documents.push(doc);
          return acc;
        },
        {} as Record<string, AggregatedDocument>
      );

      return Object.values(aggregationMap);
    },
    []
  );

  const aggregateRecordsByRecordId = useCallback(
    (records: PipesHub.Record[]): Record<string, PipesHub.Record> =>
      records.reduce(
        (acc, record) => {
          const recordKey = record._key || 'unknown';
          acc[recordKey] = record;
          return acc;
        },
        {} as Record<string, PipesHub.Record>
      ),
    []
  );

  const handleSearch = useCallback(async () => {
    // Only proceed if search query is not empty
    if (!searchQuery.trim()) {
      setSearchResults([]);
      setAggregatedCitations([]);
      setCanLoadMore(false);
      return;
    }

    setLoading(true);
    setHasSearched(true);

    try {
      const data = await KnowledgeBaseAPI.searchKnowledgeBases(searchQuery, topK, filters);

      const results = data.searchResults || [];
      const recordResult = data.records || [];
      
      setSearchResults(results);

      // Check if we can load more: if results length is less than topK, no more results available
      const shouldLoadMore = results.length >= topK && topK < MAX_TOP_K;
      setCanLoadMore(shouldLoadMore);

      const recordsLookupMap = aggregateRecordsByRecordId(recordResult);
      setRecordsMap(recordsLookupMap);
      
      const citations = aggregateCitationsByRecordId(results);
      setAggregatedCitations(citations);
    } catch (error) {
      console.error('Search failed:', error);
      setSearchResults([]);
      setAggregatedCitations([]);
      setCanLoadMore(false);
    } finally {
      setLoading(false);
    }
  }, [searchQuery, topK, filters, aggregateCitationsByRecordId, aggregateRecordsByRecordId]);

  useEffect(() => {
    // Only trigger search if there's a non-empty query
    if (searchQuery.trim()) {
      handleSearch();
    }
  }, [topK, filters, handleSearch, searchQuery]);

  const handleSearchQueryChange = (query: string): void => {
    setSearchQuery(query);
    
    // Reset topK and canLoadMore when query changes
    if (query.trim() !== searchQuery.trim()) {
      setTopK(INITIAL_TOP_K);
      setCanLoadMore(true);
    }
    
    if (!query.trim()) {
      setHasSearched(false);
      setCanLoadMore(false);
    }
  };

  const handleTopKChange = (callback: (prevTopK: number) => number): void => {
    setTopK((prevTopK) => {
      const newTopK = callback(prevTopK);
      // Don't exceed MAX_TOP_K
      return newTopK <= MAX_TOP_K ? newTopK : prevTopK;
    });
  };

  const handleLargePPTFile = (record: any) => {
    if (record.sizeInBytes / 1048576 > 5) {
      throw new Error('Large file size, redirecting to web page');
    }
  };

  const viewCitations = async (
    recordId: string,
    extension: string,
    recordCitation?: SearchResult
  ): Promise<void> => {
    // Reset all document type states
    setIsPdf(false);
    setIsExcel(false);
    setIsDocx(false);
    setIsHtml(false);
    setIsTextFile(false);
    setIsMarkdown(false);
    setIsImage(false);
    setFileBuffer(null);
    setRecordCitations(null);
    setFileUrl('');
    setHighlightedCitation(recordCitation);
    
    const documentContainer = document.querySelector('#document-container');
    if (documentContainer) {
      documentContainer.innerHTML = '';
    }

    // Close sidebar when showing citation viewer
    setOpenSidebar(false);

    try {
      const record = recordsMap[recordId];

      if (!record) {
        console.error('Record not found for ID:', recordId);
        setSnackbar({
          open: true,
          message: 'Record not found. Please try again.',
          severity: 'error',
        });
        return;
      }
      
      // Find the correct citation from the aggregated data
      const citation = aggregatedCitations.find((item) => item.recordId === recordId);
      if (citation) {
        setRecordCitations(citation);
      }

      let fileDataLoaded = false;

      if (record.origin === ORIGIN.UPLOAD) {
        const fetchRecordId = record.externalRecordId || '';
        if (!fetchRecordId) {
          console.error('No external record ID available');
          setSnackbar({
            open: true,
            message: 'External record ID not available.',
            severity: 'error',
          });
          return;
        }

        try {
          const response = await axios.get(`/api/v1/document/${fetchRecordId}/download`, {
            responseType: 'blob',
          });

          const reader = new FileReader();
          const textPromise = new Promise<string>((resolve) => {
            reader.onload = () => {
              resolve(reader.result?.toString() || '');
            };
          });

          reader.readAsText(response.data);
          const text = await textPromise;

          let filename;
          const contentDisposition = response.headers['content-disposition'];

          if (contentDisposition) {
            // First try to parse filename*=UTF-8'' format (RFC 5987) for Unicode support
            const filenameStarMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
            if (filenameStarMatch && filenameStarMatch[1]) {
              // Decode the percent-encoded UTF-8 filename
              try {
                filename = decodeURIComponent(filenameStarMatch[1]);
              } catch (e) {
                console.error('Failed to decode UTF-8 filename', e);
              }
            }
            
            // Fallback to basic filename="..." format if filename* not found
            if (!filename) {
              const filenameMatch = contentDisposition.match(/filename="?([^";\n]*)"?/i);
              if (filenameMatch && filenameMatch[1]) {
                filename = filenameMatch[1];
              }
            }
          }
          if (!filename && record.recordName) {
            filename = record.recordName;
          }

          try {
            const jsonData = JSON.parse(text);
            if (jsonData && jsonData.signedUrl) {
              setFileUrl(jsonData.signedUrl);
              fileDataLoaded = true;
            }
          } catch (e) {
            const bufferReader = new FileReader();
            const arrayBufferPromise = new Promise<ArrayBuffer>((resolve) => {
              bufferReader.onload = () => {
                resolve(bufferReader.result as ArrayBuffer);
              };
              bufferReader.readAsArrayBuffer(response.data);
            });

            const buffer = await arrayBufferPromise;
            if (buffer && buffer.byteLength > 0) {
              setFileBuffer(buffer);
              fileDataLoaded = true;
            } else {
              throw new Error('Empty buffer received');
            }
          }
        } catch (error) {
          setSnackbar({
            open: true,
            message: 'Failed to load preview. Redirecting to the original document shortly...',
            severity: 'info',
          });
          
          let webUrl = record?.webUrl;

          if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
            const baseUrl = `${window.location.protocol}//${window.location.host}`;
            webUrl = baseUrl + webUrl;
          }
          
          setTimeout(() => {
            if (webUrl) {
              try {
                window.open(webUrl, '_blank', 'noopener,noreferrer');
              } catch (openError) {
                console.error('Error opening new tab:', openError);
                setSnackbar({
                  open: true,
                  message: 'Failed to automatically open the document. Please check your browser pop-up settings.',
                  severity: 'error',
                });
              }
            } else {
              console.error('Cannot redirect: No webUrl found for the record.');
              setSnackbar({
                open: true,
                message: 'Failed to load preview and cannot redirect (document URL not found).',
                severity: 'error',
              });
            }
          }, 2500);
          return;
        }
      } else if (record.origin === ORIGIN.CONNECTOR) {
        try {
          let params = {};
          if (['pptx', 'ppt'].includes(record?.extension)) {
            params = {
              convertTo: 'pdf',
            };
            handleLargePPTFile(record);
          }
          
          const publicConnectorUrlResponse = await getConnectorPublicUrl();
          let response;
          
          if (publicConnectorUrlResponse && publicConnectorUrlResponse.url) {
            const CONNECTOR_URL = publicConnectorUrlResponse.url;
            response = await axios.get(`${CONNECTOR_URL}/api/v1/stream/record/${recordId}`, {
              responseType: 'blob',
              params,
            });
          } else {
            response = await axios.get(
              `${CONFIG.backendUrl}/api/v1/knowledgeBase/stream/record/${recordId}`,
              {
                responseType: 'blob',
                params,
              }
            );
          }
          
          if (!response) return;

          let filename;
          const contentDisposition = response.headers['content-disposition'];

          if (contentDisposition) {
            // First try to parse filename*=UTF-8'' format (RFC 5987) for Unicode support
            const filenameStarMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
            if (filenameStarMatch && filenameStarMatch[1]) {
              // Decode the percent-encoded UTF-8 filename
              try {
                filename = decodeURIComponent(filenameStarMatch[1]);
              } catch (e) {
                console.error('Failed to decode UTF-8 filename', e);
              }
            }
            
            // Fallback to basic filename="..." format if filename* not found
            if (!filename) {
              const filenameMatch = contentDisposition.match(/filename="?([^";\n]*)"?/i);
              if (filenameMatch && filenameMatch[1]) {
                filename = filenameMatch[1];
              }
            }
          }

          if(!filename && record.recordName) {
            filename = record.recordName;
          }

          const bufferReader = new FileReader();
          const arrayBufferPromise = new Promise<ArrayBuffer>((resolve, reject) => {
            bufferReader.onload = () => {
              const originalBuffer = bufferReader.result as ArrayBuffer;
              const bufferCopy = originalBuffer.slice(0);
              resolve(bufferCopy);
            };
            bufferReader.onerror = () => {
              reject(new Error('Failed to read blob as array buffer'));
            };
            bufferReader.readAsArrayBuffer(response.data);
          });

          const buffer = await arrayBufferPromise;
          if (buffer && buffer.byteLength > 0) {
            setFileBuffer(buffer);
            fileDataLoaded = true;
          } else {
            throw new Error('Empty buffer received');
          }
        } catch (err) {
          console.error('Error downloading document:', err);
          setSnackbar({
            open: true,
            message: 'Failed to load preview. Redirecting to the original document shortly...',
            severity: 'info',
          });
          
          let webUrl = record?.webUrl;
          
          if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
            const baseUrl = `${window.location.protocol}//${window.location.host}`;
            webUrl = baseUrl + webUrl;
          }

          setTimeout(() => {
            if (webUrl) {
              try {
                window.open(webUrl, '_blank', 'noopener,noreferrer');
              } catch (openError) {
                console.error('Error opening new tab:', openError);
                setSnackbar({
                  open: true,
                  message: 'Failed to automatically open the document. Please check your browser pop-up settings.',
                  severity: 'error',
                });
              }
            } else {
              console.error('Cannot redirect: No webUrl found for the record.');
              setSnackbar({
                open: true,
                message: 'Failed to load preview and cannot redirect (document URL not found).',
                severity: 'error',
              });
            }
          }, 2500);

          return;
        }
      }

      // Only set the document type if file data was successfully loaded
      if (fileDataLoaded) {
        const documentType = getDocumentType(extension);
        switch (documentType) {
          case 'pdf':
            setIsPdf(true);
            break;
          case 'excel':
            setIsExcel(true);
            break;
          case 'docx':
            setIsDocx(true);
            break;
          case 'html':
            setIsHtml(true);
            break;
          case 'text':
            setIsTextFile(true);
            break;
          case 'md':
          case 'mdx':
            setIsMarkdown(true);
            break;
          case 'image':
            setIsImage(true);
            break;
          default:
            setSnackbar({
              open: true,
              message: `Unsupported document type: ${extension}`,
              severity: 'warning',
            });
        }
      } else {
        setSnackbar({
          open: true,
          message: 'No document data was loaded. Please try again.',
          severity: 'error',
        });
      }
    } catch (error) {
      console.error('Error fetching document:', error);
    }
  };

  const toggleSidebar = () => {
    setOpenSidebar((prev) => !prev);
  };

  const handleCloseViewer = () => {
    setIsPdf(false);
    setIsExcel(false);
    setIsHtml(false);
    setIsDocx(false);
    setIsTextFile(false);
    setIsMarkdown(false);
    setIsImage(false);
    setFileBuffer(null);
    setHighlightedCitation(null);
  };

  const handleCloseSnackbar = () => {
    setSnackbar({
      ...snackbar,
      open: false,
    });
  };

  const renderDocumentViewer = () => {
    if (isPdf && (fileUrl || fileBuffer)) {
      return (
        <PdfHighlighterComp
          key={`pdf-viewer-${recordCitations?.recordId || 'new'}`}
          pdfUrl={fileUrl}
          pdfBuffer={fileBuffer}
          citations={recordCitations?.documents || []}
          highlightCitation={highlightedCitation}
          onClosePdf={handleCloseViewer}
        />
      );
    }

    if (isDocx && (fileUrl || fileBuffer)) {
      return (
        <DocxViewer
          key={`docx-viewer-${recordCitations?.recordId || 'new'}`}
          url={fileUrl}
          buffer={fileBuffer}
          citations={recordCitations?.documents || []}
          highlightCitation={highlightedCitation}
          renderOptions={{
            breakPages: true,
            renderHeaders: true,
            renderFooters: true,
          }}
          onClosePdf={handleCloseViewer}
        />
      );
    }

    if (isExcel && (fileUrl || fileBuffer)) {
      return (
        <ExcelViewer
          key={`excel-viewer-${recordCitations?.recordId || 'new'}`}
          fileUrl={fileUrl}
          citations={recordCitations?.documents || []}
          excelBuffer={fileBuffer}
          highlightCitation={highlightedCitation}
          onClosePdf={handleCloseViewer}
        />
      );
    }

    if (isHtml && (fileUrl || fileBuffer)) {
      return (
        <HtmlViewer
          key={`html-viewer-${recordCitations?.recordId || 'new'}`}
          url={fileUrl}
          citations={recordCitations?.documents || []}
          buffer={fileBuffer}
          highlightCitation={highlightedCitation}
          onClosePdf={handleCloseViewer}
        />
      );
    }

    if (isTextFile && (fileUrl || fileBuffer)) {
      return (
        <TextViewer
          key={`text-viewer-${recordCitations?.recordId || 'new'}`}
          url={fileUrl}
          citations={recordCitations?.documents || []}
          buffer={fileBuffer}
          highlightCitation={highlightedCitation}
          onClosePdf={handleCloseViewer}
        />
      );
    }
    
    if (isMarkdown && (fileUrl || fileBuffer)) {
      return (
        <MarkdownViewer
          key={`markdown-viewer-${recordCitations?.recordId || 'new'}`}
          url={fileUrl}
          citations={recordCitations?.documents || []}
          buffer={fileBuffer}
          highlightCitation={highlightedCitation}
          onClosePdf={handleCloseViewer}
        />
      );
    }

    if (isImage && (fileUrl || fileBuffer)) {
      return (
        <ImageHighlighter
          key={`image-viewer-${recordCitations?.recordId || 'new'}`}
          url={fileUrl}
          buffer={fileBuffer}
          citations={recordCitations?.documents || []}
          highlightCitation={highlightedCitation}
          onClosePdf={handleCloseViewer}
        />
      );
    }

    return null;
  };

  return (
    <Box
      sx={{
        display: 'flex',
        overflow: 'hidden',
        bgcolor: alpha(theme.palette.background.default, 0.7),
        position: 'relative',
      }}
    >
      <KnowledgeSearchSideBar
        sx={{
          height: '100%',
          zIndex: 100,
          flexShrink: 0,
          boxShadow: '0 0 10px rgba(0,0,0,0.05)',
        }}
        filters={filters}
        onFilterChange={handleFilterChange}
        openSidebar={openSidebar}
        onToggleSidebar={toggleSidebar}
      />

      <Box
        sx={{
          maxHeight: '100vh',
          width: openSidebar
            ? `calc(100% - ${SIDEBAR_EXPANDED_WIDTH}px)`
            : `calc(100% - ${SIDEBAR_COLLAPSED_WIDTH}px)`,
          transition: theme.transitions.create('width', {
            duration: '0.25s',
            easing: theme.transitions.easing.sharp,
          }),
          display: 'flex',
          position: 'relative',
        }}
      >
        <Box
          sx={{
            width: isCitationViewerOpen ? '50%' : '100%',
            height: '100%',
            transition: theme.transitions.create('width', {
              duration: '0.25s',
              easing: theme.transitions.easing.sharp,
            }),
            overflow: 'auto',
            maxHeight: '100%',
            ...scrollableStyles,
          }}
        >
          <KnowledgeSearch
            searchResults={searchResults}
            loading={loading}
            canLoadMore={canLoadMore}
            onSearchQueryChange={handleSearchQueryChange}
            onTopKChange={handleTopKChange}
            onViewCitations={viewCitations}
            recordsMap={recordsMap}
            allConnectors={allConnectors}
          />
        </Box>
        
        {isCitationViewerOpen && (
          <Divider orientation="vertical" flexItem sx={{ borderRightWidth: 3 }} />
        )}

        {isCitationViewerOpen && (
          <Box
            id="document-container"
            sx={{
              width: '65%',
              height: '100%',
              position: 'relative',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {renderDocumentViewer()}
          </Box>
        )}
      </Box>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}