import type { Record } from 'src/types/chat-message';
import type { CustomCitation } from 'src/types/chat-bot';

import React from 'react';
import { Icon } from '@iconify/react';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import linkIcon from '@iconify-icons/mdi/open-in-new';
import fileDocumentIcon from '@iconify-icons/mdi/file-document-outline';

import {
  Box,
  Fade,
  Card,
  Chip,
  Stack,
  alpha,
  Button,
  styled,
  Tooltip,
  useTheme,
  Typography,
  IconButton,
} from '@mui/material';

import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Styled components for consistent design
const StyledCard = styled(Card)(({ theme }) => ({
  maxHeight: '320px',
  padding: theme.spacing(2),
  marginTop: theme.spacing(0.5),
  boxShadow:
    theme.palette.mode === 'dark'
      ? '8px 8px 24px rgba(f, f, f, 0.8)'
      : '0 8px 24px rgba(0, 0, 0, 0.08)',
  borderRadius: theme.shape.borderRadius,
  border:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.common.white, 0.3)}`
      : `1px solid ${alpha(theme.palette.common.black, 0.2)}`,
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.default, 1)
      : theme.palette.background.paper,
  overflow: 'auto',
  position: 'relative',
  transformOrigin: 'top left',
  transition: 'all 0.15s ease-in-out',
}));

const DocumentTitle = styled(Typography)(({ theme }) => ({
  fontSize: '0.875rem',
  lineHeight: 1.4,
  fontWeight: 600,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.75),
  maxWidth: 'calc(100% - 80px)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  color: theme.palette.text.primary,
}));

const StyledChip = styled(Chip)(({ theme }) => ({
  height: '20px',
  fontSize: '0.7rem',
  fontWeight: 500,
  borderRadius: '4px',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.common.white, 0.75)
      : alpha(theme.palette.common.black, 0.1),
  color: theme.palette.mode === 'dark' ? theme.palette.primary.lighter : theme.palette.common.black,
  border:
    theme.palette.mode === 'dark' ? `1px solid ${alpha(theme.palette.primary.dark, 0.2)}` : 'none',
  '& .MuiChip-label': {
    padding: theme.spacing(0, 0.75),
  },
}));

const MetaChip = styled(Chip)(({ theme }) => ({
  height: '20px',
  fontSize: '0.7rem',
  fontWeight: 500,
  borderRadius: '4px',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.grey[100], 0.5)
      : alpha(theme.palette.grey[400], 0.5),
  color:
    theme.palette.mode === 'dark' ? alpha(theme.palette.grey[300], 0.7) : theme.palette.grey[900],
  border:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.grey[700], 0.3)}`
      : `1px solid ${alpha(theme.palette.grey[300], 1)}`,
  '& .MuiChip-label': {
    padding: theme.spacing(0, 0.75),
  },
}));

const ActionButton = styled(Button)(({ theme }) => ({
  padding: theme.spacing(0.5, 1),
  minWidth: '64px',
  height: '28px',
  borderRadius: theme.shape.borderRadius,
  textTransform: 'none',
  fontSize: '0.75rem',
  fontWeight: 500,
  marginLeft: theme.spacing(1),
  flexShrink: 0,
  boxShadow: 'none',
  '&.MuiButton-contained': {
    boxShadow: 'none',
    backgroundColor: theme.palette.primary.main,
    color: theme.palette.primary.contrastText,
    '&:hover': {
      backgroundColor: theme.palette.primary.dark,
      boxShadow:
        theme.palette.mode === 'dark'
          ? '0 4px 8px rgba(0, 0, 0, 0.3)'
          : '0 4px 8px rgba(0, 0, 0, 0.1)',
    },
  },
}));

const SectionHeading = styled(Typography)(({ theme }) => ({
  fontWeight: 600,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
  marginBottom: theme.spacing(0.5),
  fontSize: '0.7rem',
  color: theme.palette.text.secondary,
  letterSpacing: 0.2,
}));

const CitationContent = styled(Typography)(({ theme }) => ({
  fontSize: '0.8rem',
  lineHeight: 1.5,
  color: theme.palette.text.primary,
  fontStyle: 'italic',
  marginBottom: theme.spacing(0.5),
  paddingBottom: theme.spacing(0.5),
  borderLeft: `2px solid ${theme.palette.primary.main}`,
  paddingLeft: theme.spacing(1.5),
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.dark, 0.05)
      : alpha(theme.palette.primary.lighter, 0.2),
  borderRadius: `0 ${theme.shape.borderRadius}px ${theme.shape.borderRadius}px 0`,
  padding: theme.spacing(1, 1.5),
}));

interface CitationHoverCardProps {
  citation: CustomCitation;
  isVisible: boolean;
  onRecordClick: (record: Record) => void;
  onClose: () => void;
  onViewPdf: (
    url: string,
    citation: CustomCitation,
    citations: CustomCitation[],
    isExcelFile?: boolean,
    buffer?: ArrayBuffer
  ) => Promise<void>;
  aggregatedCitations: CustomCitation[];
}

const CitationHoverCard = ({
  citation,
  isVisible,
  onRecordClick,
  onClose,
  onViewPdf,
  aggregatedCitations,
}: CitationHoverCardProps) => {
  const hasRecordId = Boolean(citation?.metadata?.recordId);
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const scrollableStyles = createScrollableContainerStyle(theme);

  // Safe getter functions to prevent undefined access errors
  const getPageNumber = () => {
    try {
      return citation?.metadata?.pageNum?.[0];
    } catch (error) {
      console.warn('Error accessing pageNum:', error);
      return undefined;
    }
  };

  const getSheetName = () => {
    try {
      return citation?.metadata?.sheetName;
    } catch (error) {
      console.warn('Error accessing sheetName:', error);
      return undefined;
    }
  };

  const getBlockNumber = () => {
    try {
      return citation?.metadata?.blockNum?.[0];
    } catch (error) {
      console.warn('Error accessing blockNum:', error);
      return undefined;
    }
  };

  const getExtension = () => {
    try {
      return citation?.metadata?.extension;
    } catch (error) {
      console.warn('Error accessing extension:', error);
      return '';
    }
  };

  const getWebUrl = () => {
    try {
      let webUrl = citation?.metadata?.webUrl;
      if (citation?.metadata?.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
        const baseUrl = `${window.location.protocol}//${window.location.host}`;
        webUrl = baseUrl + webUrl;
      }
      return webUrl;
    } catch (error) {
      console.warn('Error accessing webUrl:', error);
      return undefined;
    }
  };

  const handleClick = (e: React.MouseEvent): void => {
    e.preventDefault();
    e.stopPropagation();
    if (hasRecordId && citation.metadata?.recordId) {
      // Create a proper Record object with the required citations property
      const record: Record = {
        ...citation.metadata,
        recordId: citation.metadata.recordId,
        citations: aggregatedCitations.filter(
          (c) => c.metadata?.recordId === citation.metadata?.recordId
        ),
      };
      onRecordClick(record);
    }
  };

  const handleOpenPdf = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    if (citation?.metadata?.recordId) {
      try {
        const extension = getExtension();
        const isExcelOrCSV = ['csv', 'xlsx', 'xls'].includes(extension);
        onViewPdf('', citation, aggregatedCitations, isExcelOrCSV);
      } catch (err) {
        console.error('Failed to fetch document:', err);
      }
    }
  };

  function isDocViewable(extension: string) {
    const viewableExtensions = [
      'pdf',
      'xlsx',
      'xls',
      'csv',
      'docx',
      'html',
      'txt',
      'md',
      'ppt',
      'pptx',
      'jpg',
      'jpeg',
      'png',
      'webp',
      'svg',
    ];
    return viewableExtensions.includes(extension);
  }

  const pageNumber = getPageNumber();
  const sheetName = getSheetName();
  const blockNumber = getBlockNumber();
  const extension = getExtension();
  const webUrl = getWebUrl();

  return (
    <Fade in={isVisible} timeout={150}>
      <StyledCard sx={{ ...scrollableStyles }}>
        <Stack spacing={2} sx={{ position: 'relative', zIndex: 0 }}>
          {/* Document Header with View Button */}
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <DocumentTitle
              onClick={handleClick}
              sx={{
                cursor: hasRecordId ? 'pointer' : 'default',
                transition: 'color 0.2s ease-in-out',
                '&:hover': hasRecordId
                  ? {
                      color: 'primary.main',
                    }
                  : {},
              }}
            >
              <Icon
                icon={fileDocumentIcon}
                width={16}
                height={16}
                style={{
                  flexShrink: 0,
                  color: theme.palette.primary.main,
                }}
              />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {citation.metadata?.recordName || 'Document'}
              </span>
            </DocumentTitle>

            <Box sx={{ display: 'flex', alignItems: 'center' }}>
              {webUrl && (
                <Tooltip
                  title="Open in new tab"
                  arrow
                  placement="top"
                  sx={{ zIndex: 2999, pb: 10 }}
                >
                  <Box component="span" sx={{ zIndex: 2999 }}>
                    <IconButton
                      onClick={() => window.open(webUrl, '_blank', 'noopener,noreferrer')}
                      size="small"
                      sx={{
                        mr: 0.75,
                        color: 'primary.main',
                        bgcolor: isDarkMode
                          ? alpha(theme.palette.primary.main, 0.1)
                          : alpha(theme.palette.primary.lighter, 0.3),
                        '&:hover': {
                          bgcolor: isDarkMode
                            ? alpha(theme.palette.primary.main, 0.2)
                            : alpha(theme.palette.primary.lighter, 0.5),
                        },
                        width: 24,
                        height: 24,
                      }}
                    >
                      <Icon icon={linkIcon} width={14} height={14} />
                    </IconButton>
                  </Box>
                </Tooltip>
              )}

              {extension && isDocViewable(extension) && (
                <Tooltip title="View document" arrow placement="top" sx={{ zIndex: 2999, pb: 10 }}>
                  <Box component="span" sx={{ zIndex: 2999 }}>
                    <ActionButton size="small" variant="contained" onClick={handleOpenPdf}>
                      <Icon
                        icon={eyeIcon}
                        width={14}
                        height={14}
                        style={{ marginRight: '4px', flexShrink: 0 }}
                      />
                      View
                    </ActionButton>
                  </Box>
                </Tooltip>
              )}
            </Box>
          </Box>

          {/* Document Metadata - Fixed with safe access */}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {pageNumber && (
              <MetaChip size="small" label={`Page ${pageNumber}`} />
            )}
            {sheetName && extension && ['xlsx', 'csv', 'xls'].includes(extension) && (
              <MetaChip size="small" label={`${sheetName}`} />
            )}
            {extension && ['xlsx', 'csv', 'xls'].includes(extension) && blockNumber && (
              <MetaChip 
                size="small" 
                label={`Row ${extension === 'csv' ? blockNumber + 1 : blockNumber}`} 
              />
            )}
            {extension && (
              <MetaChip size="small" label={extension.toUpperCase()} />
            )}
          </Box>

          {/* Citation Content */}
          <CitationContent>{citation?.content || 'No content available.'}</CitationContent>
        </Stack>
      </StyledCard>
    </Fade>
  );
};

export default CitationHoverCard;