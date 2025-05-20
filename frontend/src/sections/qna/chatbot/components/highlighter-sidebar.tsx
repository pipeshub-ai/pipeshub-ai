import type { HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';

import React, { useState, useEffect } from 'react';
import { Icon } from '@iconify/react';
import citationIcon from '@iconify-icons/mdi/format-quote-close';

import { styled, alpha } from '@mui/material/styles';
import { Box, List, ListItem, Typography, Divider, useTheme } from '@mui/material';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Updated props interface to include highlightedCitationId
interface CitationSidebarProps {
  citations: ProcessedCitation[];
  scrollViewerTo: (highlight: HighlightType) => void;
  highlightedCitationId?: string | null;
}

const StyledSidebar = styled(Box)(({ theme }) => ({
  width: '300px',
  borderLeft:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.divider, 0.1)}`
      : `1px solid ${theme.palette.divider}`,
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.paper, 0.9)
      : theme.palette.background.paper,
  overflow: 'hidden',
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
      ? alpha(theme.palette.common.white, 0.8)
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

const PageIndicator = styled(Typography)(({ theme }) => ({
  marginTop: theme.spacing(1),
  display: 'inline-flex',
  alignItems: 'center',
  fontSize: '0.75rem',
  padding: theme.spacing(0.25, 0.75),
  borderRadius: '4px',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.dark, 0.2)
      : alpha(theme.palette.primary.lighter, 0.4),
  color: theme.palette.mode === 'dark' ? theme.palette.primary.light : theme.palette.primary.dark,
  fontWeight: 500,
}));

const CitationSidebar = ({
  citations,
  scrollViewerTo,
  highlightedCitationId = null,
}: CitationSidebarProps) => {
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';
  const scrollableStyles = createScrollableContainerStyle(theme);

  useEffect(() => {
    if (highlightedCitationId) {
      setSelectedCitation(highlightedCitationId);

      // Find the citation that matches the highlighted ID
      const citationToHighlight = citations.find(
        (citation) => citation.highlight?.id === highlightedCitationId
      );

      // If we found it, scroll to it in the sidebar
      if (citationToHighlight?.highlight) {
        // Find the list item element for this citation
        const listItem = document.getElementById(`citation-item-${highlightedCitationId}`);
        if (listItem) {
          // Scroll the list item into view
          listItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
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
        // Make sure id is defined
        id: citation.highlight.id || citation.metadata?._id || String(Math.random()).slice(2),
      };

      // Use console.log to debug
      console.log('Sidebar: clicking citation with ID:', highlight.id);
      console.log('Position data:', highlight.position);

      // Try using the highlight we constructed rather than citation.highlight directly
      try {
        scrollViewerTo(highlight);
      } catch (err) {
        console.error('Error scrolling to highlight:', err);

        // Fallback: try again after small delay (but without the original citation.highlight which could be null)
        // Fix TypeScript error: citation.highlight might be null
        setTimeout(() => {
          try {
            console.log('Trying fallback scroll with constructed highlight again');
            scrollViewerTo(highlight);
          } catch (fallbackErr) {
            console.error('Fallback scroll also failed:', fallbackErr);
          }
        }, 200);
      }

      // Also set the hash
      document.location.hash = `highlight-${highlight.id}`;
      setSelectedCitation(highlight.id);
    }
  };

  return (
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
            color: isDarkMode ? alpha(theme.palette.text.primary, 0.9) : theme.palette.text.primary,
          }}
        >
          Citations
        </Typography>
      </SidebarHeader>

      <List
        sx={{
          flex: 1,
          overflow: 'auto',
          p: 1.5,
          bgcolor: 'transparent',
          ...scrollableStyles,
        }}
      >
        {citations.map((citation, index) => (
          <StyledListItem
            key={citation.highlight?.id || index}
            id={`citation-item-${citation.highlight?.id}`} // Add ID for scrolling
            onClick={() => handleCitationClick(citation)}
            sx={{
              bgcolor:
                selectedCitation === citation.highlight?.id
                  ? isDarkMode
                    ? alpha(theme.palette.primary.dark, 0.15)
                    : alpha(theme.palette.primary.lighter, 0.3)
                  : 'transparent',
              boxShadow:
                selectedCitation === citation.highlight?.id
                  ? isDarkMode
                    ? `0 0 0 1px ${alpha(theme.palette.primary.main, 0.3)}`
                    : `0 0 0 1px ${alpha(theme.palette.primary.main, 0.3)}`
                  : 'none',
              // Add a transition for smooth highlighting
              transition: 'all 0.2s ease-in-out',
              // Add a pulse animation when this is the initially highlighted citation
              animation:
                highlightedCitationId === citation.highlight?.id
                  ? 'pulse 1.5s ease-in-out'
                  : 'none',
              '@keyframes pulse': {
                '0%': {
                  boxShadow: `0 0 0 0 ${alpha(theme.palette.primary.main, 0.5)}`,
                },
                '70%': {
                  boxShadow: `0 0 0 6px ${alpha(theme.palette.primary.main, 0)}`,
                },
                '100%': {
                  boxShadow: `0 0 0 0 ${alpha(theme.palette.primary.main, 0)}`,
                },
              },
            }}
          >
            <Box>
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  color:
                    selectedCitation === citation.highlight?.id
                      ? theme.palette.primary.main
                      : theme.palette.text.primary,
                }}
              >
                Citation {citation.chunkIndex ? citation.chunkIndex : index + 1}
              </Typography>

              <CitationContent>{citation.content}</CitationContent>

              {citation.highlight?.position && citation.highlight?.position.pageNumber > 0 && (
                <PageIndicator>Page {citation.highlight.position.pageNumber}</PageIndicator>
              )}
            </Box>
          </StyledListItem>
        ))}
      </List>
    </StyledSidebar>
  );
};

export default CitationSidebar;
