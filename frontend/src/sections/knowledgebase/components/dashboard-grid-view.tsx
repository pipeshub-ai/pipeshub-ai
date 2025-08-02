import { Icon } from '@iconify/react';
import React, { memo, useRef, useMemo, useState, useEffect, useCallback } from 'react';
import { Theme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Stack,
  Paper,
  alpha,
  Button,
  Typography,
  CardActionArea,
  CircularProgress,
  Skeleton,
} from '@mui/material';

import type { KnowledgeBase } from '../types/kb';
import { getKBIcon } from '../utils/kb-icon';
import { MenuButton } from './menu-button';
import { EmptyState } from './empty-state';

const GridItem = memo<{
  kb: KnowledgeBase;
  navigateToKB: (kb: KnowledgeBase) => void;
  onEdit: (kb: KnowledgeBase) => void;
  onDelete: (kb: KnowledgeBase) => void;
  theme: any;
  CompactCard: React.ComponentType<{ children: React.ReactNode }>;
}>(({ kb, navigateToKB, onEdit, onDelete, theme, CompactCard }) => {
  const [isHovered, setIsHovered] = useState(false);

  const handleClick = useCallback(() => {
    navigateToKB(kb);
  }, [navigateToKB, kb]);

  const handleMouseEnter = useCallback(() => {
    setIsHovered(true);
  }, []);

  const handleMouseLeave = useCallback(() => {
    setIsHovered(false);
  }, []);

  // Simple, readable date formatting
  const formatDate = useCallback((timestamp: number) => {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return '—';

    const now = new Date();
    const diffTime = Math.abs(now.getTime() - date.getTime());
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    if (diffDays < 30) return `${Math.ceil(diffDays / 7)}w ago`;
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }, []);

  const kbIcon = getKBIcon(kb.name);

  return (
    <Grid item xs={12} sm={6} md={4} lg={3}>
      <Paper
        elevation={0}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        sx={{
          position: 'relative',
          height: 140,
          borderRadius: 2,
          border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
          backgroundColor: 'background.paper',
          transition: 'all 0.2s ease',
          cursor: 'pointer',
          '&:hover': {
            borderColor: 'primary.main',
            boxShadow: (themeVal) =>
              `0 4px 12px ${alpha(themeVal.palette.common.black, themeVal.palette.mode === 'dark' ? 0.4 : 0.1)}`,
            transform: 'translateY(-2px)',
          },
        }}
      >
        <CardActionArea
          onClick={handleClick}
          sx={{
            height: '100%',
            p: 2.5,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            borderRadius: 2,
            '&:hover': {
              backgroundColor: 'transparent',
            },
          }}
        >
          {/* Header Section */}
          <Box sx={{ width: '100%', mb: 2 }}>
            <Stack direction="row" spacing={2} alignItems="flex-start">
              {/* Clean Icon */}
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: 2,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.grey[700], 0.3)
                      : alpha(themeVal.palette.grey[100], 1),
                  color: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? themeVal.palette.grey[400]
                      : themeVal.palette.grey[600],
                  flexShrink: 0,
                  transition: 'all 0.2s ease',
                  ...(isHovered && {
                    backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.1),
                    color: 'primary.main',
                  }),
                }}
              >
                <Icon icon={kbIcon} fontSize={20} />
              </Box>

              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography
                  variant="h6"
                  sx={{
                    fontSize: '1rem',
                    fontWeight: 600,
                    lineHeight: 1.3,
                    color: 'text.primary',
                    mb: 0.5,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {kb.name}
                </Typography>

                <Typography
                  variant="body2"
                  sx={{
                    fontSize: '0.8rem',
                    color: 'text.secondary',
                    fontWeight: 500,
                    textTransform: 'capitalize',
                  }}
                >
                  {kb.userRole}
                </Typography>
              </Box>
            </Stack>
          </Box>

          {/* Footer Section */}
          <Box
            sx={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <Typography
              variant="body2"
              sx={{
                fontSize: '0.75rem',
                color: 'text.secondary',
                fontWeight: 500,
              }}
            >
              Updated {formatDate(kb.updatedAtTimestamp)}
            </Typography>

            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                backgroundColor: 'success.main',
                opacity: 0.8,
              }}
            />
          </Box>
        </CardActionArea>

        {/* Clean Menu Button */}
        {kb.userRole !== 'READER' && (
          <Box
            sx={{
              position: 'absolute',
              top: 8,
              right: 8,
              opacity: isHovered ? 1 : 0.4,
              transition: 'opacity 0.2s ease',
            }}
          >
            <MenuButton
              kb={kb}
              onEdit={onEdit}
              onDelete={onDelete}
              theme={theme}
              sx={{
                width: 28,
                height: 28,
                borderRadius: 1.5,
                backgroundColor: 'background.paper',
                border: (themeVal: Theme) => `1px solid ${themeVal.palette.divider}`,
                color: 'text.secondary',
                '&:hover': {
                  backgroundColor: 'background.default',
                  borderColor: 'text.primary',
                  color: 'text.primary',
                },
              }}
            />
          </Box>
        )}
      </Paper>
    </Grid>
  );
});

GridItem.displayName = 'GridItem';

// Loading skeletons
const GridSkeleton = memo<{ count?: number }>(({ count = 20 }) => (
  <Grid container spacing={2.5}>
    {Array.from(new Array(count)).map((_, index) => (
      <Grid item xs={12} sm={6} md={4} lg={3} key={index}>
        <Skeleton
          variant="rounded"
          height={140}
          sx={{
            borderRadius: 2,
            border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
          }}
          animation="wave"
        />
      </Grid>
    ))}
  </Grid>
));

GridSkeleton.displayName = 'GridSkeleton';

// Memoized components (GridView and ListView remain the same)
const GridView = memo<{
  loading: boolean;
  knowledgeBases: KnowledgeBase[];
  filteredKnowledgeBases: KnowledgeBase[];
  debouncedSearchQuery: string;
  hasMore: boolean;
  navigateToKB: (kb: KnowledgeBase) => void;
  onEditKB: (kb: KnowledgeBase) => void;
  onDeleteKB: (kb: KnowledgeBase) => void;
  theme: any;
  CompactCard: React.ComponentType<{ children: React.ReactNode }>;
  handleLoadMore: () => void;
  handleClearSearch: () => void;
  loadMoreRef: React.RefObject<HTMLDivElement>;
  setCreateKBDialog: (open: boolean) => void;
  loadingMore: boolean;
}>(
  ({
    loading,
    knowledgeBases,
    filteredKnowledgeBases,
    debouncedSearchQuery,
    hasMore,
    navigateToKB,
    onEditKB,
    onDeleteKB,
    theme,
    CompactCard,
    handleLoadMore,
    handleClearSearch,
    loadMoreRef,
    setCreateKBDialog,
    loadingMore,
  }) => {
    if (loading && knowledgeBases.length === 0) {
      return <GridSkeleton />;
    }

    if (filteredKnowledgeBases.length === 0 && !loading) {
      return (
        <EmptyState
          isSearchResult={!!debouncedSearchQuery}
          searchQuery={debouncedSearchQuery}
          onClearSearch={handleClearSearch}
          onCreateKB={() => setCreateKBDialog(true)}
          loading={loading}
        />
      );
    }

    return (
      <Box>
        <Grid container spacing={2.5}>
          {filteredKnowledgeBases.map((kb) => (
            <GridItem
              key={kb.id}
              kb={kb}
              navigateToKB={navigateToKB}
              onEdit={onEditKB}
              onDelete={onDeleteKB}
              theme={theme}
              CompactCard={CompactCard}
            />
          ))}
        </Grid>

        {/* Infinite Scroll Trigger and Loading More Indicator */}
        {hasMore && !debouncedSearchQuery && (
          <Box
            ref={loadMoreRef}
            sx={{
              mt: 4,
              mb: 3,
              textAlign: 'center',
              borderTop: (themeVal) => `1px solid ${alpha(themeVal.palette.divider, 0.5)}`,
              pt: 3,
            }}
          >
            {loadingMore ? (
              <Box sx={{ py: 2 }}>
                <CircularProgress size={20} thickness={4} />
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{
                    mt: 1,
                    fontSize: '0.8125rem',
                  }}
                >
                  Loading more knowledge bases...
                </Typography>
              </Box>
            ) : (
              <Box sx={{ py: 1 }}>
                <Button
                  variant="outlined"
                  onClick={handleLoadMore}
                  sx={{
                    height: 36,
                    px: 3,
                    borderRadius: 1.5,
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    textTransform: 'none',
                    borderColor: 'divider',
                    color: 'text.secondary',
                    backgroundColor: 'background.paper',
                    '&:hover': {
                      borderColor: 'primary.main',
                      backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                      color: 'primary.main',
                    },
                  }}
                >
                  Load More
                </Button>
              </Box>
            )}
          </Box>
        )}

        {/* Loading skeletons for infinite scroll */}
        {loadingMore && (
          <Box sx={{ mt: 2, mb: 3 }}>
            <GridSkeleton count={8} />
          </Box>
        )}
      </Box>
    );
  }
);

GridView.displayName = 'GridView';

export { GridView };
