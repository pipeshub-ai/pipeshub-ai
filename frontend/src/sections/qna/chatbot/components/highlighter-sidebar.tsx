import type { HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';

import React, { useState } from 'react';
import { Icon } from '@iconify/react';
import citationIcon from '@iconify-icons/mdi/format-quote-close';

import { styled, alpha } from '@mui/material/styles';
import { Box, List, ListItem, Typography, Divider, useTheme } from '@mui/material';

interface CitationSidebarProps {
  citations: ProcessedCitation[];
  scrollViewerTo: (highlight: HighlightType) => void;
}

const StyledSidebar = styled(Box)(({ theme }) => ({
  width: '300px',
  borderLeft: theme.palette.mode === 'dark' 
    ? `1px solid ${alpha(theme.palette.divider, 0.1)}` 
    : `1px solid ${theme.palette.divider}`,
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: theme.palette.mode === 'dark'
    ? alpha(theme.palette.background.paper, 0.9)
    : theme.palette.background.paper,
  overflow: 'hidden',
}));

const SidebarHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderBottom: theme.palette.mode === 'dark'
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
    backgroundColor: theme.palette.mode === 'dark'
      ? alpha(theme.palette.action.hover, 0.1)
      : theme.palette.action.hover,
  },
}));

const CitationContent = styled(Typography)(({ theme }) => ({
  color: theme.palette.mode === 'dark'
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
    backgroundColor: theme.palette.mode === 'dark'
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
  backgroundColor: theme.palette.mode === 'dark'
    ? alpha(theme.palette.primary.dark, 0.2)
    : alpha(theme.palette.primary.lighter, 0.4),
  color: theme.palette.mode === 'dark'
    ? theme.palette.primary.light
    : theme.palette.primary.dark,
  fontWeight: 500,
}));

const CitationSidebar = ({ citations, scrollViewerTo }: CitationSidebarProps) => {
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';

  const handleCitationClick = (citation: ProcessedCitation) => {
    if (citation.highlight) {
      // eslint-disable-next-line
      const highlight: HighlightType = {
        ...citation.highlight,
        content: citation.highlight.content || { text: '' },
      };
      scrollViewerTo(citation.highlight);
      document.location.hash = `highlight-${citation.highlight.id}`;
      setSelectedCitation(citation.highlight.id);
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
            color: isDarkMode 
              ? theme.palette.primary.light 
              : theme.palette.primary.main 
          }} 
        />
        <Typography 
          variant="h6"
          sx={{ 
            fontSize: '1rem',
            fontWeight: 600,
            color: isDarkMode
              ? alpha(theme.palette.text.primary, 0.9)
              : theme.palette.text.primary
          }}
        >
          Citations
        </Typography>
      </SidebarHeader>
      
      <List sx={{ 
        flex: 1, 
        overflow: 'auto', 
        p: 1.5,
        bgcolor: 'transparent' 
      }}>
        {citations.map((citation, index) => (
          <StyledListItem
            key={citation.highlight?.id || index}
            onClick={() => handleCitationClick(citation)}
            sx={{
              bgcolor: selectedCitation === citation.highlight?.id
                ? isDarkMode
                  ? alpha(theme.palette.primary.dark, 0.15)
                  : alpha(theme.palette.primary.lighter, 0.3)
                : 'transparent',
              boxShadow: selectedCitation === citation.highlight?.id
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
                  color: selectedCitation === citation.highlight?.id
                    ? theme.palette.primary.main
                    : theme.palette.text.primary,
                }}
              >
                Citation {citation.chunkIndex ? citation.chunkIndex : index + 1}
              </Typography>
              
              <CitationContent>
                {citation.content}
              </CitationContent>
              
              {citation.highlight?.position && citation.highlight?.position.pageNumber > 0 && (
                <PageIndicator>
                  Page {citation.highlight.position.pageNumber}
                </PageIndicator>
              )}
            </Box>
          </StyledListItem>
        ))}
      </List>
    </StyledSidebar>
  );
};

export default CitationSidebar;