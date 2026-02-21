import React, { useCallback, useEffect, useState } from 'react';
import {
  Container,
  Box,
  alpha,
  useTheme,
  Button,
  Stack,
  Typography,
  TextField,
  InputAdornment,
  IconButton,
  Skeleton,
  Fade,
  Alert,
  Snackbar,
  Grid,
} from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import keyLinkIcon from '@iconify-icons/mdi/key-link';
import plusIcon from '@iconify-icons/mdi/plus';
import magniferIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import { OAuth2Api, type OAuth2App } from './services/oauth2-api';
import { OAuth2AppCard } from './oauth2-app-card';

const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 400;

export function OAuth2ListView() {
  const theme = useTheme();
  const navigate = useNavigate();
  const isDark = theme.palette.mode === 'dark';

  const [apps, setApps] = useState<OAuth2App[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [searchActive, setSearchActive] = useState('');
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' | 'info' }>({
    open: false,
    message: '',
    severity: 'success',
  });
  const [pagination, setPagination] = useState({ page: 1, total: 0, totalPages: 0 });

  const loadApps = useCallback(async (page: number, searchQuery?: string) => {
    setLoading(true);
    try {
      const result = await OAuth2Api.listApps({
        page,
        limit: ITEMS_PER_PAGE,
        search: searchQuery || undefined,
      });
      setApps(result.data || []);
      setPagination({
        page: result.pagination?.page ?? 1,
        total: result.pagination?.total ?? 0,
        totalPages: result.pagination?.totalPages ?? 0,
      });
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || 'Failed to load OAuth 2.0 apps';
      setSnackbar({ open: true, message: msg, severity: 'error' });
      setApps([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setSearchActive(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    loadApps(1, searchActive || undefined);
  }, [loadApps, searchActive]);

  const handleOpenApp = (app: OAuth2App) => {
    navigate(`${app.id}`);
  };

  const handleAddNew = () => {
    navigate('new');
  };

  return (
    <Container maxWidth="xl" sx={{ py: 3, px: 3 }}>
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
            backgroundColor: isDark ? alpha(theme.palette.background.default, 0.4) : alpha(theme.palette.grey[50], 0.6),
          }}
        >
          <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2}>
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
                <Iconify icon={keyLinkIcon} width={22} height={22} sx={{ color: theme.palette.primary.main }} />
              </Box>
              <Box>
                <Typography variant="h5" sx={{ fontWeight: 700, color: theme.palette.text.primary }}>
                  OAuth 2.0
                </Typography>
                <Typography variant="body2" sx={{ color: theme.palette.text.secondary, mt: 0.25 }}>
                  Manage OAuth 2.0 applications that can access your organization via Pipeshub
                </Typography>
              </Box>
            </Stack>
            <Button
              variant="contained"
              color="primary"
              startIcon={<Iconify icon={plusIcon} width={18} height={18} />}
              onClick={handleAddNew}
              sx={{ textTransform: 'none', fontWeight: 600, borderRadius: 1.5, px: 3, py: 1.25 }}
            >
              New OAuth App
            </Button>
          </Stack>
        </Box>

        <Box sx={{ p: 3 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={2} sx={{ mb: 3 }}>
            <TextField
              placeholder="Search apps by name or description..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              size="small"
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={magniferIcon} width={20} height={20} sx={{ color: theme.palette.text.secondary }} />
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
              }}
            />
            {!loading && apps.length > 0 && (
              <Typography variant="body2" sx={{ color: theme.palette.text.secondary }}>
                {pagination.total} {pagination.total === 1 ? 'app' : 'apps'}
              </Typography>
            )}
          </Stack>

          {loading ? (
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
                  py: 10,
                  px: 3,
                  textAlign: 'center',
                  borderRadius: 2,
                  border: `1px dashed ${theme.palette.divider}`,
                  backgroundColor: alpha(theme.palette.grey[500], 0.04),
                }}
              >
                <Iconify icon="mdi:application-cog-outline" width={64} height={64} sx={{ color: theme.palette.text.disabled }} />
                <Typography variant="h6" sx={{ mt: 2, fontWeight: 600, color: theme.palette.text.primary }}>
                  No OAuth 2.0 apps yet
                </Typography>
                <Typography variant="body2" sx={{ color: theme.palette.text.secondary, mt: 1, maxWidth: 420, mx: 'auto' }}>
                  Create an OAuth 2.0 app to allow third-party applications to access your organization via Pipeshub.
                </Typography>
                <Button
                  variant="contained"
                  startIcon={<Iconify icon={plusIcon} width={20} height={20} />}
                  onClick={handleAddNew}
                  sx={{ mt: 3, textTransform: 'none', fontWeight: 600, borderRadius: 1.5, px: 3 }}
                >
                  New OAuth App
                </Button>
              </Box>
            </Fade>
          ) : (
            <Grid container spacing={2.5}>
              {apps.map((app) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={app.id}>
                  <OAuth2AppCard app={app} onClick={() => handleOpenApp(app)} />
                </Grid>
              ))}
            </Grid>
          )}
        </Box>
      </Box>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert severity={snackbar.severity} variant="filled" onClose={() => setSnackbar((s) => ({ ...s, open: false }))}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
