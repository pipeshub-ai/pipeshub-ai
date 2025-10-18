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

import { Box, Paper, Stack, Button, Collapse, Typography, alpha, useTheme } from '@mui/material';

import type { CustomCitation } from 'src/types/chat-bot';
import type { Record } from 'src/types/chat-message';
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
            <Box
              sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}
            >
              <Box sx={{ cursor: 'pointer', alignItems: 'center', display: 'flex', gap: 0.5 }} onClick={(e) => {
                  e.stopPropagation();
                  window.open(file.webUrl, '_blank', 'noopener,noreferrer');
                }}>
              <Icon
                icon={linkIcon}
                width={14}
                height={14}
              />
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
            {file.extension && isDocViewable(file.extension) && (
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
}) => {
  const theme = useTheme();
  const [isFilesExpanded, setIsFilesExpanded] = useState(false);
  const [isCitationsExpanded, setIsCitationsExpanded] = useState(false);

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

  const handleViewDocument = useCallback((file: FileInfo) => {
    if (file.webUrl) {
      window.open(file.webUrl, '_blank', 'noopener,noreferrer');
    }
  }, []);

  const handleViewCitations = useCallback(
    (file: FileInfo) => {
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
          onViewPdf('', citation, recordCitations, false);
          resolve();
        }
      }),
    [aggregatedCitations, onViewPdf]
  );

  // Don't render if no citations
  if (!citations || citations.length === 0) {
    return null;
  }

  return (
    <Box className={className} sx={{ mt: 2.5 }}>
      {/* Compact Side by Side Buttons */}
      <Stack direction="row" spacing={1.5} sx={{ mb: 2 }}>
        {/* Source Files Button */}
        {uniqueFiles.length > 0 && (
          <Button
            variant="outlined"
            size="small"
            onClick={() => setIsFilesExpanded(!isFilesExpanded)}
            startIcon={
              <Icon icon={isFilesExpanded ? downIcon : rightIcon} width={14} height={14} />
            }
            sx={{
              ...getButtonStyles(theme, 'success'),
              minWidth: 'auto',
              '& .MuiButton-startIcon': {
                marginRight: 0.75,
              },
            }}
          >
            <Icon icon={folderIcon} width={14} height={14} style={{ marginRight: 6 }} />
            {uniqueFiles.length} {uniqueFiles.length === 1 ? 'Source' : 'Sources'}
          </Button>
        )}

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
        </Button>
      </Stack>

      {/* File Sources Section */}
      {uniqueFiles.length > 0 && (
        <Collapse in={isFilesExpanded}>
          <Box sx={{ mb: 2 }}>
            {uniqueFiles.map((file) => (
              <FileCard
                key={file.recordId}
                file={file}
                theme={theme}
                connectorData={connectorData}
                onViewDocument={handleViewDocument}
                onViewCitations={handleViewCitations}
                onViewRecord={handleViewRecord}
              />
            ))}
          </Box>
        </Collapse>
      )}

      {/* Citations Section */}
      <Collapse in={isCitationsExpanded}>
        <Box sx={{ mb: 2 }}>
          {citations.map((citation, cidx) => (
            <Paper
              key={cidx}
              elevation={0}
              sx={{
                p: 2,
                mb: 1,
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
                fontFamily:
                  '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                transition: 'all 0.2s ease',
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
              }}
            >
              <Box
                sx={{
                  pl: 1.5,
                  borderLeft: `3px solid ${theme.palette.primary.main}`,
                  borderRadius: '2px',
                }}
              >
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
                    mb: 1.5,
                    fontFamily:
                      '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                  }}
                >
                  {citation.metadata?.blockText &&
                  citation.metadata?.extension === 'pdf' &&
                  typeof citation.metadata?.blockText === 'string' &&
                  citation.metadata?.blockText.length > 0
                    ? citation.metadata?.blockText
                    : citation.content}
                </Typography>

                {citation.metadata?.recordId && (
                  <Stack direction="row" spacing={1} justifyContent="flex-end">
                    {citation.metadata.extension && isDocViewable(citation.metadata.extension) && (
                      <Button
                        size="small"
                        variant="text"
                        startIcon={<Icon icon={eyeIcon} width={14} height={14} />}
                        onClick={() => handleViewCitationsFromList(citation.metadata?.recordId)}
                        sx={{
                          textTransform: 'none',
                          fontSize: '12px',
                          fontWeight: 500,
                          borderRadius: 1.5,
                          px: 1.5,
                          py: 0.5,
                          minHeight: 28,
                        }}
                      >
                        View
                      </Button>
                    )}
                    <Button
                      size="small"
                      variant="text"
                      startIcon={<Icon icon={fileDocIcon} width={14} height={14} />}
                      onClick={() => {
                        if (citation.metadata?.recordId) {
                          onRecordClick({
                            ...citation.metadata,
                            citations: [],
                          });
                        }
                      }}
                      sx={{
                        textTransform: 'none',
                        fontSize: '12px',
                        fontWeight: 500,
                        borderRadius: 1.5,
                        px: 1.5,
                        py: 0.5,
                        minHeight: 28,
                      }}
                    >
                      Details
                    </Button>
                  </Stack>
                )}
              </Box>
            </Paper>
          ))}
        </Box>
      </Collapse>

      {/* Minimal Hide Controls */}
      {(isFilesExpanded || isCitationsExpanded) && (
        <Stack direction="row" spacing={1} sx={{ mt: 1.5, justifyContent: 'center' }}>
          {isFilesExpanded && (
            <Button
              variant="text"
              size="small"
              onClick={() => setIsFilesExpanded(false)}
              startIcon={<Icon icon={upIcon} width={14} height={14} />}
              sx={{
                ...getButtonStyles(theme, 'success'),
                minWidth: 'auto',
                '& .MuiButton-startIcon': {
                  marginRight: 0.75,
                },
              }}
            >
              Hide Sources
            </Button>
          )}

          {isCitationsExpanded && (
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
              Hide citations
            </Button>
          )}
        </Stack>
      )}
    </Box>
  );
};

SourcesAndCitations.displayName = 'SourcesAndCitations';

export default SourcesAndCitations;
