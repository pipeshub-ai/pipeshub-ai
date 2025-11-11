// RecordDocumentViewer.tsx - Fixed for Mail Records
import dayjs from 'dayjs';
import { Icon } from '@iconify/react';
import React, { useState, useCallback, useRef, useEffect } from 'react';
import downloadIcon from '@iconify-icons/mdi/download';
import zipIcon from '@iconify-icons/vscode-icons/file-type-zip';
import pdfIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import imageIcon from '@iconify-icons/vscode-icons/file-type-image';
import defaultFileIcon from '@iconify-icons/mdi/file-document-outline';
import emailIcon from '@iconify-icons/mdi/email-outline'; // Add email icon
import eyeIcon from '@iconify-icons/mdi/eye';
import closeIcon from '@iconify-icons/mdi/close';
import fullscreenIcon from '@iconify-icons/mdi/fullscreen';
import fullscreenExitIcon from '@iconify-icons/mdi/fullscreen-exit';
import mdIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import htmlIcon from '@iconify-icons/vscode-icons/file-type-html';
import jsonIcon from '@iconify-icons/vscode-icons/file-type-json';
import databaseIcon from '@iconify-icons/mdi/database';
import xlsIcon from '@iconify-icons/vscode-icons/file-type-excel';
import docIcon from '@iconify-icons/vscode-icons/file-type-word';
import pptIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import txtIcon from '@iconify-icons/vscode-icons/file-type-text';

import {
  Box,
  Stack,
  Typography,
  IconButton,
  CircularProgress,
  Fade,
  Alert,
  Snackbar,
  useTheme,
  Tooltip,
} from '@mui/material';

import axios from 'src/utils/axios';
import { CONFIG } from 'src/config-global';
import type { Record } from './types/record-details';
import { getConnectorPublicUrl } from '../accountdetails/account-settings/services/utils/services-configuration-service';
import { ORIGIN } from './constants/knowledge-search';
import PdfHighlighterComp from '../qna/chatbot/components/pdf-highlighter';
import DocxViewer from '../qna/chatbot/components/docx-highlighter';
import ExcelViewer from '../qna/chatbot/components/excel-highlighter';
import HtmlViewer from '../qna/chatbot/components/html-highlighter';
import TextViewer from '../qna/chatbot/components/text-highlighter';
import MarkdownViewer from '../qna/chatbot/components/markdown-highlighter';
import { KnowledgeBaseAPI } from './services/api';
import ImageHighlighter from '../qna/chatbot/components/image-highlighter';

// Simplified state management for viewport mode
interface DocumentViewerState {
  phase: 'idle' | 'loading' | 'ready' | 'error' | 'closing';
  documentType: string | null;
  fileUrl: string;
  fileBuffer: ArrayBuffer | null;
  recordCitations: any | null;
  error: string | null;
  loadingStep: string;
}

// Enhanced utility functions to handle both file and mail records
const getFileIcon = (extension: string, recordType?: string) => {
  // Handle mail records
  if (recordType === 'MAIL') {
    return emailIcon;
  }

  const ext = extension?.replace('.', '').toLowerCase();
  switch (ext) {
    case 'pdf':
      return pdfIcon;
    case 'doc':
    case 'docx':
      return docIcon;
    case 'xls':
    case 'xlsx':
    case 'csv':
      return xlsIcon;
    case 'ppt':
    case 'pptx':
      return pptIcon;
    case 'jpg':
    case 'jpeg':
    case 'png':
    case 'gif':
    case 'webp':
    case 'svg':
      return imageIcon;
    case 'zip':
    case 'rar':
    case '7z':
      return zipIcon;
    case 'txt':
      return txtIcon;
    case 'html':
    case 'css':
    case 'js':
      return htmlIcon;
    case 'md':
    case 'mdx':
      return mdIcon;
    case 'json':
      return jsonIcon;
    case 'database':
      return databaseIcon;
    default:
      return defaultFileIcon;
  }
};

const getExtensionColor = (extension: string, recordType?: string) => {
  // Handle mail records
  if (recordType === 'MAIL') {
    return '#1976d2'; // Blue for emails
  }

  const ext = extension?.replace('.', '').toLowerCase();
  switch (ext) {
    case 'pdf':
      return '#FF4B4B';
    case 'doc':
    case 'docx':
      return '#2B579A';
    case 'xls':
    case 'xlsx':
      return '#217346';
    case 'jpg':
    case 'jpeg':
    case 'png':
    case 'webp':
    case 'svg':
      return '#4BAFFF';
    case 'zip':
    case 'rar':
      return '#FFA000';
    default:
      return '#757575';
  }
};

function getDocumentType(extension: string, recordType?: string) {
  // Handle mail records - treat as HTML for rendering
  if (recordType === 'MAIL') {
    return 'html';
  }

  if (extension === 'pdf') return 'pdf';
  if (['xlsx', 'xls', 'csv'].includes(extension)) return 'excel';
  if (extension === 'docx') return 'docx';
  if (extension === 'html') return 'html';
  if (extension === 'txt') return 'text';
  if (extension === 'md') return 'md';
  if (extension === 'mdx') return 'mdx';
  if (['ppt', 'pptx'].includes(extension)) return 'ppt';
  if (['jpg', 'jpeg', 'png', 'webp', 'svg'].includes(extension)) return 'image';
  return 'other';
}

interface RecordDocumentViewerProps {
  record: Record;
}

// Professional Minimalistic Loading Animation
const ViewportLoadingAnimation = ({ fileName, step }: { fileName: string; step: string }) => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        position: 'fixed',
        top: 64,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 1400,
        backgroundColor: theme.palette.background.default,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Box
        sx={{
          textAlign: 'center',
          maxWidth: 320,
          mx: 2,
        }}
      >
        {/* Minimalistic Document Icon */}
        <Box
          sx={{
            width: 48,
            height: 48,
            mx: 'auto',
            mb: 3,
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Box
            sx={{
              width: 32,
              height: 40,
              borderRadius: 1,
              border: `2px solid ${theme.palette.text.secondary}`,
              position: 'relative',
              opacity: 0.7,
              '&::before': {
                content: '""',
                position: 'absolute',
                top: -2,
                right: -2,
                width: 8,
                height: 8,
                borderLeft: `2px solid ${theme.palette.text.secondary}`,
                borderBottom: `2px solid ${theme.palette.text.secondary}`,
                backgroundColor: theme.palette.background.default,
              },
              '&::after': {
                content: '""',
                position: 'absolute',
                top: 8,
                left: 6,
                right: 6,
                height: 2,
                backgroundColor: theme.palette.text.secondary,
                opacity: 0.3,
                boxShadow: `0 6px 0 ${theme.palette.text.secondary}30, 0 12px 0 ${theme.palette.text.secondary}30`,
              },
            }}
          />
        </Box>

        {/* Clean Typography */}
        <Typography
          variant="h6"
          sx={{
            mb: 1,
            fontWeight: 500,
            color: theme.palette.text.primary,
            letterSpacing: '-0.02em',
          }}
        >
          Opening Document
        </Typography>

        <Typography
          variant="body2"
          color="text.primary"
          sx={{
            mb: 1,
            fontWeight: 400,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {fileName}
        </Typography>

        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            mb: 4,
            display: 'block',
          }}
        >
          {step}
        </Typography>

        {/* Minimalistic Progress Indicator */}
        <Box sx={{ position: 'relative', display: 'inline-flex' }}>
          {/* Subtle spinning indicator */}
          <Box
            sx={{
              width: 24,
              height: 24,
              border: `2px solid ${theme.palette.divider}`,
              borderTop: `2px solid ${theme.palette.primary.main}`,
              borderRadius: '50%',
              animation: 'spin 1s linear infinite',
              '@keyframes spin': {
                '0%': { transform: 'rotate(0deg)' },
                '100%': { transform: 'rotate(360deg)' },
              },
            }}
          />
        </Box>

        {/* Optional: Three dot indicator as alternative */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            gap: 0.5,
            mt: 3,
          }}
        >
          {[0, 1, 2].map((index) => (
            <Box
              key={index}
              sx={{
                width: 4,
                height: 4,
                borderRadius: '50%',
                backgroundColor: theme.palette.text.secondary,
                opacity: 0.4,
                animation: `dot-pulse 1.4s ${index * 0.2}s infinite ease-in-out`,
                '@keyframes dot-pulse': {
                  '0%, 80%, 100%': {
                    opacity: 0.4,
                    transform: 'scale(1)',
                  },
                  '40%': {
                    opacity: 1,
                    transform: 'scale(1.2)',
                  },
                },
              }}
            />
          ))}
        </Box>
      </Box>
    </Box>
  );
};

const RecordDocumentViewer = ({ record }: RecordDocumentViewerProps) => {
  const [isDownloading, setIsDownloading] = useState<boolean>(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'error' as 'error' | 'warning' | 'info' | 'success',
  });

  // Enhanced state management with fullscreen tracking
  const [viewerState, setViewerState] = useState<DocumentViewerState>({
    phase: 'idle',
    documentType: null,
    fileUrl: '',
    fileBuffer: null,
    recordCitations: null,
    error: null,
    loadingStep: '',
  });

  // Fullscreen state management
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Single cleanup timeout
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(
    () => () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    },
    []
  );

  // Fullscreen event handlers
  useEffect(() => {
    const handleFullscreenChange = () => {
      const isCurrentlyFullscreen = !!document.fullscreenElement;
      setIsFullscreen(isCurrentlyFullscreen);

      // If user exits fullscreen using browser controls, we should detect it
      if (!isCurrentlyFullscreen && isFullscreen) {
        // User exited fullscreen, but we're still in ready state - this is fine
        // We'll just update our fullscreen state
      }
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
    };
  }, [isFullscreen]);

  const resetViewerState = useCallback(() => {
    setViewerState({
      phase: 'idle',
      documentType: null,
      fileUrl: '',
      fileBuffer: null,
      recordCitations: null,
      error: null,
      loadingStep: '',
    });
  }, []);

  const handleCloseViewer = useCallback(() => {
    // Exit fullscreen if we're in it
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(console.error);
    }

    setViewerState((prev) => ({ ...prev, phase: 'closing' }));

    if (timeoutRef.current) clearTimeout(timeoutRef.current);

    timeoutRef.current = setTimeout(() => {
      resetViewerState();
      timeoutRef.current = null;
    }, 300);
  }, [resetViewerState]);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (!document.fullscreenElement && containerRef.current) {
        // Enter fullscreen
        await containerRef.current.requestFullscreen();
      } else {
        // Exit fullscreen
        await document.exitFullscreen();
      }
    } catch (err) {
      console.error('Error toggling fullscreen:', err);
      setSnackbar({
        open: true,
        message: 'Unable to toggle fullscreen mode.',
        severity: 'warning',
      });
    }
  }, []);

  const showErrorAndRedirect = useCallback(
    (errorMessage: string) => {
      setSnackbar({
        open: true,
        message: 'Failed to load preview. Redirecting to the original document shortly...',
        severity: 'info',
      });

      let webUrl = record.fileRecord?.webUrl || record.mailRecord?.webUrl;

      if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
        const baseUrl = `${window.location.protocol}//${window.location.host}`;
        webUrl = baseUrl + webUrl;
      }

      setTimeout(() => {
        handleCloseViewer();
      }, 500);

      setTimeout(() => {
        if (webUrl) {
          try {
            window.open(webUrl, '_blank', 'noopener,noreferrer');
          } catch (openError) {
            console.error('Error opening new tab:', openError);
            setSnackbar({
              open: true,
              message:
                'Failed to automatically open the document. Please check your browser pop-up settings.',
              severity: 'error',
            });
          }
        } else {
          setSnackbar({
            open: true,
            message: 'Failed to load preview and cannot redirect (document URL not found).',
            severity: 'error',
          });
        }
      }, 2500);
    },
    [record, handleCloseViewer]
  );

  // Fixed early return - check for either fileRecord OR mailRecord
  if (!record?.fileRecord && !record?.mailRecord) return null;

  const {
    recordName,
    externalRecordId,
    sourceCreatedAtTimestamp,
    fileRecord,
    mailRecord,
    origin,
    recordType,
  } = record;

  // Get the appropriate record data and extension
  const currentRecord = fileRecord || mailRecord;
  const extension = fileRecord?.extension || 'eml'; // Use 'eml' for email records
  const recordTypeForDisplay = recordType || 'FILE';

  const handleDownload = async () => {
    try {
      setIsDownloading(true);
      const recordId = origin === ORIGIN.UPLOAD ? externalRecordId : record._key;
      await KnowledgeBaseAPI.handleDownloadDocument(recordId, recordName, origin);
    } catch (error) {
      console.error('Failed to download document:', error);
      setSnackbar({
        open: true,
        message: 'Failed to download document. Please try again.',
        severity: 'error',
      });
    } finally {
      setIsDownloading(false);
    }
  };

  const viewDocument = async (): Promise<void> => {
    // Start with loading phase
    setViewerState((prev) => ({
      ...prev,
      phase: 'loading',
      loadingStep: 'Preparing to load document...',
      error: null,
    }));

    try {
      const recordId = record._key;

      if (!record) {
        console.error('Record not found for ID:', recordId);
        setSnackbar({
          open: true,
          message: 'Record not found. Please try again.',
          severity: 'error',
        });
        handleCloseViewer();
        return;
      }

      let fileDataLoaded = false;
      let loadedFileUrl = '';
      let loadedFileBuffer: ArrayBuffer | null = null;

      if (record.origin === ORIGIN.UPLOAD) {
        try {
          setViewerState((prev) => ({ ...prev, loadingStep: 'Downloading document...' }));

          const downloadResponse = await axios.get(
            `/api/v1/document/${externalRecordId}/download`,
            { responseType: 'blob' }
          );

          setViewerState((prev) => ({ ...prev, loadingStep: 'Processing document data...' }));

          const reader = new FileReader();
          const textPromise = new Promise<string>((resolve) => {
            reader.onload = () => {
              resolve(reader.result?.toString() || '');
            };
          });

          reader.readAsText(downloadResponse.data);
          const text = await textPromise;

          try {
            const jsonData = JSON.parse(text);
            if (jsonData && jsonData.signedUrl) {
              loadedFileUrl = jsonData.signedUrl;
              fileDataLoaded = true;
            }
          } catch (e) {
            const bufferReader = new FileReader();
            const arrayBufferPromise = new Promise<ArrayBuffer>((resolve) => {
              bufferReader.onload = () => {
                resolve(bufferReader.result as ArrayBuffer);
              };
              bufferReader.readAsArrayBuffer(downloadResponse.data);
            });

            loadedFileBuffer = await arrayBufferPromise;
            fileDataLoaded = true;
          }
        } catch (error) {
          console.error('Error downloading document:', error);
          showErrorAndRedirect('Failed to load document from upload');
          return;
        }
      } else if (record.origin === ORIGIN.CONNECTOR) {
        try {
          setViewerState((prev) => ({ ...prev, loadingStep: 'Connecting to document source...' }));

          let params = {};

          // Handle PowerPoint files
          if (record?.fileRecord && ['pptx', 'ppt'].includes(record?.fileRecord?.extension)) {
            params = { convertTo: 'pdf' };
            if (record.fileRecord.sizeInBytes / 1048576 > 5) {
              throw new Error('Large file size, redirecting to web page');
            }
          }

          const publicConnectorUrlResponse = await getConnectorPublicUrl();
          let connectorResponse;

          if (publicConnectorUrlResponse && publicConnectorUrlResponse.url) {
            const CONNECTOR_URL = publicConnectorUrlResponse.url;
            connectorResponse = await axios.get(
              `${CONNECTOR_URL}/api/v1/stream/record/${recordId}`,
              { responseType: 'blob', params }
            );
          } else {
            connectorResponse = await axios.get(
              `${CONFIG.backendUrl}/api/v1/knowledgeBase/stream/record/${recordId}`,
              { responseType: 'blob', params }
            );
          }

          if (!connectorResponse) return;

          setViewerState((prev) => ({ ...prev, loadingStep: 'Processing document...' }));

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
            bufferReader.readAsArrayBuffer(connectorResponse.data);
          });

          loadedFileBuffer = await arrayBufferPromise;
          fileDataLoaded = true;
        } catch (err) {
          console.error('Error downloading document:', err);
          showErrorAndRedirect('Failed to load document from connector');
          return;
        }
      }

      if (fileDataLoaded) {
        // Use recordType to determine document type for mail records
        const documentType = getDocumentType(extension, recordTypeForDisplay);

        setViewerState((prev) => ({ ...prev, loadingStep: 'Opening in viewport...' }));

        // Small delay to show final step
        setTimeout(() => {
          setViewerState((prev) => ({
            ...prev,
            phase: 'ready',
            documentType,
            fileUrl: loadedFileUrl,
            fileBuffer: loadedFileBuffer,
            recordCitations: null,
          }));
        }, 800);

        // Support mail records in addition to existing types
        if (!['pdf', 'excel', 'docx', 'html', 'text', 'md', 'mdx', 'image'].includes(documentType)) {
          setSnackbar({
            open: true,
            message: `Unsupported document type: ${extension}`,
            severity: 'warning',
          });
          handleCloseViewer();
        }
      } else {
        setSnackbar({
          open: true,
          message: 'No document data was loaded. Please try again.',
          severity: 'error',
        });
        handleCloseViewer();
      }
    } catch (error) {
      console.error('Error fetching document:', error);
      setViewerState((prev) => ({
        ...prev,
        phase: 'error',
        error: 'Failed to load document. Please try again.',
      }));

      setTimeout(() => {
        handleCloseViewer();
      }, 2000);
    }
  };

  const renderDocumentViewer = () => {
    const { documentType, fileUrl, fileBuffer, recordCitations } = viewerState;

    if (!documentType || (!fileUrl && !fileBuffer)) return null;

    const commonProps = {
      key: `${documentType}-viewer-${recordCitations?.recordId || 'new'}`,
      onClosePdf: handleCloseViewer,
    };

    switch (documentType) {
      case 'pdf':
        return (
          <PdfHighlighterComp
            {...commonProps}
            pdfUrl={fileUrl}
            pdfBuffer={fileBuffer}
            citations={[]}
          />
        );
      case 'docx':
        return (
          <DocxViewer
            {...commonProps}
            url={fileUrl}
            buffer={fileBuffer}
            citations={[]}
            renderOptions={{
              breakPages: true,
              renderHeaders: true,
              renderFooters: true,
            }}
          />
        );
      case 'excel':
        return (
          <ExcelViewer
            {...commonProps}
            fileUrl={fileUrl}
            citations={recordCitations?.documents || []}
            excelBuffer={fileBuffer}
          />
        );
      case 'html':
        return (
          <HtmlViewer
            {...commonProps}
            url={fileUrl}
            citations={recordCitations?.documents || []}
            buffer={fileBuffer}
          />
        );
      case 'text':
        return (
          <TextViewer
            {...commonProps}
            url={fileUrl}
            citations={recordCitations?.documents || []}
            buffer={fileBuffer}
          />
        );
      case 'md':
        return (
          <MarkdownViewer
            {...commonProps}
            url={fileUrl}
            citations={recordCitations?.documents || []}
            buffer={fileBuffer}
          />
        );
      case 'mdx':
        return (
          <MarkdownViewer
            {...commonProps}
            url={fileUrl}
            citations={recordCitations?.documents || []}
            buffer={fileBuffer}
          />
        );
      case 'image':
        return (
          <ImageHighlighter
            {...commonProps}
            url={fileUrl}
            buffer={fileBuffer}
            citations={recordCitations?.documents || []}
          />
        );
      default:
        return null;
    }
  };

  return (
    <>
      {/* Main Document Card */}
      <Box
        sx={{
          maxWidth: 800,
          width: '100%',
          p: 2,
          opacity: viewerState.phase === 'loading' ? 0.5 : 1,
          transition: 'opacity 0.3s ease-in-out',
          pointerEvents: viewerState.phase === 'loading' ? 'none' : 'auto',
        }}
      >
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 3 }}>
          <Icon
            icon={getFileIcon(extension, recordTypeForDisplay)}
            width={40}
            height={40}
            style={{ color: getExtensionColor(extension, recordTypeForDisplay) }}
          />
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" gutterBottom>
              {recordName}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {recordTypeForDisplay === 'MAIL' ? 'Email received' : 'Added'} on{' '}
              {dayjs(sourceCreatedAtTimestamp).format('MMM DD, YYYY')}
            </Typography>
            {/* Show additional info for email records */}
            {recordTypeForDisplay === 'MAIL' && mailRecord && (
              <Typography variant="caption" color="text.secondary" display="block">
                From: {mailRecord.from}
              </Typography>
            )}
          </Box>

          {/* Download Button */}
          <Tooltip title="Download document" arrow placement="top">
            {isDownloading ? (
              <Box sx={{ p: 1 }}>
                <CircularProgress size={24} />
              </Box>
            ) : (
              <>
                {recordTypeForDisplay !== 'MAIL' && (
                  <>
                    <IconButton
                      onClick={handleDownload}
                      sx={{
                        color: 'primary.main',
                        '&:hover': {
                          backgroundColor: 'primary.light',
                          color: 'white',
                        },
                      }}
                      disabled={viewerState.phase === 'loading'}
                    >
                      <Icon icon={downloadIcon} width={24} />
                    </IconButton>
                  </>
                )}
              </>
            )}
          </Tooltip>

          {/* View Document Button */}
          <Tooltip
            title={recordTypeForDisplay === 'MAIL' ? 'Preview email' : 'Preview document'}
            arrow
            placement="top"
          >
            <IconButton
              onClick={viewDocument}
              sx={{
                color: 'primary.main',
                '&:hover': {
                  backgroundColor: 'primary.light',
                  color: 'white',
                },
              }}
              disabled={viewerState.phase === 'loading'}
            >
              <Icon icon={eyeIcon} width={24} />
            </IconButton>
          </Tooltip>
        </Stack>
      </Box>

      {/* Beautiful Loading Animation */}
      {viewerState.phase === 'loading' && (
        <ViewportLoadingAnimation fileName={recordName} step={viewerState.loadingStep} />
      )}

      {/* Clean Viewport Document Viewer */}
      {viewerState.phase === 'ready' && (
        <Fade in={Boolean(true)} timeout={600}>
          <Box
            ref={containerRef}
            sx={{
              position: 'fixed',
              top: isFullscreen ? 0 : 64, // Full height when fullscreen, below navbar otherwise
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 1300,
              backgroundColor: '#fff',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {/* Header with Close Button - Hide in fullscreen */}
            {!isFullscreen && (
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  p: 2,
                  borderBottom: '1px solid',
                  borderColor: 'divider',
                  backgroundColor: 'background.paper',
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                  <Icon
                    icon={getFileIcon(extension, recordTypeForDisplay)}
                    width={24}
                    height={24}
                    style={{ color: getExtensionColor(extension, recordTypeForDisplay) }}
                  />
                  <Typography variant="h6" noWrap>
                    {recordName}
                  </Typography>
                </Box>

                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  {/* Fullscreen Toggle Button */}
                  <Tooltip title="Enter fullscreen mode" arrow placement="top">
                    <IconButton
                      onClick={toggleFullscreen}
                      sx={{
                        color: 'text.secondary',
                        '&:hover': {
                          backgroundColor: 'action.hover',
                        },
                      }}
                    >
                      <Icon icon={fullscreenIcon} width={20} />
                    </IconButton>
                  </Tooltip>

                  {/* Close Button */}
                  <Tooltip title="Close document viewer" arrow placement="top">
                    <IconButton
                      onClick={handleCloseViewer}
                      sx={{
                        color: 'text.secondary',
                        '&:hover': {
                          backgroundColor: 'action.hover',
                        },
                      }}
                    >
                      <Icon icon={closeIcon} width={20} />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Box>
            )}

            {/* Fullscreen Controls - Show only in fullscreen */}
            {isFullscreen && (
              <Box
                sx={{
                  position: 'absolute',
                  top: 16,
                  right: 16,
                  zIndex: 1400,
                  display: 'flex',
                  gap: 1,
                }}
              >
                {/* Exit Fullscreen Button */}
                <IconButton
                  onClick={toggleFullscreen}
                  sx={{
                    backgroundColor: 'rgba(0, 0, 0, 0.7)',
                    color: 'white',
                    '&:hover': {
                      backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    },
                  }}
                  title="Exit fullscreen"
                >
                  <Icon icon={fullscreenExitIcon} width={20} />
                </IconButton>

                {/* Close Button */}
                <IconButton
                  onClick={handleCloseViewer}
                  sx={{
                    backgroundColor: 'rgba(0, 0, 0, 0.7)',
                    color: 'white',
                    '&:hover': {
                      backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    },
                  }}
                  title="Close document"
                >
                  <Icon icon={closeIcon} width={20} />
                </IconButton>
              </Box>
            )}

            {/* Document Content */}
            <Box sx={{ flex: 1, overflow: 'hidden' }}>{renderDocumentViewer()}</Box>
          </Box>
        </Fade>
      )}

      {/* Error State */}
      {viewerState.phase === 'error' && (
        <Fade in={Boolean(true)} timeout={300}>
          <Box
            sx={{
              position: 'fixed',
              top: 64,
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 1300,
              backgroundColor: 'rgba(0, 0, 0, 0.8)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Alert severity="error" sx={{ maxWidth: 400, mx: 2 }}>
              {viewerState.error}
            </Alert>
          </Box>
        </Fade>
      )}

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          severity={snackbar.severity}
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </>
  );
};

export default RecordDocumentViewer;
