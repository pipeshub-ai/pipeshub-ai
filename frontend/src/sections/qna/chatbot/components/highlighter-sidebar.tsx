import type { HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';

import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/mdi/close';
import React, { useState, useEffect } from 'react';
import tableRowIcon from '@iconify-icons/mdi/table-row';
import fullScreenIcon from '@iconify-icons/mdi/fullscreen';
import citationIcon from '@iconify-icons/mdi/format-quote-close';
import fileExcelIcon from '@iconify-icons/mdi/file-excel-outline';
import fullScreenExitIcon from '@iconify-icons/mdi/fullscreen-exit';

import { alpha, styled } from '@mui/material/styles';
import {
  Box,
  List,
  Button,
  Tooltip,
  ListItem,
  useTheme,
  Typography,
  IconButton,
} from '@mui/material';

import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Updated props interface to include highlightedCitationId
interface CitationSidebarProps {
  citations: ProcessedCitation[];
  scrollViewerTo: (highlight: HighlightType) => void;
  highlightedCitationId?: string | null;
  toggleFullScreen: () => void;
  onClosePdf: () => void;
}

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

const PageIndicator = styled(Typography)(({ theme }) => ({
  marginTop: theme.spacing(1),
  display: 'inline-flex',
  alignItems: 'center',
  fontSize: '10px',
  padding: theme.spacing(0.25, 0.75),
  borderRadius: '4px',
  backgroundColor: theme.palette.mode === 'dark' ? '#3a3d42' : '#e9ecef',
  color: theme.palette.mode === 'dark' ? '#b8bcc8' : '#495057',
  fontWeight: 500,
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  border: theme.palette.mode === 'dark' ? '1px solid #404448' : '1px solid #dee2e6',
}));

const CitationTitle = styled(Typography)(({ theme }) => ({
  fontWeight: 600,
  fontSize: '11px',
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  color: theme.palette.mode === 'dark' ? '#e8eaed' : '#212529',
}));

const SidebarTitle = styled(Typography)(({ theme }) => ({
  fontSize: '14px',
  fontWeight: 600,
  fontFamily: '"Segoe UI", Tahoma, Geneva, Verdana, sans-serif',
  color: theme.palette.mode === 'dark' ? '#e8eaed' : '#212529',
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

const CitationSidebar = ({
  citations,
  scrollViewerTo,
  highlightedCitationId = null,
  toggleFullScreen,
  onClosePdf,
}: CitationSidebarProps) => {
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const scrollableStyles = createScrollableContainerStyle(theme);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  
  useEffect(() => {
    if (highlightedCitationId) {
      // Find the citation that matches the highlighted ID using multiple strategies
      const citationToHighlight = citations.find(
        (citation) =>
          citation.highlight?.id === highlightedCitationId ||
          citation.citationId === highlightedCitationId ||
          citation.metadata?._id === highlightedCitationId ||
          citation.id === highlightedCitationId
      );

      if (citationToHighlight) {
        // Use the citation's highlight ID or fallback to other IDs
        const citationId = citationToHighlight.highlight?.id || 
                          citationToHighlight.citationId || 
                          citationToHighlight.metadata?._id || 
                          citationToHighlight.id;
        
        setSelectedCitation(citationId || null);

        // If we found it, scroll to it in the sidebar
        if (citationToHighlight?.highlight) {
          // Find the list item element for this citation
          const listItemId = `citation-item-${citationId}`;
          const listItem = document.getElementById(listItemId);
          if (listItem) {
            // Scroll the list item into view
            listItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }
      }
    } else {
      setSelectedCitation(null);
    }
  }, [highlightedCitationId, citations]);

  const handleCitationClick = (citation: ProcessedCitation) => {
    if (citation.highlight) {
      // Ensure the highlight has all required properties
      const highlight: HighlightType = {
        ...citation.highlight,
        content: citation.highlight.content || { text: '' },
        // Make sure position is properly set
        position: citation.highlight.position || {
          boundingRect: {},
          rects: [],
          pageNumber: citation.metadata?.pageNum?.[0] || 1,
        },
        // Make sure id is defined with multiple fallback strategies
        id: citation.highlight.id || citation.citationId || citation.metadata?._id || String(Math.random()).slice(2),
      };
      // Try using the highlight we constructed rather than citation.highlight directly
      try {
        scrollViewerTo(highlight);
      } catch (err) {
        // Fallback: try again after small delay
        setTimeout(() => {
          try {
            scrollViewerTo(highlight);
          } catch (fallbackErr) {
            // Silent fallback failure
          }
        }, 200);
      }

      // Also set the hash
      document.location.hash = `highlight-${highlight.id}`;
      setSelectedCitation(highlight.id);
    } else {
      // No highlight found for citation
    }
  };

  const handleOnClosePdf = () => {
    onClosePdf();
  };

  return (
    <StyledSidebar>
      <SidebarHeader>
        <Icon
          icon={citationIcon}
          width={18}
          height={18}
          style={{
            color: isDarkMode ? '#b8bcc8' : '#495057',
          }}
        />
        <SidebarTitle>Citations</SidebarTitle>
        <Tooltip placement="top" title={isFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'}>
          <IconButton
            onClick={toggleFullScreen}
            size="small"
            sx={{
              ml: 2,
              backgroundColor: isDarkMode ? alpha(theme.palette.background.paper, 0.9) : '#ffffff',
              boxShadow: isDarkMode ? '0 4px 12px rgba(0, 0, 0, 0.3)' : '0 2px 4px rgba(0,0,0,0.1)',
              border: isDarkMode ? '1px solid #404448' : '1px solid #e1e5e9',
              color: theme.palette.primary.main,
              '&:hover': {
                backgroundColor: isDarkMode ? alpha(theme.palette.background.paper, 1) : '#f8f9fa',
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

      <List
        sx={{
          flex: 1,
          overflow: 'auto',
          p: 1,
          bgcolor: 'transparent',
          ...scrollableStyles,
        }}
      >
        {citations.map((citation, index) => (
          <StyledListItem
            key={citation.metadata?._id || citation.highlight?.id || index}
            id={`citation-item-${citation.highlight?.id || citation.citationId || citation.metadata?._id || citation.id}`}
            onClick={() => handleCitationClick(citation)}
            sx={{
              bgcolor:
                selectedCitation === (citation.highlight?.id || citation.citationId || citation.metadata?._id || citation.id)
                  ? isDarkMode
                    ? alpha(theme.palette.primary.dark, 0.15)
                    : alpha(theme.palette.primary.lighter, 0.3)
                  : 'transparent',
              boxShadow:
                selectedCitation === (citation.highlight?.id || citation.citationId || citation.metadata?._id || citation.id)
                  ? isDarkMode
                    ? `0 0 0 1px ${alpha(theme.palette.primary.main, 0.3)}`
                    : `0 0 0 1px ${alpha(theme.palette.primary.main, 0.3)}`
                  : 'none',
            }}
          >
            <Box>
              <CitationTitle
                sx={{
                  color:
                    selectedCitation === (citation.highlight?.id || citation.citationId || citation.metadata?._id || citation.id)
                      ? '#0066cc'
                      : isDarkMode
                        ? '#e8eaed'
                        : '#212529',
                }}
              >
                Citation {citation.chunkIndex ? citation.chunkIndex : index + 1}
              </CitationTitle>

              <CitationContent>
                {' '}
                {citation.metadata?.extension === 'pdf' &&
                citation.metadata?.blockText &&
                typeof citation.metadata?.blockText === 'string' &&
                citation.metadata?.blockText.length > 0
                  ? citation.metadata?.blockText
                  : citation.content}
              </CitationContent>

              <Box sx={{ mt: 1.5, display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                {citation.metadata?.sheetName && (
                  <MetaLabel>
                    <Icon
                      icon={fileExcelIcon}
                      width={12}
                      height={12}
                      style={{
                        color: isDarkMode ? '#b8bcc8' : '#495057',
                      }}
                    />
                    {citation.metadata.sheetName}
                  </MetaLabel>
                )}

                {(citation.metadata.extension === 'csv' ||
                  citation.metadata.extension === 'xlsx' ||
                  citation.metadata.extension === 'xls') &&
                  citation.metadata?.blockNum?.[0] && (
                    <MetaLabel>
                      <Icon
                        icon={tableRowIcon}
                        width={12}
                        height={12}
                        style={{
                          color: isDarkMode ? '#b8bcc8' : '#495057',
                        }}
                      />
                      {citation.metadata.extension === 'csv'
                        ? `Row ${citation.metadata.blockNum[0] + 1}`
                        : `Row ${citation.metadata.blockNum[0]}`}
                    </MetaLabel>
                  )}

                {citation.highlight?.position && citation.highlight?.position.pageNumber > 0 && (
                  <PageIndicator>Page {citation.highlight.position.pageNumber}</PageIndicator>
                )}
              </Box>
            </Box>
          </StyledListItem>
        ))}
      </List>
    </StyledSidebar>
  );
};

export default CitationSidebar;