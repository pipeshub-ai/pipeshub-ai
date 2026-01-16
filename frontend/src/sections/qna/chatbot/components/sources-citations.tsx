import React, { useMemo, useState, useCallback } from 'react';
import { Icon, IconifyIcon } from '@iconify/react';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import downIcon from '@iconify-icons/mdi/chevron-down';
import upIcon from '@iconify-icons/mdi/chevron-up';
import rightIcon from '@iconify-icons/mdi/chevron-right';
import fileDocIcon from '@iconify-icons/mdi/file-document-outline';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import linkIcon from '@iconify-icons/mdi/open-in-new';
import pdfIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import docIcon from '@iconify-icons/vscode-icons/file-type-word';
import xlsIcon from '@iconify-icons/vscode-icons/file-type-excel';
import pptIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import txtIcon from '@iconify-icons/vscode-icons/file-type-text';
import mdIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import htmlIcon from '@iconify-icons/vscode-icons/file-type-html';
import jsonIcon from '@iconify-icons/vscode-icons/file-type-json';
import zipIcon from '@iconify-icons/vscode-icons/file-type-zip';
import imageIcon from '@iconify-icons/vscode-icons/file-type-image';
import databaseIcon from '@iconify-icons/mdi/database';

import {
  Box,
  Paper,
  Stack,
  Button,
  Collapse,
  Typography,
  alpha,
  useTheme,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  IconButton,
  Link,
} from '@mui/material';

import type { CustomCitation } from 'src/types/chat-bot';
import type { Record } from 'src/types/chat-message';
import {
  getWebUrlWithFragment,
} from 'src/sections/knowledgebase/utils/utils';

import { useConnectors } from '../../../accountdetails/connectors/context';

// File type configuration with modern icons
const FILE_CONFIG = {
  icons: {
    pdf: pdfIcon,
    doc: docIcon,
    docx: docIcon,
    xls: xlsIcon,
    xlsx: xlsIcon,
    ppt: pptIcon,
    pptx: pptIcon,
    txt: txtIcon,
    md: mdIcon,
    html: htmlIcon,
    csv: xlsIcon,
    json: jsonIcon,
    zip: zipIcon,
    png: imageIcon,
    jpg: imageIcon,
    jpeg: imageIcon,
  },
  colors: {
    pdf: '#FF5722',
    doc: '#2196F3',
    docx: '#2196F3',
    xls: '#4CAF50',
    xlsx: '#4CAF50',
    ppt: '#FF9800',
    pptx: '#FF9800',
    txt: '#757575',
    md: '#9C27B0',
    html: '#FF5722',
    csv: '#4CAF50',
    json: '#FFB74D',
    zip: '#795548',
    png: '#E91E63',
    jpg: '#E91E63',
    jpeg: '#E91E63',
  },
  viewableExtensions: [
    'pdf',
    'xlsx',
    'xls',
    'csv',
    'docx',
    'html',
    'txt',
    'md',
    'mdx',
    'ppt',
    'pptx',
  ],
};

// Default fallback icon for unknown connectors
const DEFAULT_CONNECTOR_ICON = databaseIcon;

interface FileInfo {
  recordId: string;
  recordName: string;
  extension: string;
  webUrl?: string;
  citationCount: number;
  citation: CustomCitation;
  connector: string;
}

interface SourcesAndCitationsProps {
  citations: CustomCitation[];
  aggregatedCitations: { [key: string]: CustomCitation[] };
  onRecordClick: (record: Record) => void;
  onViewPdf: (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => Promise<void>;
  className?: string;
  modelInfo?: {
    modelName?: string;
    modelKey?: string;
    chatMode?: string;
  } | null;
}

const getFileIcon = (extension: string): IconifyIcon =>
  FILE_CONFIG.icons[extension?.toLowerCase() as keyof typeof FILE_CONFIG.icons] || fileDocIcon;

const isDocViewable = (extension: string): boolean => {
  if (!extension) return false;
  return FILE_CONFIG.viewableExtensions.includes(extension?.toLowerCase());
};

// Common button styles following the existing pattern
const getButtonStyles = (theme: any, colorType: 'primary' | 'success' = 'primary') => ({
  color:
    theme.palette.mode === 'dark' ? theme.palette[colorType].light : theme.palette[colorType].main,
  textTransform: 'none' as const,
  fontWeight: 500,
  fontSize: '11px',
  fontFamily:
    '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  py: 0.5,
  px: 1.5,
  borderRadius: 2,
  borderColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette[colorType].main, 0.3)
      : alpha(theme.palette[colorType].main, 0.25),
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette[colorType].main, 0.08)
      : alpha(theme.palette[colorType].main, 0.05),
  '&:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette[colorType].main, 0.15)
        : alpha(theme.palette[colorType].main, 0.1),
    borderColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette[colorType].main, 0.5)
        : alpha(theme.palette[colorType].main, 0.4),
    transform: 'translateY(-1px)',
  },
});

// Clean file card with optimal UX and appealing design
const FileCard = React.memo(
  ({
    file,
    theme,
    onViewDocument,
    onViewCitations,
    onViewRecord,
    connectorData,
  }: {
    file: FileInfo;
    theme: any;
    onViewDocument: (file: FileInfo) => void;
    onViewCitations: (file: FileInfo) => void;
    onViewRecord: (file: FileInfo) => void;
    connectorData: { [key: string]: { iconPath: string; color?: string } };
  }) => {
    // Get connector info from dynamic data
    const connectorInfo = connectorData[file.connector?.toUpperCase()] || {
      iconPath: '/assets/icons/connectors/default.svg',
    };

    return (
      <Paper
        elevation={0}
        sx={{
          p: 1.25,
          mb: 0.75,
          bgcolor:
            theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.02)',
          borderRadius: 2,
          border: '1px solid',
          borderColor:
            theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)',
          fontFamily:
            '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          transition: 'all 0.2s ease',
          cursor: 'pointer',
          '&:hover': {
            borderColor:
              theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.12)',
            backgroundColor:
              theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.03)',
          },
        }}
        onClick={() => {
          if (file.extension) {
            onViewCitations(file);
          }
        }}
      >
        <Box
          sx={{
            pl: 1,
            borderLeft: `3px solid ${theme.palette.success.main}`,
            borderRadius: '2px',
          }}
        >
          {/* Main Content Area */}
          <Stack direction="row" spacing={1.5} alignItems="flex-start">
            {/* File Icon */}
            <Box
              sx={{
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                mt: 0.125,
              }}
            >
              <Icon
                icon={getFileIcon(file.extension)}
                width={40}
                height={40}
                style={{ borderRadius: '4px', color: theme.palette.primary.main }}
              />
            </Box>

            {/* File Information - Takes most space */}
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography
                variant="body2"
                sx={{
                  fontSize: '13px',
                  fontWeight: 600,
                  color: 'text.primary',
                  mb: 0.5,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  lineHeight: 1.4,
                }}
                title={file.recordName}
              >
                {file.recordName}
              </Typography>

              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                <Typography
                  variant="caption"
                  sx={{
                    color: 'text.secondary',
                    fontSize: '12px',
                    fontWeight: 500,
                    textTransform: 'uppercase',
                    letterSpacing: '0.3px',
                  }}
                >
                  {file.extension}
                </Typography>

                {file.citationCount > 1 && (
                  <>
                    <Box
                      sx={{
                        width: 3,
                        height: 3,
                        borderRadius: '50%',
                        bgcolor: 'text.secondary',
                        opacity: 0.5,
                      }}
                    />
                    <Typography
                      variant="caption"
                      sx={{
                        color: 'text.secondary',
                        fontSize: '12px',
                        fontWeight: 400,
                      }}
                    >
                      {file.citationCount} citations
                    </Typography>
                  </>
                )}
              </Stack>
            </Box>

            {/* Connector Icon */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}>
              {file.webUrl && !file.citation?.metadata?.hideWeburl && (
                <Box
                  sx={{ cursor: 'pointer', alignItems: 'center', display: 'flex', gap: 0.5 }}
                  onClick={(e) => {
                    e.stopPropagation();
                    window.open(file.webUrl, '_blank', 'noopener,noreferrer');
                  }}
                >
                  <Icon icon={linkIcon} width={14} height={14} />
                  <img
                    src={connectorInfo.iconPath}
                    alt={file.connector || 'UPLOAD'}
                    width={16}
                    height={16}
                    style={{
                      objectFit: 'contain',
                      borderRadius: '2px',
                    }}
                    onError={(e) => {
                      e.currentTarget.src = '/assets/icons/connectors/default.svg';
                    }}
                  />
                  <Typography
                    variant="caption"
                    sx={{
                      color: 'text.secondary',
                      fontSize: '11px',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.3px',
                    }}
                  >
                    {file.connector || 'UPLOAD'}
                  </Typography>
                </Box>
              )}
              {(!file.webUrl || file.citation?.metadata?.hideWeburl) && (
                <Box sx={{ alignItems: 'center', display: 'flex', gap: 0.5 }}>
                  <img
                    src={connectorInfo.iconPath}
                    alt={file.connector || 'UPLOAD'}
                    width={16}
                    height={16}
                    style={{
                      objectFit: 'contain',
                      borderRadius: '2px',
                    }}
                    onError={(e) => {
                      e.currentTarget.src = '/assets/icons/connectors/default.svg';
                    }}
                  />
                  <Typography
                    variant="caption"
                    sx={{
                      color: 'text.secondary',
                      fontSize: '11px',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.3px',
                    }}
                  >
                    {file.connector || 'UPLOAD'}
                  </Typography>
                </Box>
              )}
            </Box>
          </Stack>

          {/* Action Buttons - Moved below and made responsive */}
          <Box
            sx={{
              mt: 0.75,
              display: 'flex',
              flexWrap: 'wrap',
              gap: 0.75,
              justifyContent: 'flex-end',
            }}
          >
            {file.extension && isDocViewable(file.extension) && file.citation?.metadata?.previewRenderable !== false && (
              <Button
                size="small"
                variant="text"
                startIcon={<Icon icon={eyeIcon} width={14} height={14} />}
                onClick={(e) => {
                  e.stopPropagation();
                  onViewCitations(file);
                }}
                sx={{
                  textTransform: 'none',
                  fontSize: '11px',
                  fontWeight: 500,
                  borderRadius: 1.5,
                  px: 1.25,
                  py: 0.375,
                  minHeight: 26,
                }}
              >
                View Citations
              </Button>
            )}

            <Button
              size="small"
              variant="text"
              startIcon={<Icon icon={fileDocIcon} width={14} height={14} />}
              onClick={(e) => {
                e.stopPropagation();
                onViewRecord(file);
              }}
              sx={{
                textTransform: 'none',
                fontSize: '11px',
                fontWeight: 500,
                borderRadius: 1.5,
                px: 1.25,
                py: 0.375,
                minHeight: 26,
              }}
            >
              Details
            </Button>
          </Box>
        </Box>
      </Paper>
    );
  }
);

FileCard.displayName = 'FileCard';

const SourcesAndCitations: React.FC<SourcesAndCitationsProps> = ({
  citations,
  aggregatedCitations,
  onRecordClick,
  onViewPdf,
  className,
  modelInfo,
}) => {
  const theme = useTheme();
  const [isCitationsExpanded, setIsCitationsExpanded] = useState(false);
  const [expandedRecords, setExpandedRecords] = useState<Set<string>>(new Set());

  // Get connector data from the hook
  const { activeConnectors } = useConnectors();

  // Create connector data map for easy lookup
  const connectorData = useMemo(() => {
    const allConnectors = [...activeConnectors];
    const data: { [key: string]: { iconPath: string; color?: string } } = {};
    allConnectors.forEach((connector) => {
      data[connector.name.toUpperCase()] = {
        iconPath: connector.iconPath || '/assets/icons/connectors/default.svg',
      };
    });

    // Add UPLOAD connector for local files
    data.UPLOAD = {
      iconPath: '/assets/icons/connectors/kb.svg',
    };

    return data;
  }, [activeConnectors]);

  // Group citations by recordId to get unique files
  const uniqueFiles = useMemo((): FileInfo[] => {
    const fileMap = new Map<string, FileInfo>();

    citations.forEach((citation) => {
      const recordId = citation.metadata?.recordId;
      if (recordId && !fileMap.has(recordId)) {
        fileMap.set(recordId, {
          recordId,
          recordName: citation.metadata?.recordName || 'Unknown Document',
          extension: citation.metadata?.extension,
          webUrl: citation.metadata?.webUrl,
          citationCount: aggregatedCitations[recordId]?.length || 1,
          citation,
          connector: citation.metadata?.connector,
        });
      }
    });

    return Array.from(fileMap.values());
  }, [citations, aggregatedCitations]);

  // Group citations by recordId for accordion display
  const citationsByRecord = useMemo(() => {
    const grouped: { [recordId: string]: CustomCitation[] } = {};

    citations.forEach((citation) => {
      const recordId = citation.metadata?.recordId || 'unknown';
      if (!grouped[recordId]) {
        grouped[recordId] = [];
      }
      grouped[recordId].push(citation);
    });

    return grouped;
  }, [citations]);

  // Get record info for each recordId
  const getRecordInfo = useCallback(
    (recordId: string) => {
      const citation = citations.find((c) => c.metadata?.recordId === recordId);
      if (!citation) return null;

      return {
        recordId,
        recordName: citation.metadata?.recordName || 'Unknown Document',
        extension: citation.metadata?.extension,
        connector: citation.metadata?.connector,
        citationCount: citationsByRecord[recordId]?.length || 0,
      };
    },
    [citations, citationsByRecord]
  );

  const handleAccordionChange = useCallback((recordId: string) => {
    setExpandedRecords((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(recordId)) {
        newSet.delete(recordId);
      } else {
        newSet.add(recordId);
      }
      return newSet;
    });
  }, []);


  const handleViewDocument = useCallback((file: FileInfo) => {
    // Check if previewRenderable is false - if so, open webUrl instead of viewer
    if (file.citation?.metadata?.previewRenderable === false) {
      const webUrl = getWebUrlWithFragment(file.citation);
      if (webUrl) {
        window.open(webUrl, '_blank', 'noopener,noreferrer');
      }
      return;
    }
    if (file.webUrl) {
      window.open(file.webUrl, '_blank', 'noopener,noreferrer');
    }
  }, []);

  const handleViewCitations = useCallback(
    (file: FileInfo) => {
      // Check if previewRenderable is false - if so, open webUrl instead of viewer
      if (file.citation?.metadata?.previewRenderable === false) {
        const webUrl = getWebUrlWithFragment(file.citation);
        if (webUrl) {
          window.open(webUrl, '_blank', 'noopener,noreferrer');
        }
        return;
      }
      const recordCitations = aggregatedCitations[file.recordId] || [file.citation];
      onViewPdf('', file.citation, recordCitations, false);
    },
    [aggregatedCitations, onViewPdf]
  );

  const handleViewRecord = useCallback(
    (file: FileInfo) => {
      if (file.extension) {
        onRecordClick({
          recordId: file.recordId,
          citations: aggregatedCitations[file.recordId] || [],
        });
      }
    },
    [onRecordClick, aggregatedCitations]
  );

  const handleViewCitationsFromList = useCallback(
    async (recordId: string): Promise<void> =>
      new Promise<void>((resolve) => {
        const recordCitations = aggregatedCitations[recordId] || [];
        if (recordCitations.length > 0) {
          const citation = recordCitations[0];
          // Check if previewRenderable is false - if so, open webUrl instead of viewer
          if (citation?.metadata?.previewRenderable === false) {
            const webUrl = getWebUrlWithFragment(citation);
            if (webUrl) {
              window.open(webUrl, '_blank', 'noopener,noreferrer');
            }
            resolve();
            return;
          }
          onViewPdf('', citation, recordCitations, false);
          resolve();
        }
      }),
    [aggregatedCitations, onViewPdf]
  );

  // Don't render if no citations
  if (!citations || citations.length === 0) {
    return modelInfo && modelInfo.modelName ? (
      <Box
        sx={{
          my: 2,
          display: 'flex',
          gap: 0.75,
          flexDirection: 'row',
          justifyContent: 'flex-end',
        }}
      >
        <Typography
          variant="body2"
          sx={{
            color: 'text.primary',
            fontSize: '13px',
            fontWeight: 600,
            fontFamily:
              '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          }}
        >
          <Typography
            variant="caption"
            sx={{
              color: 'text.secondary',
              fontSize: '12px',
              fontWeight: 500,
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
              mr: 0.5,
            }}
          >
            Model:
          </Typography>
          {modelInfo.modelName}
          {modelInfo.chatMode && (
            <Box
              component="span"
              sx={{
                ml: 0.75,
                px: 0.75,
                py: 0.25,
                borderRadius: 1,
                bgcolor: (t) =>
                  t.palette.mode === 'dark'
                    ? alpha(t.palette.primary.main, 0.15)
                    : alpha(t.palette.primary.main, 0.1),
                color: (t) =>
                  t.palette.mode === 'dark' ? t.palette.primary.light : t.palette.primary.main,
                fontSize: '11px',
                fontWeight: 500,
                textTransform: 'capitalize',
              }}
            >
              {modelInfo.chatMode}
            </Box>
          )}
        </Typography>
      </Box>
    ) : null;
  }

  return (
    <Box className={className} sx={{ mt: 2.5 }}>
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          mb: 2,
        }}
      >
        {/* Citations Button */}
        <Button
          variant="outlined"
          size="small"
          onClick={() => setIsCitationsExpanded(!isCitationsExpanded)}
          startIcon={
            <Icon icon={isCitationsExpanded ? downIcon : rightIcon} width={14} height={14} />
          }
          sx={{
            ...getButtonStyles(theme, 'primary'),
            minWidth: 'auto',
            '& .MuiButton-startIcon': {
              marginRight: 0.75,
            },
          }}
        >
          {citations.length} {citations.length === 1 ? 'Citation' : 'Citations'}
          {Object.keys(citationsByRecord).length > 0 && (
            <Typography
              component="span"
              sx={{
                ml: 1,
                px: 0.75,
                py: 0.25,
                borderRadius: 1,
                bgcolor: (t) =>
                  t.palette.mode === 'dark'
                    ? alpha(t.palette.primary.main, 0.2)
                    : alpha(t.palette.primary.main, 0.1),
                color: (t) =>
                  t.palette.mode === 'dark' ? t.palette.primary.light : t.palette.primary.main,
                fontSize: '10px',
                fontWeight: 600,
              }}
            >
              {Object.keys(citationsByRecord).length}{' '}
              {Object.keys(citationsByRecord).length === 1 ? 'Source' : 'Sources'}
            </Typography>
          )}
        </Button>

        {/* Model Name Display */}
        {modelInfo?.modelName && (
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'row',
              gap: 0.75,
              alignItems: 'center',
            }}
          >
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                fontSize: '11px',
                fontWeight: 500,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              Model:
            </Typography>
            <Typography
              variant="body2"
              sx={{
                color: 'text.primary',
                fontSize: '12px',
                fontWeight: 600,
                fontFamily:
                  '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
              }}
            >
              {modelInfo.modelName}
              {modelInfo.chatMode && (
                <Box
                  component="span"
                  sx={{
                    ml: 0.75,
                    px: 0.75,
                    py: 0.25,
                    borderRadius: 1,
                    bgcolor: (t) =>
                      t.palette.mode === 'dark'
                        ? alpha(t.palette.primary.main, 0.15)
                        : alpha(t.palette.primary.main, 0.1),
                    color: (t) =>
                      t.palette.mode === 'dark' ? t.palette.primary.light : t.palette.primary.main,
                    fontSize: '10px',
                    fontWeight: 500,
                    textTransform: 'capitalize',
                  }}
                >
                  {modelInfo.chatMode}
                </Box>
              )}
            </Typography>
          </Box>
        )}
      </Box>

      {/* Citations Section - Grouped by Record */}
      <Collapse in={isCitationsExpanded}>
        <Box sx={{ mb: 2 }}>
          {Object.keys(citationsByRecord).map((recordId) => {
            const recordCitations = citationsByRecord[recordId];
            const recordInfo = getRecordInfo(recordId);
            const isExpanded = expandedRecords.has(recordId);

            if (!recordInfo) return null;

            return (
              <Accordion
                key={recordId}
                expanded={isExpanded}
                onChange={() => handleAccordionChange(recordId)}
                sx={{
                  mb: 1.5,
                  bgcolor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.03)'
                      : 'rgba(0, 0, 0, 0.02)',
                  borderRadius: 2,
                  border: '1px solid',
                  borderColor:
                    theme.palette.mode === 'dark'
                      ? 'rgba(255, 255, 255, 0.08)'
                      : 'rgba(0, 0, 0, 0.08)',
                  boxShadow: 'none',
                  '&:before': {
                    display: 'none',
                  },
                  '&:hover': {
                    borderColor:
                      theme.palette.mode === 'dark'
                        ? 'rgba(255, 255, 255, 0.12)'
                        : 'rgba(0, 0, 0, 0.12)',
                    backgroundColor:
                      theme.palette.mode === 'dark'
                        ? 'rgba(255, 255, 255, 0.05)'
                        : 'rgba(0, 0, 0, 0.03)',
                  },
                  transition: 'all 0.2s ease',
                }}
              >
                <AccordionSummary
                  expandIcon={<Icon icon={isExpanded ? upIcon : downIcon} width={20} height={20} />}
                  sx={{
                    px: 2,
                    py: 1.5,
                    minHeight: 56,
                    overflow: 'hidden',
                    '& .MuiAccordionSummary-content': {
                      my: 1,
                      alignItems: 'center',
                      overflow: 'hidden',
                      minWidth: 0,
                      margin: 0,
                    },
                    '&:hover': {
                      backgroundColor: 'transparent',
                    },
                  }}
                >
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      flex: 1,
                      minWidth: 0,
                      overflow: 'hidden',
                      gap: 1.5,
                    }}
                  >
                    <Icon
                      icon={getFileIcon(recordInfo.extension)}
                      width={32}
                      height={32}
                      style={{
                        color:
                          FILE_CONFIG.colors[
                            recordInfo.extension?.toLowerCase() as keyof typeof FILE_CONFIG.colors
                          ] || theme.palette.primary.main,
                        flexShrink: 0,
                      }}
                    />
                    <Box sx={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
                      <Box sx={{  minWidth: 0 }}>
                        <Typography
                          sx={{
                            fontSize: '13px',
                            fontWeight: 600,
                            color: 'text.primary',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            fontFamily:
                              '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                          }}
                          title={recordInfo.recordName}
                        >
                          {recordInfo.recordName}
                        </Typography>
                        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.25 }}>
                          {recordInfo.extension && (
                            <Typography
                              variant="caption"
                              sx={{
                                color: 'text.secondary',
                                fontSize: '11px',
                                fontWeight: 500,
                                textTransform: 'uppercase',
                                letterSpacing: '0.3px',
                              }}
                            >
                              {recordInfo.extension}
                            </Typography>
                          )}
                          {recordCitations.length > 1 && (
                            <>
                              <Box
                                sx={{
                                  width: 3,
                                  height: 3,
                                  borderRadius: '50%',
                                  bgcolor: 'text.secondary',
                                  opacity: 0.5,
                                }}
                              />
                              <Typography
                                variant="caption"
                                sx={{
                                  color: 'text.secondary',
                                  fontSize: '11px',
                                  fontWeight: 500,
                                }}
                              >
                                {recordCitations.length} citations
                              </Typography>
                            </>
                          )}
                        </Stack>
                      </Box>
                      {/* Action Buttons - Outside expandIcon to prevent flipping */}
                      <Stack
                        direction="row"
                        spacing={0.5}
                        alignItems="center"
                        sx={{ flexShrink: 0, mt:1, mr:2}}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {recordCitations[0]?.metadata?.webUrl && !recordCitations[0]?.metadata?.hideWeburl ? (
                          <Link
                            href={
                              (() => {
                                const firstCitation = recordCitations[0];
                                if (firstCitation?.metadata?.previewRenderable === false) {
                                  return getWebUrlWithFragment(firstCitation) || firstCitation?.metadata?.webUrl || '#';
                                }
                                return firstCitation?.metadata?.webUrl || '#';
                              })()
                            }
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => {
                              e.stopPropagation();
                              const firstCitation = recordCitations[0];
                              // If there's a webUrl, let the link handle navigation naturally
                              if (firstCitation?.metadata?.webUrl || 
                                  (firstCitation?.metadata?.previewRenderable === false && getWebUrlWithFragment(firstCitation))) {
                                // Link will navigate naturally via href
                                return;
                              }
                              // For files without webUrl (like UPLOAD), prevent default and open the document viewer
                              if (
                                recordInfo.extension &&
                                recordInfo.recordId &&
                                firstCitation
                              ) {
                                e.preventDefault();
                                const allRecordCitations =
                                  aggregatedCitations[recordInfo.recordId] || recordCitations;
                                const isExcelOrCSV = ['csv', 'xlsx', 'xls'].includes(
                                  recordInfo.extension || ''
                                );
                                onViewPdf('', firstCitation, allRecordCitations, isExcelOrCSV);
                              } else {
                                // No webUrl and no viewer option, prevent default navigation
                                e.preventDefault();
                              }
                            }}
                            sx={{
                              textTransform: 'none',
                              fontSize: '11px',
                              fontWeight: 500,
                              color: 'text.secondary',
                              textDecoration: 'none',
                              px: { xs: 0.5, sm: 1 },
                              py: 0.5,
                              minHeight: 28,
                              minWidth: 'auto',
                              display: 'flex',
                              alignItems: 'center',
                              gap: 0.5,
                              borderRadius: 1,
                              '&:hover': {
                                color: 'primary.main',
                                bgcolor: (t) =>
                                  t.palette.mode === 'dark'
                                    ? alpha(t.palette.primary.main, 0.1)
                                    : alpha(t.palette.primary.main, 0.05),
                              },
                            }}
                          >
                            <Icon
                              icon={recordCitations[0]?.metadata?.webUrl ? linkIcon : eyeIcon}
                              width={14}
                              height={14}
                            />
                            <img
                              src={
                                connectorData[(recordInfo.connector || 'UPLOAD')?.toUpperCase()]
                                  ?.iconPath || '/assets/icons/connectors/default.svg'
                              }
                              alt={recordInfo.connector || 'UPLOAD'}
                              width={16}
                              height={16}
                              style={{
                                objectFit: 'contain',
                                borderRadius: '2px',
                                flexShrink: 0,
                              }}
                              onError={(e) => {
                                e.currentTarget.src = '/assets/icons/connectors/default.svg';
                              }}
                            />
                            <Typography
                              component="span"
                              sx={{
                                fontSize: '11px',
                                fontWeight: 500,
                                textTransform: 'uppercase',
                                letterSpacing: '0.3px',
                                display: { xs: 'none', md: 'inline' },
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {recordInfo.connector || 'KB'}
                            </Typography>
                          </Link>
                        ) : (
                          <Box
                            sx={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 0.5,
                              px: { xs: 0.5, sm: 1 },
                              py: 0.5,
                            }}
                          >
                            <img
                              src={
                                connectorData[(recordInfo.connector || 'UPLOAD')?.toUpperCase()]
                                  ?.iconPath || '/assets/icons/connectors/default.svg'
                              }
                              alt={recordInfo.connector || 'UPLOAD'}
                              width={16}
                              height={16}
                              style={{
                                objectFit: 'contain',
                                borderRadius: '2px',
                                flexShrink: 0,
                              }}
                              onError={(e) => {
                                e.currentTarget.src = '/assets/icons/connectors/default.svg';
                              }}
                            />
                            <Typography
                              component="span"
                              sx={{
                                fontSize: '11px',
                                fontWeight: 500,
                                textTransform: 'uppercase',
                                letterSpacing: '0.3px',
                                display: { xs: 'none', md: 'inline' },
                                whiteSpace: 'nowrap',
                                color: 'text.secondary',
                              }}
                            >
                              {recordInfo.connector || 'KB'}
                            </Typography>
                          </Box>
                        )}
                        {recordInfo.extension && (
                          <Button
                            size="small"
                            variant="text"
                            startIcon={<Icon icon={fileDocIcon} width={14} height={14} />}
                            onClick={(e) => {
                              e.stopPropagation();
                              if (recordInfo.recordId) {
                                onRecordClick({
                                  recordId: recordInfo.recordId,
                                  citations: aggregatedCitations[recordInfo.recordId] || [],
                                });
                              }
                            }}
                            sx={{
                              textTransform: 'none',
                              fontSize: '10px',
                              fontWeight: 500,
                              color: 'text.secondary',
                              px: { xs: 0.5, sm: 1 },
                              py: 0.5,
                              minHeight: 28,
                              minWidth: 'auto',
                              '&:hover': {
                                color: 'primary.main',
                                bgcolor: (t) =>
                                  t.palette.mode === 'dark'
                                    ? alpha(t.palette.primary.main, 0.1)
                                    : alpha(t.palette.primary.main, 0.05),
                              },
                            }}
                          >
                            <Typography
                              component="span"
                              sx={{
                                display: { xs: 'none', sm: 'inline' },
                                fontSize: '13px',
                              }}
                            >
                              Details
                            </Typography>
                          </Button>
                        )}
                      </Stack>
                    </Box>
                  </Box>
                </AccordionSummary>
                <AccordionDetails sx={{ p: 0, pb: 1.5 }}>
                  <Box sx={{ px: 2 }}>
                    {recordCitations.map((citation) => (
                      <Box
                        key={citation._id}
                        onClick={(e) => {
                          e.stopPropagation();
                          // Check if previewRenderable is false - if so, open webUrl instead of viewer
                          if (citation.metadata?.previewRenderable === false) {
                            const webUrl = getWebUrlWithFragment(citation);
                            if (webUrl) {
                              window.open(webUrl, '_blank', 'noopener,noreferrer');
                            }
                            return;
                          }
                          if (!citation.metadata?.extension && citation.metadata?.webUrl) {
                            const webUrl = getWebUrlWithFragment(citation);
                            if (webUrl) {
                              window.open(webUrl, '_blank', 'noopener,noreferrer');
                            }
                            return;
                          }
                          if (citation.metadata?.recordId) {
                            const allRecordCitations = aggregatedCitations[
                              citation.metadata.recordId
                            ] || [citation];
                            const isExcelOrCSV = ['csv', 'xlsx', 'xls'].includes(
                              citation.metadata?.extension || ''
                            );
                            onViewPdf('', citation, allRecordCitations, isExcelOrCSV);
                          }
                        }}
                        sx={{
                          p: 1.5,
                          mb: 1.25,
                          cursor: 'pointer',
                          borderRadius: 1.5,
                          border: `1px solid ${
                            theme.palette.mode === 'dark'
                              ? 'rgba(255, 255, 255, 0.06)'
                              : 'rgba(0, 0, 0, 0.06)'
                          }`,
                          bgcolor:
                            theme.palette.mode === 'dark'
                              ? 'rgba(255, 255, 255, 0.02)'
                              : 'rgba(0, 0, 0, 0.01)',
                          transition: 'all 0.15s ease',
                          position: 'relative',
                          '&:hover': {
                            bgcolor:
                              theme.palette.mode === 'dark'
                                ? 'rgba(255, 255, 255, 0.04)'
                                : 'rgba(0, 0, 0, 0.02)',
                            borderColor:
                              theme.palette.mode === 'dark'
                                ? 'rgba(255, 255, 255, 0.12)'
                                : 'rgba(0, 0, 0, 0.12)',
                          },
                          '&:last-child': {
                            mb: 0,
                          },
                        }}
                      >
                        <Stack direction="row" spacing={1.5} alignItems="flex-start">
                          <Box
                            sx={{
                              minWidth: 24,
                              height: 24,
                              borderRadius: '50%',
                              bgcolor: theme.palette.primary.main,
                              color: 'white',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              fontSize: '11px',
                              fontWeight: 600,
                              flexShrink: 0,
                              mt: 0.125,
                            }}
                          >
                            {citation.chunkIndex}
                          </Box>
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography
                              sx={{
                                fontSize: '13px',
                                lineHeight: 1.6,
                                color:
                                  theme.palette.mode === 'dark'
                                    ? 'rgba(255, 255, 255, 0.85)'
                                    : 'rgba(0, 0, 0, 0.75)',
                                fontStyle: 'normal',
                                fontWeight: 400,
                                fontFamily:
                                  '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                                display: '-webkit-box',
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: 'vertical',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                mb: 0.5,
                              }}
                              title={
                                citation.metadata?.blockText &&
                                citation.metadata?.extension === 'pdf' &&
                                typeof citation.metadata?.blockText === 'string' &&
                                citation.metadata?.blockText.length > 0
                                  ? citation.metadata?.blockText
                                  : citation.content
                              }
                            >
                              {citation.metadata?.blockText &&
                              citation.metadata?.extension === 'pdf' &&
                              typeof citation.metadata?.blockText === 'string' &&
                              citation.metadata?.blockText.length > 0
                                ? citation.metadata?.blockText
                                : citation.content}
                            </Typography>
                            <Stack direction="row" spacing={0.75} alignItems="center">
                              <Icon
                                icon={eyeIcon}
                                width={12}
                                height={12}
                                style={{
                                  color: theme.palette.primary.main,
                                  opacity: 0.7,
                                }}
                              />
                              <Typography
                                variant="caption"
                                sx={{
                                  color: 'primary.main',
                                  fontSize: '11px',
                                  fontWeight: 500,
                                  opacity: 0.8,
                                }}
                              >
                                Click to view in document
                              </Typography>
                            </Stack>
                          </Box>
                        </Stack>
                      </Box>
                    ))}
                  </Box>
                </AccordionDetails>
              </Accordion>
            );
          })}
        </Box>
      </Collapse>

      {/* Minimal Hide Controls */}
      {isCitationsExpanded && (
        <Box sx={{ mt: 1.5, display: 'flex', justifyContent: 'center' }}>
          <Button
            variant="text"
            size="small"
            onClick={() => setIsCitationsExpanded(false)}
            startIcon={<Icon icon={upIcon} width={14} height={14} />}
            sx={{
              ...getButtonStyles(theme, 'primary'),
              minWidth: 'auto',
              '& .MuiButton-startIcon': {
                marginRight: 0.75,
              },
            }}
          >
            Hide Citations
          </Button>
        </Box>
      )}
    </Box>
  );
};

SourcesAndCitations.displayName = 'SourcesAndCitations';

export default SourcesAndCitations;
