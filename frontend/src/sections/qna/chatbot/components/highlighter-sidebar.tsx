import type { HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';

import React, { useState } from 'react';

import { styled } from '@mui/material/styles';
import { Box, List, ListItem, Typography } from '@mui/material';

interface CitationSidebarProps {
  citations: ProcessedCitation[];
  scrollViewerTo: (highlight: HighlightType) => void;
}

const StyledSidebar = styled(Box)(({ theme }) => ({
  width: '300px',
  borderLeft: `1px solid ${theme.palette.divider}`,
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: theme.palette.background.paper,
  overflow: 'hidden',
}));

const CitationSidebar = ({ citations, scrollViewerTo }: CitationSidebarProps) => {
  const [selectedCitation, setSelectedCitation] = useState<string | null>(null);

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
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6">Citations</Typography>
      </Box>
      <List sx={{ flex: 1, overflow: 'auto', px: 2 }}>
        {citations.map((citation, index) => (
          <ListItem
            key={citation.highlight?.id || index}
            onClick={() => handleCitationClick(citation)}
            sx={{
              cursor: 'pointer',
              position: 'relative',
              px: 2,
              '&:hover': {
                bgcolor: 'action.hover',
              },
              ...(selectedCitation === citation.highlight?.id && {
                '&::before': {
                  content: '""',
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  border: '2px solid #2e7d32', // Material-UI's green[800]
                  borderRadius: 1,
                  pointerEvents: 'none',
                },
              }),
            }}
          >
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Citation {citation.chunkIndex ? citation.chunkIndex : index + 1}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {citation.content}
              </Typography>
              {citation.highlight?.position && citation.highlight?.position.pageNumber > 0 && (
                <Typography variant="caption" color="primary" sx={{ mt: 1, display: 'block' }}>
                  Page {citation.highlight.position.pageNumber}
                </Typography>
              )}
            </Box>
          </ListItem>
        ))}
      </List>
    </StyledSidebar>
  );
};

export default CitationSidebar;
