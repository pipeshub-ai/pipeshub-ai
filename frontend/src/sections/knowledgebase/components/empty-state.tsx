import React, { memo } from 'react';
import { Icon } from '@iconify/react';
import { Paper, Button, Typography, alpha } from '@mui/material';
import searchIcon from '@iconify-icons/mdi/magnify';
import addIcon from '@iconify-icons/mdi/plus';

// Empty State Component
export const EmptyState = memo<{
    isSearchResult?: boolean;
    searchQuery?: string;
    onClearSearch?: () => void;
    onCreateKB?: () => void;
    loading?: boolean;
  }>(({ isSearchResult = false, searchQuery = '', onClearSearch, onCreateKB, loading = false }) => (
    <Paper
      sx={{
        p: 6,
        textAlign: 'center',
        borderRadius: 2,
        border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
        backgroundColor: 'background.paper',
      }}
    >
      <Icon icon={searchIcon} fontSize={48} color="rgba(0,0,0,0.3)" />
      <Typography variant="h6" color="text.secondary" sx={{ mt: 2, mb: 1 }}>
        No knowledge bases found
      </Typography>
      <Typography variant="body2" color="text.secondary">
        {isSearchResult
          ? `No results found for "${searchQuery}"`
          : 'Create your first knowledge base to get started'}
      </Typography>
      {isSearchResult && onClearSearch && (
        <Button
          variant="outlined"
          onClick={onClearSearch}
          sx={{
            mt: 2,
            borderRadius: 2,
            textTransform: 'none',
            borderColor: 'divider',
            color: 'text.primary',
            '&:hover': {
              borderColor: 'primary.main',
              backgroundColor: 'action.hover',
            },
          }}
        >
          Clear search
        </Button>
      )}
      {!isSearchResult && !loading && onCreateKB && (
        <Button
          variant="outlined"
          onClick={onCreateKB}
          sx={{
            mt: 2,
            borderRadius: 2,
            textTransform: 'none',
            borderColor: 'primary.main',
            color: 'primary.main',
            '&:hover': {
              backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
            },
          }}
          startIcon={<Icon icon={addIcon} fontSize={16} />}
        >
          Create Knowledge Base
        </Button>
      )}
    </Paper>
  ));
  
  EmptyState.displayName = 'EmptyState';