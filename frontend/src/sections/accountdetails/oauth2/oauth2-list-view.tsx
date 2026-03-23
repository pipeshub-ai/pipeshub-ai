import { useNavigate } from 'react-router-dom';
import plusIcon from '@iconify-icons/mdi/plus';
import keyLinkIcon from '@iconify-icons/mdi/key-link';
import magniferIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import { useRef, useState, useEffect, useCallback } from 'react';

import {
  Box,
  Fade,
  Grid,
  alpha,
  Stack,
  Alert,
  Paper,
  Button,
  useTheme,
  Skeleton,
  Snackbar,
  Container,
  TextField,
  Typography,
  IconButton,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import { OAuth2AppCard } from './oauth2-app-card';
import { OAuth2Api, type OAuth2App } from './services/oauth2-api';

const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 400;
const INITIAL_PAGE = 1;

export function OAuth2ListView() {
  const theme = useTheme();
  const navigate = useNavigate();
  const isDark = theme.palette.mode === 'dark';

  const [apps, setApps] = useState<OAuth2App[]>([]);
  const [currentPage, setCurrentPage] = useState(INITIAL_PAGE);
  const [loading, setLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMorePages, setHasMorePages] = useState(true);
  const [isFirstLoad, setIsFirstLoad] = useState(true);
  const [search, setSearch] = useState('');
  const [searchActive, setSearchActive] = useState('');
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info';
  }>({
    open: false,
    message: '',
    severity: 'success',
  });
  const [pagination, setPagination] = useState({ page: 1, total: 0, totalPages: 0 });

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const requestIdRef = useRef(0);
  const isRequestInProgressRef = useRef(false);

  const loadApps = useCallback(
    async (page: number, isLoadMore = false, searchQuery?: string) => {
      if (isRequestInProgressRef.current) return;

      requestIdRef.current += 1;
      const currentRequestId = requestIdRef.current;
      isRequestInProgressRef.current = true;

      if (isLoadMore) {
        setIsLoadingMore(true);
      } else {
        setLoading(true);
      }

      try {
        const result = await OAuth2Api.listApps({
          page,
          limit: ITEMS_PER_PAGE,
          search: searchQuery || undefined,
        });

        if (currentRequestId !== requestIdRef.current) return;

        const newApps = Array.isArray(result.data) ? result.data : [];
        const paginationData = result.pagination ?? {};

        setApps((prev) => {
          if (page === INITIAL_PAGE) return newApps;
          const existingIds = new Set(prev.map((a) => a.id));
          const uniqueNew = newApps.filter((a) => !existingIds.has(a.id));
          return [...prev, ...uniqueNew];
        });

        setPagination({
          page: paginationData.page ?? page,
          total: paginationData.total ?? 0,
          totalPages: paginationData.totalPages ?? 0,
        });

        const hasMore =
          typeof paginationData.totalPages === 'number'
            ? page < (paginationData.totalPages ?? 0)
            : newApps.length === ITEMS_PER_PAGE;
        setHasMorePages(hasMore);
      } catch (err: any) {
        if (currentRequestId !== requestIdRef.current) return;
        const msg =
          err?.response?.data?.message || err?.message || 'Failed to load OAuth 2.0 applications.';
        setSnackbar({ open: true, message: msg, severity: 'error' });
        if (page === INITIAL_PAGE) setApps([]);
      } finally {
        setLoading(false);
        setIsLoadingMore(false);
        setIsFirstLoad(false);
        isRequestInProgressRef.current = false;
      }
    },
    [] // stable — no state dependencies
  );

  useEffect(() => {
    const t = setTimeout(() => setSearchActive(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    setCurrentPage(INITIAL_PAGE);
    setHasMorePages(true);
    setApps([]);
    setIsFirstLoad(true);
  }, [searchActive]);

  useEffect(() => {
    const isLoadMore = currentPage > INITIAL_PAGE;
    loadApps(currentPage, isLoadMore, searchActive || undefined);
  }, [currentPage, loadApps, searchActive]);

  useEffect(() => {
    if (observerRef.current) observerRef.current.disconnect();
    if (!sentinelRef.current || !hasMorePages || isFirstLoad) return undefined;

    observerRef.current = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (
          entry?.isIntersecting &&
          !isRequestInProgressRef.current &&
          hasMorePages &&
          !isFirstLoad &&
          !loading
        ) {
          setCurrentPage((prev) => prev + 1);
        }
      },
      { root: null, rootMargin: '200px', threshold: 0 }
    );

    observerRef.current.observe(sentinelRef.current);
    return () => {
      if (observerRef.current) observerRef.current.disconnect();
    };
  }, [hasMorePages, isFirstLoad, loading]);

  const handleOpenApp = (app: OAuth2App) => {
    navigate(`${app.id}`);
  };

  const handleAddNew = () => {
    navigate('new');
  };

  return (
    <Container maxWidth="xl" sx={{ py: 3, px: 3, flex: 1, overflow: 'auto' }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
          boxShadow: isDark ? undefined : `0 1px 3px ${alpha(theme.palette.common.black, 0.06)}`,
        }}
      >
        {/* Header */}
        <Box
          sx={{
            p: 3,
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDark
              ? alpha(theme.palette.background.default, 0.4)
              : alpha(theme.palette.grey[50], 0.6),
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            flexWrap="wrap"
            gap={2}
          >
            <Stack direction="row" alignItems="center" spacing={2}>
              <Box
                sx={{
                  width: 44,
                  height: 44,
                  borderRadius: 1.5,
                  backgroundColor: alpha(theme.palette.primary.main, 0.12),
                  border: `1px solid ${alpha(theme.palette.primary.main, 0.24)}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Iconify
                  icon={keyLinkIcon}
                  width={22}
                  height={22}
                  sx={{ color: theme.palette.primary.main }}
                />
              </Box>
              <Box>
                <Typography
                  sx={{ fontWeight: 600, fontSize: '1.5rem', color: theme.palette.text.primary }}
                >
                  OAuth 2.0
                </Typography>
                <Typography
                  sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary, mt: 0.25 }}
                >
                  Manage OAuth 2.0 applications that can access your organization via Pipeshub
                </Typography>
              </Box>
            </Stack>
            <Button
              variant="contained"
              color="primary"
              startIcon={<Iconify icon={plusIcon} width={18} height={18} />}
              onClick={handleAddNew}
              sx={{
                textTransform: 'none',
                fontWeight: 600,
                borderRadius: 1.5,
                px: 3,
                py: 1.25,
                fontSize: '0.875rem',
              }}
            >
              New OAuth application
            </Button>
          </Stack>
        </Box>

        <Box sx={{ p: 3 }}>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            flexWrap="wrap"
            gap={2}
            sx={{ mb: 3 }}
          >
            <TextField
              placeholder="Search applications by name or description..."
              size="small"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon={magniferIcon}
                      width={20}
                      height={20}
                      sx={{ color: theme.palette.text.secondary }}
                    />
                  </InputAdornment>
                ),
                endAdornment: search ? (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={() => setSearch('')}>
                      <Iconify icon={clearIcon} width={16} height={16} />
                    </IconButton>
                  </InputAdornment>
                ) : null,
              }}
              sx={{
                width: 320,
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1.5,
                  backgroundColor: theme.palette.background.default,
                },
                '& .MuiInputBase-input': { fontSize: '0.875rem' },
              }}
            />
            {!isFirstLoad && apps.length > 0 && (
              <Typography sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary }}>
                {pagination.total} {pagination.total === 1 ? 'application' : 'applications'}
              </Typography>
            )}
          </Stack>

          {loading && isFirstLoad ? (
            <Grid container spacing={2.5}>
              {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={i}>
                  <Skeleton variant="rounded" height={220} sx={{ borderRadius: 2 }} />
                </Grid>
              ))}
            </Grid>
          ) : apps.length === 0 ? (
            <Fade in>
              <Box
                sx={{
                  py: 8,
                  px: 3,
                  textAlign: 'center',
                  borderRadius: 2,
                  border: `1px dashed ${theme.palette.divider}`,
                  backgroundColor: alpha(theme.palette.grey[500], 0.04),
                }}
              >
                <Iconify
                  icon="mdi:application-cog-outline"
                  width={64}
                  height={64}
                  sx={{ color: theme.palette.text.disabled }}
                />
                <Typography
                  sx={{
                    mt: 2.5,
                    fontWeight: 600,
                    fontSize: '1.25rem',
                    color: theme.palette.text.primary,
                  }}
                >
                  No OAuth 2.0 applications
                </Typography>
                <Typography
                  sx={{
                    fontSize: '0.875rem',
                    color: theme.palette.text.secondary,
                    mt: 1.5,
                    maxWidth: 420,
                    mx: 'auto',
                  }}
                >
                  Create an OAuth 2.0 application to allow third-party integrations to access your
                  organization via Pipeshub.
                </Typography>
                <Button
                  variant="contained"
                  startIcon={<Iconify icon={plusIcon} width={20} height={20} />}
                  onClick={handleAddNew}
                  sx={{
                    mt: 3,
                    textTransform: 'none',
                    fontWeight: 600,
                    borderRadius: 1.5,
                    px: 3,
                    fontSize: '0.875rem',
                    py: 1.25,
                  }}
                >
                  New OAuth application
                </Button>
              </Box>
            </Fade>
          ) : (
            <>
              <Grid container spacing={2.5}>
                {apps.map((app) => (
                  <Grid item xs={12} sm={6} md={4} lg={3} key={app.id}>
                    <OAuth2AppCard app={app} onClick={() => handleOpenApp(app)} />
                  </Grid>
                ))}
              </Grid>

              {/* Infinite scroll sentinel */}
              <Box ref={sentinelRef} sx={{ height: 1 }} />

              {isLoadingMore && (
                <Fade in>
                  <Paper
                    elevation={0}
                    sx={{
                      py: 3,
                      px: 2,
                      mt: 2,
                      textAlign: 'center',
                      borderRadius: 2,
                      backgroundColor: alpha(theme.palette.primary.main, 0.04),
                      border: `1px solid ${alpha(theme.palette.primary.main, 0.1)}`,
                    }}
                  >
                    <Stack direction="row" alignItems="center" justifyContent="center" spacing={2}>
                      <CircularProgress size={24} thickness={4} />
                      <Typography
                        variant="body2"
                        sx={{ color: theme.palette.text.primary, fontWeight: 500 }}
                      >
                        Loading more applications...
                      </Typography>
                    </Stack>
                  </Paper>
                </Fade>
              )}

              {!hasMorePages && apps.length > ITEMS_PER_PAGE && (
                <Fade in>
                  <Paper
                    elevation={0}
                    sx={{
                      py: 2,
                      px: 2,
                      mt: 2,
                      textAlign: 'center',
                      borderRadius: 2,
                      backgroundColor: alpha(theme.palette.success.main, 0.04),
                      border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                    }}
                  >
                    <Stack direction="row" alignItems="center" justifyContent="center" spacing={1}>
                      <Iconify
                        icon={checkCircleIcon}
                        width={18}
                        height={18}
                        sx={{ color: theme.palette.success.main }}
                      />
                      <Typography
                        variant="body2"
                        sx={{ color: theme.palette.success.main, fontWeight: 600 }}
                      >
                        All applications loaded
                      </Typography>
                    </Stack>
                  </Paper>
                </Fade>
              )}
            </>
          )}
        </Box>
      </Box>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 8 }}
      >
        <Alert
          severity={snackbar.severity}
          variant="filled"
          onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
