/**
 * Connector Registry Page
 *
 * Page for browsing available connector types and creating new instances.
 * Shows all connectors from the registry that can be configured.
 */

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
  IconButton,
} from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import appsIcon from '@iconify-icons/mdi/apps';
import magniferIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import { SnackbarState } from 'src/types/chat-sidebar';
import { useAccountType } from 'src/hooks/use-account-type';
import { ConnectorApiService } from '../services/api';
import { ConnectorRegistry as ConnectorRegistryType } from '../types/types';
import ConnectorRegistryCard from '../components/connector-registry-card';

const ConnectorRegistry = () => {
  const [connectors, setConnectors] = useState<ConnectorRegistryType[]>([]);
  const [filteredConnectors, setFilteredConnectors] = useState<ConnectorRegistryType[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: 'success',
  });

  const theme = useTheme();
  const navigate = useNavigate();
  const isDark = theme.palette.mode === 'dark';
  const { isBusiness } = useAccountType();
  // Fetch connector registry
  useEffect(() => {
    const fetchRegistry = async () => {
      try {
        setLoading(true);
        const registry = await ConnectorApiService.getConnectorRegistry();
        setConnectors(registry);
        setFilteredConnectors(registry);
      } catch (error) {
        console.error('Error fetching connector registry:', error);
        setSnackbar({
          open: true,
          message: 'Failed to fetch connector registry',
          severity: 'error',
        });
      } finally {
        setLoading(false);
      }
    };
    fetchRegistry();
  }, []);

  // Get unique categories
  const categories = useMemo(() => {
    const allCategories = new Set<string>();
    connectors.forEach((connector) => {
      connector.appCategories?.forEach((category) => allCategories.add(category));
    });
    return ['all', ...Array.from(allCategories).sort()];
  }, [connectors]);

  // Filter logic
  useEffect(() => {
    let filtered = connectors;

    // Apply search filter
    if (searchQuery) {
      filtered = filtered.filter(
        (connector) =>
          connector.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          connector.appGroup.toLowerCase().includes(searchQuery.toLowerCase()) ||
          connector.appDescription.toLowerCase().includes(searchQuery.toLowerCase())
      );
    }

    // Apply category filter
    if (selectedCategory !== 'all') {
      filtered = filtered.filter((connector) =>
        connector.appCategories?.includes(selectedCategory)
      );
    }

    setFilteredConnectors(filtered);
  }, [connectors, searchQuery, selectedCategory]);

  const loadingPlaceholders = useMemo(() => new Array(12).fill(null), []);

  const handleClearSearch = () => {
    setSearchQuery('');
  };

  const handleBackToInstances = () => {
    if (isBusiness) {
      navigate('/account/company-settings/settings/connector');
    } else {
      navigate('/account/individual/settings/connector');
    }
  };

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
              <Stack direction="row" alignItems="center" justifyContent="space-between">
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
                      icon={appsIcon}
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
                      Available Connectors
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{
                        color: theme.palette.text.secondary,
                        fontSize: '0.875rem',
                      }}
                    >
                      Browse and configure new connector instances
                    </Typography>
                  </Box>
                </Stack>

                {/* Back Button */}
                <Button
                  variant="outlined"
                  startIcon={<Iconify icon={arrowLeftIcon} width={18} height={18} />}
                  onClick={handleBackToInstances}
                  sx={{
                    textTransform: 'none',
                    fontWeight: 600,
                    borderRadius: 1.5,
                    px: 3,
                    height: 40,
                  }}
                >
                  Back to My Connectors
                </Button>
              </Stack>
            </Stack>
          </Fade>
        </Box>

        {/* Content */}
        <Box sx={{ p: 3 }}>
          {loading ? (
            <Stack spacing={3}>
              <Skeleton variant="rectangular" height={48} sx={{ borderRadius: 1.5 }} />
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {[1, 2, 3, 4, 5].map((i) => (
                  <Skeleton
                    key={i}
                    variant="rectangular"
                    width={80}
                    height={32}
                    sx={{ borderRadius: 1 }}
                  />
                ))}
              </Stack>
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
                {/* Search and Categories */}
                <Stack spacing={2}>
                  {/* Search Bar */}
                  <TextField
                    placeholder="Search connectors by name, category, or description..."
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

                  {/* Category Chips */}
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                    <Typography
                      variant="body2"
                      sx={{
                        color: theme.palette.text.secondary,
                        fontWeight: 500,
                        mr: 1,
                      }}
                    >
                      Category:
                    </Typography>
                    {categories.map((category) => (
                      <Chip
                        key={category}
                        label={category === 'all' ? 'All' : category}
                        onClick={() => setSelectedCategory(category)}
                        sx={{
                          textTransform: 'capitalize',
                          fontWeight: 600,
                          fontSize: '0.8125rem',
                          cursor: 'pointer',
                          ...(selectedCategory === category
                            ? {
                                backgroundColor: theme.palette.primary.main,
                                color: theme.palette.primary.contrastText,
                                '&:hover': {
                                  backgroundColor: theme.palette.primary.dark,
                                },
                              }
                            : {
                                backgroundColor: 'transparent',
                                borderColor: theme.palette.divider,
                                color: theme.palette.text.primary,
                                '&:hover': {
                                  borderColor: theme.palette.primary.main,
                                  backgroundColor: alpha(theme.palette.primary.main, 0.04),
                                },
                              }),
                        }}
                        variant={selectedCategory === category ? 'filled' : 'outlined'}
                      />
                    ))}
                  </Stack>
                </Stack>

                {/* Connectors Grid */}
                {filteredConnectors.length > 0 ? (
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
                        : selectedCategory === 'all'
                          ? `All Connectors (${filteredConnectors.length})`
                          : `${selectedCategory} (${filteredConnectors.length})`}
                    </Typography>

                    <Grid container spacing={2.5}>
                      {filteredConnectors.map((connector) => (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={connector.type}>
                          <ConnectorRegistryCard connector={connector} />
                        </Grid>
                      ))}
                    </Grid>
                  </Stack>
                ) : (
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
                    <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
                      No connectors found
                    </Typography>
                    <Typography variant="body2" sx={{ color: theme.palette.text.secondary }}>
                      Try adjusting your search or category filter
                    </Typography>
                  </Paper>
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
          sx={{ borderRadius: 1.5, fontWeight: 600 }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default ConnectorRegistry;
