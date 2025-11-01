import React, { useEffect, useMemo, useState } from 'react';
import {
  Paper,
  Container,
  Box,
  Typography,
  alpha,
  useTheme,
  Grid,
  InputAdornment,
  TextField,
  Skeleton,
  Alert,
  Snackbar,
  Button,
  Chip,
  Fade,
  Stack,
  Divider,
  IconButton,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import infoIcon from '@iconify-icons/mdi/info-circle';
import arrowRightIcon from '@iconify-icons/mdi/arrow-right';
import magniferIcon from '@iconify-icons/mdi/magnify';
import linkBrokenIcon from '@iconify-icons/mdi/link-off';
import linkIcon from '@iconify-icons/mdi/link-variant';
import listIcon from '@iconify-icons/mdi/format-list-bulleted';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import clockCircleIcon from '@iconify-icons/mdi/clock-outline';
import settingsIcon from '@iconify-icons/mdi/settings';
import clearIcon from '@iconify-icons/mdi/close-circle';
import { SnackbarState } from 'src/types/chat-sidebar';
import { ConnectorApiService } from './services/api';
import { Connector } from './types/types';
import ConnectorCard from './components/connector-card';

const Connectors = () => {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [filteredConnectors, setFilteredConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedFilter, setSelectedFilter] = useState<
    'all' | 'connected' | 'configured' | 'not-configured'
  >('all');
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  useEffect(() => {
    const fetchConnectors = async () => {
      try {
        const fetchedConnectors = await ConnectorApiService.getConnectors();
        setConnectors(fetchedConnectors);
        setFilteredConnectors(fetchedConnectors);
        setLoading(false);
      } catch (error) {
        setSnackbar({
          open: true,
          message: 'Failed to fetch connectors',
          severity: 'error',
        });
        setLoading(false);
      }
    };
    fetchConnectors();
  }, []);

  // Filter and search logic
  useEffect(() => {
    let filtered = connectors;

    // Apply search filter
    if (searchQuery) {
      filtered = filtered.filter(
        (connector) =>
          connector.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          connector.appGroup.toLowerCase().includes(searchQuery.toLowerCase())
      );
    }

    // Apply status filter
    if (selectedFilter !== 'all') {
      filtered = filtered.filter((connector) => {
        switch (selectedFilter) {
          case 'connected':
            return connector.isConfigured && connector.isActive;
          case 'configured':
            return connector.isConfigured && !connector.isActive;
          case 'not-configured':
            return !connector.isConfigured;
          default:
            return true;
        }
      });
    }

    setFilteredConnectors(filtered);
  }, [connectors, searchQuery, selectedFilter]);

  // Get filter counts
  const getFilterCounts = () => ({
    all: connectors.length,
    connected: connectors.filter((c) => c.isConfigured && c.isActive).length,
    configured: connectors.filter((c) => c.isConfigured && !c.isActive).length,
    'not-configured': connectors.filter((c) => !c.isConfigured).length,
  });

  const filterCounts = getFilterCounts();

  const filterOptions = [
    { key: 'all', label: 'All', count: filterCounts.all, icon: listIcon },
    { key: 'connected', label: 'Active', count: filterCounts.connected, icon: checkCircleIcon },
    {
      key: 'configured',
      label: 'Configured',
      count: filterCounts.configured,
      icon: clockCircleIcon,
    },
    {
      key: 'not-configured',
      label: 'Not Configured',
      count: filterCounts['not-configured'],
      icon: settingsIcon,
    },
  ];

  const loadingPlaceholders = useMemo(() => new Array(8).fill(null), []);

  const handleClearSearch = () => {
    setSearchQuery('');
  };

  const totalConnected = filterCounts.connected;
  const totalConfigured = filterCounts.configured + filterCounts.connected;

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
        }}
      >
        {/* Header Section */}
        <Box
          sx={{
            p: 3,
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDark
              ? alpha(theme.palette.background.default, 0.3)
              : alpha(theme.palette.grey[50], 0.5),
          }}
        >
          <Fade in={!loading} timeout={600}>
            <Stack spacing={2}>
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <Box
                  sx={{
                    width: 40,
                    height: 40,
                    borderRadius: 1.5,
                    backgroundColor: alpha(theme.palette.primary.main, 0.1),
                    border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Iconify
                    icon={linkIcon}
                    width={20}
                    height={20}
                    sx={{ color: theme.palette.primary.main }}
                  />
                </Box>
                <Box>
                  <Typography
                    variant="h5"
                    sx={{
                      fontWeight: 700,
                      fontSize: '1.5rem',
                      color: theme.palette.text.primary,
                      mb: 0.5,
                    }}
                  >
                    Data Connectors
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{
                      color: theme.palette.text.secondary,
                      fontSize: '0.875rem',
                    }}
                  >
                    Connect and manage integrations with external services
                    {totalConnected > 0 && (
                      <Chip
                        label={`${totalConnected} active`}
                        size="small"
                        sx={{
                          ml: 1,
                          height: 20,
                          fontSize: '0.6875rem',
                          fontWeight: 600,
                          backgroundColor: isDark
                            ? alpha(theme.palette.success.main, 0.8)
                            : alpha(theme.palette.success.main, 0.1),
                          color: isDark
                            ? theme.palette.success.contrastText
                            : theme.palette.success.main,
                          border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                        }}
                      />
                    )}
                  </Typography>
                </Box>
              </Stack>
            </Stack>
          </Fade>
        </Box>

        {/* Content */}
        <Box sx={{ p: 3 }}>
          {loading ? (
            <Stack spacing={3}>
              {/* Loading Search Bar */}
              <Skeleton variant="rectangular" height={48} sx={{ borderRadius: 1.5 }} />

              {/* Loading Filter Buttons */}
              <Stack direction="row" spacing={1}>
                {[1, 2, 3, 4].map((i) => (
                  <Skeleton
                    key={i}
                    variant="rectangular"
                    width={100}
                    height={32}
                    sx={{ borderRadius: 1 }}
                  />
                ))}
              </Stack>

              {/* Loading Grid */}
              <Grid container spacing={3}>
                {loadingPlaceholders.map((_, idx) => (
                  <Grid item xs={12} sm={6} md={4} lg={3} key={idx}>
                    <Skeleton variant="rectangular" height={220} sx={{ borderRadius: 2 }} />
                  </Grid>
                ))}
              </Grid>
            </Stack>
          ) : (
            <Fade in timeout={800}>
              <Stack spacing={3}>
                {/* Search and Filters */}
                <Stack spacing={2}>
                  {/* Search Bar */}
                  <TextField
                    placeholder="Search connectors by name or category..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    size="small"
                    fullWidth
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
                      endAdornment: searchQuery && (
                        <InputAdornment position="end">
                          <IconButton
                            size="small"
                            onClick={handleClearSearch}
                            sx={{
                              color: theme.palette.text.secondary,
                              '&:hover': {
                                backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                              },
                            }}
                          >
                            <Iconify icon={clearIcon} width={16} height={16} />
                          </IconButton>
                        </InputAdornment>
                      ),
                    }}
                    sx={{
                      '& .MuiOutlinedInput-root': {
                        height: 48,
                        borderRadius: 1.5,
                        backgroundColor: isDark
                          ? alpha(theme.palette.background.default, 0.4)
                          : theme.palette.background.paper,
                        '&:hover': {
                          borderColor: alpha(theme.palette.primary.main, 0.4),
                        },
                      },
                    }}
                  />

                  {/* Filter Buttons */}
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                    <Typography
                      variant="body2"
                      sx={{
                        color: theme.palette.text.secondary,
                        fontWeight: 500,
                        mr: 1,
                      }}
                    >
                      Filter:
                    </Typography>
                    {filterOptions.map((option) => {
                      const isSelected = selectedFilter === option.key;
                      return (
                        <Button
                          key={option.key}
                          variant={isSelected ? 'contained' : 'outlined'}
                          size="small"
                          onClick={() => setSelectedFilter(option.key as any)}
                          startIcon={<Iconify icon={option.icon} width={16} height={16} />}
                          sx={{
                            textTransform: 'none',
                            borderRadius: 1.5,
                            fontWeight: 600,
                            fontSize: '0.8125rem',
                            height: 32,
                            ...(isSelected
                              ? {
                                  backgroundColor: theme.palette.primary.main,
                                  color: theme.palette.primary.contrastText,
                                  '&:hover': {
                                    backgroundColor: theme.palette.primary.dark,
                                  },
                                }
                              : {
                                  borderColor: theme.palette.divider,
                                  color: theme.palette.text.primary,
                                  backgroundColor: 'transparent',
                                  '&:hover': {
                                    borderColor: theme.palette.primary.main,
                                    backgroundColor: alpha(theme.palette.primary.main, 0.04),
                                  },
                                }),
                          }}
                        >
                          {option.label}
                          {option.count > 0 && (
                            <Chip
                              label={option.count}
                              size="small"
                              sx={{
                                ml: 1,
                                height: 18,
                                fontSize: '0.6875rem',
                                fontWeight: 700,
                                '& .MuiChip-label': {
                                  px: 0.75,
                                },
                                ...(isSelected
                                  ? {
                                      backgroundColor: alpha(
                                        theme.palette.primary.contrastText,
                                        0.2
                                      ),
                                      color: theme.palette.primary.contrastText,
                                    }
                                  : {
                                      backgroundColor: isDark
                                        ? alpha(theme.palette.primary.main, 0.8)
                                        : alpha(theme.palette.primary.main, 0.1),
                                      color: theme.palette.primary.main,
                                    }),
                              }}
                            />
                          )}
                        </Button>
                      );
                    })}

                    {searchQuery && (
                      <>
                        <Divider orientation="vertical" sx={{ height: 24, mx: 1 }} />
                        <Typography
                          variant="caption"
                          sx={{
                            color: theme.palette.text.secondary,
                            fontWeight: 500,
                          }}
                        >
                          {filteredConnectors.length} result
                          {filteredConnectors.length !== 1 ? 's' : ''}
                        </Typography>
                      </>
                    )}
                  </Stack>
                </Stack>

                {/* Empty State */}
                {filteredConnectors.length === 0 && (
                  <Paper
                    elevation={0}
                    sx={{
                      py: 6,
                      px: 4,
                      textAlign: 'center',
                      borderRadius: 2,
                      border: `1px solid ${theme.palette.divider}`,
                      backgroundColor: isDark
                        ? alpha(theme.palette.background.default, 0.2)
                        : alpha(theme.palette.grey[50], 0.5),
                    }}
                  >
                    <Box
                      sx={{
                        width: 80,
                        height: 80,
                        borderRadius: 2,
                        backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        mx: 'auto',
                        mb: 3,
                      }}
                    >
                      <Iconify
                        icon={searchQuery ? magniferIcon : linkBrokenIcon}
                        width={32}
                        height={32}
                        sx={{ color: theme.palette.text.disabled }}
                      />
                    </Box>
                    <Typography
                      variant="h6"
                      sx={{
                        mb: 1,
                        fontWeight: 600,
                        color: theme.palette.text.primary,
                      }}
                    >
                      {searchQuery ? 'No connectors found' : 'No connectors available'}
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{
                        color: theme.palette.text.secondary,
                        maxWidth: 400,
                        mx: 'auto',
                      }}
                    >
                      {searchQuery ? (
                        <>
                          No connectors match &quot;{searchQuery}&quot;. Try adjusting your search
                          terms or{' '}
                          <Button
                            variant="text"
                            size="small"
                            onClick={handleClearSearch}
                            sx={{
                              textTransform: 'none',
                              p: 0,
                              minWidth: 'auto',
                              fontWeight: 600,
                            }}
                          >
                            clear the search
                          </Button>
                        </>
                      ) : (
                        'No connectors match the selected filter. Try selecting a different filter.'
                      )}
                    </Typography>
                  </Paper>
                )}

                {/* Connectors Grid */}
                {filteredConnectors.length > 0 && (
                  <Stack spacing={2}>
                    <Typography
                      variant="h6"
                      sx={{
                        fontWeight: 600,
                        fontSize: '1.125rem',
                        color: theme.palette.text.primary,
                      }}
                    >
                      {searchQuery
                        ? `Search Results (${filteredConnectors.length})`
                        : selectedFilter === 'all'
                          ? `All Connectors (${filteredConnectors.length})`
                          : selectedFilter === 'connected'
                            ? `Active Connectors (${filteredConnectors.length})`
                            : selectedFilter === 'configured'
                              ? `Ready Connectors (${filteredConnectors.length})`
                              : `Setup Required (${filteredConnectors.length})`}
                    </Typography>

                    <Grid container spacing={2.5}>
                      {filteredConnectors.map((connector) => (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={connector._key}>
                          <ConnectorCard connector={connector} />
                        </Grid>
                      ))}
                    </Grid>
                  </Stack>
                )}

                {/* Info Alert */}
                {!loading && connectors.length > 0 && (
                  <Alert
                    variant="outlined"
                    severity="info"
                    icon={<Iconify icon={infoIcon} width={20} height={20} />}
                    sx={{
                      borderRadius: 1.5,
                      borderColor: alpha(theme.palette.info.main, 0.2),
                      backgroundColor: isDark
                        ? alpha(theme.palette.info.main, 0.04)
                        : alpha(theme.palette.info.main, 0.04),
                      '& .MuiAlert-icon': {
                        display: 'none',
                      },
                      '& .MuiAlert-message': {
                        width: '100%',
                      },
                    }}
                  >
                    <Stack direction="row" justifyContent="space-between" alignItems="center">
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        Click any connector to configure settings and start syncing data
                        automatically. Refer to{' '}
                        <a
                          href='https://docs.pipeshub.com/connectors/overview'
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ color: theme.palette.primary.main }}
                        >
                          the documentation
                        </a>{' '}
                        for more information.
                      </Typography>
                    </Stack>
                  </Alert>
                )}
              </Stack>
            </Fade>
          )}
        </Box>
      </Box>

      {/* Snackbar */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 8 }}
      >
        <Alert
          onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
          severity={snackbar.severity}
          variant="filled"
          sx={{
            borderRadius: 1.5,
            fontWeight: 600,
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default Connectors;
