
import { Icon } from '@iconify/react';
import addIcon from '@iconify-icons/mdi/plus';
import clearIcon from '@iconify-icons/mdi/close';
import searchIcon from '@iconify-icons/mdi/magnify';
import databaseIcon from '@iconify-icons/mdi/database';
import gridViewIcon from '@iconify-icons/mdi/view-grid-outline';
import listViewIcon from '@iconify-icons/mdi/format-list-bulleted';
import React, { memo, useRef, useMemo, useState, useEffect, useCallback } from 'react';

import {
  Box,
  Fade,
  Stack,
  alpha,
  Button,
  Container,
  TextField,
  Typography,
  ToggleButton,
  InputAdornment,
  LinearProgress,
  CircularProgress,
  ToggleButtonGroup,
  Alert,
  Snackbar,
} from '@mui/material';

import { KnowledgeBaseAPI } from '../services/api';

import type { KnowledgeBase } from '../types/kb';
import type { RouteParams } from '../hooks/use-router';
import { CreateKnowledgeBaseDialog } from './dialogs/create-kb-dialog';
import { EditKnowledgeBaseDialog } from './dialogs/edit-dialogs';
import { DeleteConfirmDialog } from './dialogs/delete-confim-dialog';
import { GridView } from './dashboard-grid-view';
import { ListView } from './dashboard-list-view';

interface DashboardProps {
  theme: any;
  navigateToKB: (kb: KnowledgeBase) => void;
  CompactCard: React.ComponentType<{ children: React.ReactNode }>;
  isInitialized: boolean;
  navigate: (route: RouteParams) => void;
}
type ViewMode = 'grid' | 'list';


const useDebounce = (value: string, delay: number) => {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delay]);

  return debouncedValue;
};

const useIntersectionObserver = (callback: () => void, options: IntersectionObserverInit = {}) => {
  const targetRef = useRef<HTMLDivElement>(null);
  const callbackRef = useRef(callback);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    const target = targetRef.current;
    if (!target) return undefined;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          callbackRef.current();
        }
      },
      {
        threshold: 0.1,
        rootMargin: '100px',
        ...options,
      }
    );

    observer.observe(target);

    return () => observer.disconnect();
  }, [options]); 

  return targetRef;
};


const DashboardComponent: React.FC<DashboardProps> = ({
  theme,
  navigateToKB,
  CompactCard,
  isInitialized,
  navigate,
}) => {
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Knowledge base state
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(20);
  const [createKBDialog, setCreateKBDialog] = useState(false);
  const [editKBDialog, setEditKBDialog] = useState(false);
  const [itemToEdit, setItemToEdit] = useState<KnowledgeBase | null>(null);
  const [itemToDelete, setItemToDelete] = useState<KnowledgeBase | null>(null);
  const [deleteDialog, setDeleteDialog] = useState(false);
  const [pageLoading, setPageLoading] = useState(false);

  // Debounced search value
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  // Refs to prevent multiple simultaneous calls
  const loadingRef = useRef(false);
  const currentSearchRef = useRef('');
  const [success, setSuccess] = useState('');
  const [error, setError] = useState('');

  // Load knowledge bases function
  const loadKnowledgeBases = useCallback(
    async (queryString = '', isLoadMore = false, pageNum = 0, pageSize = 20) => {
      if (!isInitialized || loadingRef.current) return;

      // Prevent loading more if already loading or no more data for grid view
      if (isLoadMore && (loadingMore || !hasMore)) return;

      loadingRef.current = true;
      currentSearchRef.current = queryString;

      // Set appropriate loading states
      if (isLoadMore) {
        setLoadingMore(true);
      } else {
        setLoading(true);
        if (queryString !== currentSearchRef.current) {
          setKnowledgeBases([]);
          setHasMore(true);
          setPage(0);
        }
      }

      try {
        const params = {
          page: isLoadMore ? Math.floor(knowledgeBases.length / pageSize) + 1 : pageNum + 1,
          limit: pageSize,
          search: queryString,
        };

        const data = await KnowledgeBaseAPI.getKnowledgeBases(params);

        if (data.knowledgeBases && data.pagination) {
          const newKBs = data.knowledgeBases;
          const totalItems = data.pagination.totalCount;

          if (isLoadMore) {
            setKnowledgeBases((prev) => [...prev, ...newKBs]);
            setHasMore(knowledgeBases.length + newKBs.length < totalItems);
          } else {
            setKnowledgeBases(newKBs);
            setHasMore(newKBs.length < totalItems);
          }

          setTotalCount(totalItems);
        } else if (Array.isArray(data)) {
          if (isLoadMore) {
            setKnowledgeBases((prev) => [...prev, ...data]);
          } else {
            setKnowledgeBases(data);
          }
          setTotalCount(data.length);
          setHasMore(false);
        }
      } catch (err: any) {
        console.error('Failed to fetch knowledge bases:', err);
        if (!isLoadMore) {
          setKnowledgeBases([]);
          setTotalCount(0);
        }
      } finally {
        setLoading(false);
        setLoadingMore(false);
        loadingRef.current = false;
      }
    },
    [isInitialized, knowledgeBases.length, loadingMore, hasMore]
  );

  // Effect for debounced search
  useEffect(() => {
    if (isInitialized) {
      loadKnowledgeBases(debouncedSearchQuery, false, 0, rowsPerPage);
    }
    // eslint-disable-next-line
  }, [debouncedSearchQuery, isInitialized, rowsPerPage]);

  // Load more for infinite scroll
  const handleLoadMore = useCallback(() => {
    if (!loadingMore && hasMore && !loading) {
      loadKnowledgeBases(debouncedSearchQuery, true, 0, rowsPerPage);
    }
  }, [loadingMore, hasMore, loading, debouncedSearchQuery, rowsPerPage, loadKnowledgeBases]);

  // Handlers
  const handleViewChange = useCallback(
    (event: React.MouseEvent<HTMLElement>, newView: ViewMode | null): void => {
      if (newView !== null) {
        setViewMode(newView);
      }
    },
    []
  );

  const handleSearchChange = useCallback((event: React.ChangeEvent<HTMLInputElement>): void => {
    setSearchQuery(event.target.value);
  }, []);

  const handleClearSearch = useCallback((): void => {
    setSearchQuery('');
    setPage(0);
  }, []);

  const handlePageChange = useCallback(
    (newPage: number) => {
      setPage(newPage);
      loadKnowledgeBases(debouncedSearchQuery, false, newPage, rowsPerPage);
    },
    [debouncedSearchQuery, rowsPerPage, loadKnowledgeBases]
  );

  const handleRowsPerPageChange = useCallback(
    (newRowsPerPage: number) => {
      setRowsPerPage(newRowsPerPage);
      setPage(0);
      loadKnowledgeBases(debouncedSearchQuery, false, 0, newRowsPerPage);
    },
    [debouncedSearchQuery, loadKnowledgeBases]
  );

  const handleCreateKB = useCallback(
    async (name: string) => {
      setLoading(true);
      try {
        const newKB = await KnowledgeBaseAPI.createKnowledgeBase(name);
        setKnowledgeBases((prev) => [...prev, newKB]);
        setSuccess('Knowledge base created successfully');
        setCreateKBDialog(false);
      } catch (err: any) {
        setError(err.message || 'Failed to create knowledge base');
      } finally {
        setLoading(false);
      }
    },
    [setLoading, setSuccess, setError, setKnowledgeBases]
  );

  const onEditKB = useCallback(async (kb: KnowledgeBase) => {
    setItemToEdit(kb);
    setEditKBDialog(true);
  }, []);

  const handleEditKB = useCallback(
    async (name: string) => {
      if (!itemToEdit) return;
      setLoading(true);
      try {
        await KnowledgeBaseAPI.updateKnowledgeBase(itemToEdit.id, name);

        // Update knowledge bases list
        setKnowledgeBases((prev) =>
          prev.map((kb) => (kb.id === itemToEdit.id ? { ...kb, name } : kb))
        );
        setSuccess('Knowledge base updated successfully');
        setEditKBDialog(false);
        setItemToEdit(null);
      } catch (err: any) {
        setError(err.message || 'Failed to update knowledge base');
      } finally {
        setLoading(false);
      }
    },
    [setLoading, setSuccess, setError, setKnowledgeBases, itemToEdit]
  );

  const onDeleteKB = useCallback(async (kb: KnowledgeBase) => {
    setItemToDelete(kb);
    setDeleteDialog(true);
  }, []);

  const handleDelete = async () => {
    if (!itemToDelete) return;

    setPageLoading(true);
    try {
      await KnowledgeBaseAPI.deleteKnowledgeBase(itemToDelete.id);
      setKnowledgeBases((prev) => prev.filter((kb) => kb.id !== itemToDelete.id));
      setSuccess('Knowledge base deleted successfully');
    } catch (err: any) {
      setError(err.message || 'Failed to delete item');
    } finally {
      setPageLoading(false);
    }
    setDeleteDialog(false);
    setItemToDelete(null);
  };

  // Intersection observer for infinite scroll
  const loadMoreRef = useIntersectionObserver(handleLoadMore);

  // Memoized filtered knowledge bases
  const filteredKnowledgeBases = useMemo(() => knowledgeBases, [knowledgeBases]);


  // Calculate display counts
  const { displayCount, actualTotalCount } = useMemo(
    () => ({
      displayCount: filteredKnowledgeBases.length,
      actualTotalCount: totalCount,
    }),
    [filteredKnowledgeBases.length, totalCount]
  );

  if (!isInitialized) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
        }}
      >
        <CircularProgress size={40} thickness={4} />
      </Box>
    );
  }

  return (
    <Box sx={{ height: '90vh', display: 'flex', flexDirection: 'column' }}>
      <Box
        sx={{
          borderBottom: (themeVal) => `1px solid ${themeVal.palette.divider}`,
          backgroundColor: 'background.paper',
          px: { xs: 2, sm: 3 },
          py: 2,
        }}
      >
        {(loading || loadingMore) && (
          <LinearProgress
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 2,
              backgroundColor: 'transparent',
            }}
          />
        )}

        <Stack
          direction="row"
          alignItems="center"
          spacing={3}
          sx={{
            width: '100%',
            minHeight: 56,
          }}
        >
          <Box sx={{ flexShrink: 0 }}>
            <Typography
              variant="h5"
              sx={{
                fontSize: '1.5rem',
                fontWeight: 700,
                color: 'text.primary',
                letterSpacing: '-0.02em',
                lineHeight: 1.2,
              }}
            >
              Knowledge Bases
            </Typography>
            {actualTotalCount > 0 && (
              <Typography
                variant="caption"
                sx={{
                  fontSize: '0.75rem',
                  color: 'text.secondary',
                  fontWeight: 500,
                }}
              >
                {actualTotalCount} total
              </Typography>
            )}
          </Box>

          <Box sx={{ flexGrow: 1 }} />

          <Box sx={{ flexShrink: 0, minWidth: 300 }}>
            <TextField
              placeholder="Search knowledge bases..."
              value={searchQuery}
              onChange={handleSearchChange}
              size="small"
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Icon icon={searchIcon} fontSize={18} color="action.active" />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <Box
                      component="button"
                      onClick={handleClearSearch}
                      sx={{
                        width: 20,
                        height: 20,
                        borderRadius: 0.5,
                        border: 'none',
                        backgroundColor: 'transparent',
                        color: 'action.active',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        cursor: 'pointer',
                        '&:hover': {
                          backgroundColor: 'action.hover',
                          color: 'text.primary',
                        },
                      }}
                    >
                      <Icon icon={clearIcon} fontSize={14} />
                    </Box>
                  </InputAdornment>
                ),
              }}
              sx={{
                width: '100%',
                '& .MuiOutlinedInput-root': {
                  height: 36,
                  borderRadius: 1.5,
                  backgroundColor: 'background.default',
                  border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                  fontSize: '0.875rem',
                  '&:hover': {
                    borderColor: 'action.active',
                  },
                  '&.Mui-focused': {
                    borderColor: 'primary.main',
                    backgroundColor: 'background.paper',
                  },
                  '& .MuiOutlinedInput-notchedOutline': {
                    border: 'none',
                  },
                },
                '& .MuiInputBase-input::placeholder': {
                  color: 'text.secondary',
                  opacity: 0.7,
                  fontSize: '0.875rem',
                },
              }}
            />
            {searchQuery && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  mt: 0.5,
                  display: 'block',
                  fontSize: '0.7rem',
                }}
              >
                {displayCount} of {actualTotalCount} results
              </Typography>
            )}
          </Box>

          <ToggleButtonGroup
            value={viewMode}
            exclusive
            onChange={handleViewChange}
            size="small"
            sx={{
              flexShrink: 0,
              '& .MuiToggleButton-root': {
                border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                borderRadius: 1,
                px: 1.5,
                py: 0.5,
                minWidth: 36,
                height: 32,
                color: 'text.secondary',
                '&.Mui-selected': {
                  backgroundColor: 'action.selected',
                  color: 'text.primary',
                  borderColor: 'divider',
                },
                '&:hover': {
                  backgroundColor: 'action.hover',
                },
              },
            }}
          >
            <ToggleButton value="grid">
              <Icon icon={gridViewIcon} fontSize={16} />
            </ToggleButton>
            <ToggleButton value="list">
              <Icon icon={listViewIcon} fontSize={16} />
            </ToggleButton>
          </ToggleButtonGroup>

          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            <Button
              variant="outlined"
              startIcon={<Icon icon={databaseIcon} fontSize={14} />}
              onClick={() => navigate({ view: 'all-records' })}
              sx={{
                height: 32,
                px: 1.5,
                borderRadius: 1,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                borderColor: 'divider',
                color: 'text.secondary',
                '&:hover': {
                  borderColor: 'action.active',
                  backgroundColor: 'action.hover',
                  color: 'text.primary',
                },
              }}
            >
              <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>All Records</Box>
            </Button>

            <Button
              variant="outlined"
              startIcon={<Icon icon={addIcon} fontSize={14} />}
              onClick={() => setCreateKBDialog(true)}
              sx={{
                height: 32,
                px: 1.5,
                borderRadius: 1,
                fontSize: '0.8125rem',
                fontWeight: 500,
                textTransform: 'none',
                borderColor: 'primary.main',
                color: 'primary.main',
                '&:hover': {
                  backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                  borderColor: 'primary.dark',
                },
              }}
            >
              <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>Create KB</Box>
            </Button>
          </Stack>
        </Stack>
      </Box>

      <Box
        sx={{
          flex: 1,
          overflow: 'auto',
          display: 'flex',
          flexDirection: 'column',
          '&::-webkit-scrollbar': {
            width: '6px',
          },
          '&::-webkit-scrollbar-track': {
            background: 'transparent',
          },
          '&::-webkit-scrollbar-thumb': {
            background: (themeVal) => alpha(themeVal.palette.grey[500], 0.3),
            borderRadius: '3px',
            '&:hover': {
              background: (themeVal) => alpha(themeVal.palette.grey[500], 0.5),
            },
          },
        }}
      >
        <Container
          maxWidth="xl"
          sx={{
            py: { xs: 2, sm: 3 },
            px: { xs: 2, sm: 3 },
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <Fade in timeout={300}>
            <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              {viewMode === 'grid' ?
               <GridView 
                loading={loading}
                knowledgeBases={knowledgeBases}
                filteredKnowledgeBases={filteredKnowledgeBases}
                debouncedSearchQuery={debouncedSearchQuery}
                hasMore={hasMore}
                navigateToKB={navigateToKB}
                onEditKB={onEditKB}
                onDeleteKB={onDeleteKB}
                theme={theme}
                CompactCard={CompactCard}
                handleLoadMore={handleLoadMore}
                handleClearSearch={handleClearSearch}
                loadMoreRef={loadMoreRef}
                setCreateKBDialog={setCreateKBDialog}
                loadingMore={loadingMore}
               /> :
                <ListView 
                loading={loading}
                filteredKnowledgeBases={filteredKnowledgeBases}
                navigateToKB={navigateToKB}
                onEditKB={onEditKB}
                onDeleteKB={onDeleteKB}
                theme={theme}
                totalCount={totalCount}
                page={page}
                rowsPerPage={rowsPerPage}
                handlePageChange={handlePageChange}
                handleRowsPerPageChange={handleRowsPerPageChange}
                />}
            </Box>
          </Fade>
        </Container>

        {viewMode === 'grid' && !debouncedSearchQuery && filteredKnowledgeBases.length > 0 && (
          <Box
            sx={{
              borderTop: (themeVal) => `1px solid ${themeVal.palette.divider}`,
              backgroundColor: 'background.paper',
              py: 1.5,
              px: 3,
              textAlign: 'center',
              mt: 'auto',
              flexShrink: 0,
            }}
          >
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
              Showing {displayCount} of {actualTotalCount} knowledge bases
              {hasMore && (
                <Box
                  component="span"
                  sx={{
                    ml: 1,
                    px: 1,
                    py: 0.25,
                    borderRadius: 0.5,
                    backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.1),
                    color: 'primary.main',
                    fontSize: '0.7rem',
                    fontWeight: 500,
                  }}
                >
                  More available
                </Box>
              )}
            </Typography>
          </Box>
        )}
      </Box>

      <CreateKnowledgeBaseDialog
        open={createKBDialog}
        onClose={() => setCreateKBDialog(false)}
        onSubmit={handleCreateKB}
        loading={loading}
      />

      <EditKnowledgeBaseDialog
        open={editKBDialog}
        onClose={() => {
          setEditKBDialog(false);
          setItemToEdit(null);
        }}
        onSubmit={handleEditKB}
        currentName={itemToEdit?.name || ''}
        loading={loading}
      />

      <DeleteConfirmDialog
        open={deleteDialog}
        onClose={() => {
          setDeleteDialog(false);
          setItemToDelete(null);
        }}
        onConfirm={handleDelete}
        title="Confirm Delete"
        message={`Are you sure you want to delete ${itemToDelete?.name}?`}
        loading={pageLoading}
      />

      <Snackbar
        open={!!success}
        autoHideDuration={3000}
        onClose={() => setSuccess('')}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert
          severity="success"
          onClose={() => setSuccess('')}
          sx={{
            borderRadius: 2,
            fontSize: '0.85rem',
            fontWeight: 500,
          }}
        >
          {success}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default memo(DashboardComponent);
