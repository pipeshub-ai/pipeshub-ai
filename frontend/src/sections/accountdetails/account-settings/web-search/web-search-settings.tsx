import { useState, useEffect, useMemo } from 'react';
import {
  Box,
  Container,
  Typography,
  Alert,
  Snackbar,
  Divider,
  Button,
  Card,
  CardContent,
  Avatar,
  Grid,
  Tooltip,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  FormControlLabel,
  RadioGroup,
  Radio,
  Stack,
  useTheme,
  alpha,
  IconButton,
  CircularProgress,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import deleteIcon from '@iconify-icons/mdi/delete';
import closeIcon from '@iconify-icons/mdi/close';
import searchIcon from '@iconify-icons/mdi/magnify';
import starIcon from '@iconify-icons/mdi/star';
import pencilIcon from '@iconify-icons/mdi/pencil';
import { webSearchService } from './services/web-search-config';
import {
  AVAILABLE_WEB_SEARCH_PROVIDERS,
  ConfiguredWebSearchProvider,
  DUCKDUCKGO_PROVIDER_ID,
  WebSearchProvider,
  WebSearchProviderData,
  WebSearchSettings as WebSearchSettingsConfig,
} from './types';

/** Providers that can be added by the user (excludes built-in DuckDuckGo) */
const ADDABLE_WEB_SEARCH_PROVIDERS = AVAILABLE_WEB_SEARCH_PROVIDERS.filter(
  (p) => p.id !== DUCKDUCKGO_PROVIDER_ID
);

function WebSearchSettings() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [configuredProviders, setConfiguredProviders] = useState<ConfiguredWebSearchProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<WebSearchProvider | null>(null);
  const [editingProvider, setEditingProvider] = useState<ConfiguredWebSearchProvider | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [providerToDelete, setProviderToDelete] = useState<ConfiguredWebSearchProvider | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [settingDefaultKey, setSettingDefaultKey] = useState<string | null>(null);
  const [isSavingProvider, setIsSavingProvider] = useState(false);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [webSearchSettings, setWebSearchSettings] = useState<WebSearchSettingsConfig>({
    includeImages: false,
    maxImages: 3,
  });
  const [maxImagesInput, setMaxImagesInput] = useState('3');

  // Load configured providers
  const loadConfiguredProviders = async () => {
    setIsLoading(true);
    try {
      const config = await webSearchService.getConfig();
      setConfiguredProviders(config.providers);
      setWebSearchSettings(config.settings);
      setMaxImagesInput(String(config.settings.maxImages ?? ''));
      setError(null);
    } catch (err: any) {
      console.error('Error loading providers:', err);
      setError(err.message || 'Failed to load configured providers');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadConfiguredProviders();
  }, []);

  // DuckDuckGo is always shown in configured providers (built-in, not from API)
  const displayConfiguredProviders = useMemo(() => {
    const hasDuckDuckGo = configuredProviders.some(
      (p) => p.provider === DUCKDUCKGO_PROVIDER_ID
    );
    if (hasDuckDuckGo) return configuredProviders;
    const duckDuckGoEntry: ConfiguredWebSearchProvider = {
      providerKey: DUCKDUCKGO_PROVIDER_ID,
      provider: DUCKDUCKGO_PROVIDER_ID,
      configuration: {},
      isDefault: configuredProviders.length === 0 || !configuredProviders.some((p) => p.isDefault),
    };
    return [duckDuckGoEntry, ...configuredProviders];
  }, [configuredProviders]);

  const handleProviderSelect = (provider: WebSearchProvider) => {
    setSelectedProvider(provider);
    setEditingProvider(null);
    setFormData({});
    setDialogOpen(true);
  };

  const handleEdit = (configured: ConfiguredWebSearchProvider) => {
    const provider = AVAILABLE_WEB_SEARCH_PROVIDERS.find((p) => p.id === configured.provider);
    if (provider) {
      setSelectedProvider(provider);
      setEditingProvider(configured);
      setFormData(configured.configuration);
      setDialogOpen(true);
    }
  };

  const handleDialogClose = () => {
    setDialogOpen(false);
    setSelectedProvider(null);
    setEditingProvider(null);
    setFormData({});
    setIsSavingProvider(false);
  };

  const handleSave = async () => {
    if (!selectedProvider || isSavingProvider) return;

    setIsSavingProvider(true);
    try {
      const providerData: WebSearchProviderData = {
        provider: selectedProvider.id,
        configuration: formData,
        isDefault: configuredProviders.length === 0 || editingProvider?.isDefault || false,
      };

      if (editingProvider) {
        await webSearchService.updateProvider(editingProvider.providerKey, providerData);
        setSuccess(`${selectedProvider.name} updated successfully`);
      } else {
        await webSearchService.addProvider(providerData);
        setSuccess(`${selectedProvider.name} added successfully`);
      }

      handleDialogClose();
      loadConfiguredProviders();
    } catch (err: any) {
      setError(err.message || 'Failed to save provider');
    } finally {
      setIsSavingProvider(false);
    }
  };

  const handleDeleteClick = (configured: ConfiguredWebSearchProvider) => {
    setProviderToDelete(configured);
    setDeleteDialogOpen(true);
  };

  const handleDeleteConfirm = async () => {
    if (!providerToDelete) return;
    setIsDeleting(true);
    try {
      const wasDefault = providerToDelete.isDefault;
      await webSearchService.deleteProvider(providerToDelete.providerKey);
      if (wasDefault) {
        await webSearchService.setDefaultProvider(DUCKDUCKGO_PROVIDER_ID);
      }
      setSuccess('Provider deleted successfully');
      setDeleteDialogOpen(false);
      setProviderToDelete(null);
      loadConfiguredProviders();
    } catch (err: any) {
      setError(err.message || 'Failed to delete provider');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleSetDefault = async (providerKey: string) => {
    setSettingDefaultKey(providerKey);
    try {
      await webSearchService.setDefaultProvider(providerKey);
      setSuccess('Default provider updated successfully');
      loadConfiguredProviders();
    } catch (err: any) {
      setError(err.message || 'Failed to set default provider');
    } finally {
      setSettingDefaultKey(null);
    }
  };

  const handleSaveWebSearchSettings = async () => {
    const maxImagesText = maxImagesInput.trim();
    const parsedMaxImages = Number(maxImagesText);
    if (
      webSearchSettings.includeImages &&
      (maxImagesText === '' ||
        !Number.isInteger(parsedMaxImages) ||
        parsedMaxImages < 1 ||
        parsedMaxImages > 500)
    ) {
      setError('Max images must be an integer between 1 and 500');
      return;
    }
    const maxImages = maxImagesText === '' ? webSearchSettings.maxImages : parsedMaxImages;

    setIsSavingSettings(true);
    try {
      const updatedSettings = await webSearchService.updateSettings({
        includeImages: webSearchSettings.includeImages,
        maxImages,
      });
      setWebSearchSettings(updatedSettings);
      setMaxImagesInput(String(updatedSettings.maxImages));
      setSuccess('Web search image settings updated successfully');
    } catch (err: any) {
      setError(err.message || 'Failed to update web search image settings');
    } finally {
      setIsSavingSettings(false);
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
                icon={searchIcon}
                width={20}
                height={20}
                sx={{ color: theme.palette.primary.main }}
              />
            </Box>
            <Box>
              <Typography
                variant="h5"
                sx={{ fontWeight: 700, fontSize: '1.5rem', color: theme.palette.text.primary }}
              >
                Web Search Configuration
              </Typography>
              <Typography
                variant="body2"
                sx={{ color: theme.palette.text.secondary, fontSize: '0.875rem' }}
              >
                Configure web search providers for the chatbot to use when searching the web.
              </Typography>
            </Box>
          </Stack>
        </Box>

        {/* Content Section */}
        <Box sx={{ p: 3 }}>
          <Stack spacing={3}>

            {/* Image Settings Panel */}
            <Box
              sx={{
                borderRadius: 2,
                border: `1px solid ${theme.palette.divider}`,
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  px: 2.5,
                  py: 1.5,
                  borderBottom: `1px solid ${theme.palette.divider}`,
                  backgroundColor: isDark
                    ? alpha(theme.palette.background.default, 0.3)
                    : alpha(theme.palette.grey[50], 0.5),
                }}
              >
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                  Image Settings
                </Typography>
                <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                  Control whether images are sent to the LLM during web search
                </Typography>
              </Box>
              <Box sx={{ p: 2.5 }}>
                <Stack spacing={2}>
                  <FormControl component="fieldset">
                    <RadioGroup
                      value={webSearchSettings.includeImages ? 'include' : 'exclude'}
                      onChange={(event) =>
                        setWebSearchSettings({
                          ...webSearchSettings,
                          includeImages: event.target.value === 'include',
                        })
                      }
                    >
                      <FormControlLabel
                        value="include"
                        control={<Radio size="small" />}
                        label={
                          <Typography variant="body2">Send images to LLM</Typography>
                        }
                      />
                      <FormControlLabel
                        value="exclude"
                        control={<Radio size="small" />}
                        label={
                          <Typography variant="body2">Do not send images to LLM</Typography>
                        }
                      />
                    </RadioGroup>
                  </FormControl>

                  {webSearchSettings.includeImages && (
                    <>
                      <TextField
                        label="Maximum input images per LLM call"
                        type="number"
                        value={maxImagesInput}
                        onChange={(event) => setMaxImagesInput(event.target.value)}
                        inputProps={{ min: 1, max: 500, step: 1 }}
                        helperText="Enter a whole number between 1 and 500"
                        size="small"
                        sx={{ maxWidth: 320 }}
                      />
                      <Alert severity="warning" sx={{ borderRadius: 1.5 }}>
                        Including images may increase cost and add latency to web search queries.
                      </Alert>
                    </>
                  )}

                  <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <Button
                      variant="contained"
                      size="small"
                      onClick={handleSaveWebSearchSettings}
                      disabled={isLoading || isSavingSettings}
                      sx={{
                        textTransform: 'none',
                        fontWeight: 600,
                        borderRadius: 1.5,
                        px: 3,
                        height: 36,
                      }}
                    >
                      {isSavingSettings ? 'Saving...' : 'Save'}
                    </Button>
                  </Box>
                </Stack>
              </Box>
            </Box>

            {/* Configured Providers Panel */}
            <Box
              sx={{
                borderRadius: 2,
                border: `1px solid ${theme.palette.divider}`,
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  px: 2.5,
                  py: 1.5,
                  borderBottom: `1px solid ${theme.palette.divider}`,
                  backgroundColor: isDark
                    ? alpha(theme.palette.background.default, 0.3)
                    : alpha(theme.palette.grey[50], 0.5),
                }}
              >
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                  Configured Providers
                </Typography>
                <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                  Manage your active web search provider integrations
                </Typography>
              </Box>
              <Box sx={{ p: 2.5 }}>
                {isLoading ? (
                  <Typography variant="body2" color="text.secondary">
                    Loading...
                  </Typography>
                ) : displayConfiguredProviders.length === 0 ? (
                  <Alert severity="info" sx={{ borderRadius: 1.5 }}>
                    No providers configured yet. Add a provider below to get started.
                  </Alert>
                ) : (
                  <Grid container spacing={2.5}>
                    {displayConfiguredProviders.map((configured) => {
                      const provider = AVAILABLE_WEB_SEARCH_PROVIDERS.find(
                        (p) => p.id === configured.provider
                      );
                      const isDuckDuckGo = configured.provider === DUCKDUCKGO_PROVIDER_ID;
                      return (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={configured.providerKey}>
                          <Card
                            elevation={0}
                            sx={{
                              height: '100%',
                              display: 'flex',
                              flexDirection: 'column',
                              borderRadius: 2,
                              border: configured.isDefault
                                ? `1px solid ${alpha(theme.palette.primary.main, 0.5)}`
                                : `1px solid ${theme.palette.divider}`,
                              backgroundColor: theme.palette.background.paper,
                              cursor: 'pointer',
                              position: 'relative',
                              transition: theme.transitions.create(
                                ['transform', 'box-shadow', 'border-color'],
                                {
                                  duration: theme.transitions.duration.shorter,
                                  easing: theme.transitions.easing.easeOut,
                                }
                              ),
                              '&:hover': {
                                transform: 'translateY(-2px)',
                                borderColor: alpha(theme.palette.primary.main, 0.5),
                                boxShadow: isDark
                                  ? `0 8px 32px ${alpha('#000', 0.3)}`
                                  : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`,
                                '& .provider-avatar': { transform: 'scale(1.05)' },
                              },
                            }}
                          >
                            {/* Top-right status dot */}
                            <Box
                              sx={{
                                position: 'absolute',
                                top: 12,
                                right: 12,
                                width: 6,
                                height: 6,
                                borderRadius: '50%',
                                backgroundColor: theme.palette.success.main,
                                boxShadow: `0 0 0 2px ${theme.palette.background.paper}`,
                              }}
                            />

                            <CardContent
                              sx={{
                                p: 2,
                                display: 'flex',
                                flexDirection: 'column',
                                height: '100%',
                                gap: 1.5,
                                '&:last-child': { pb: 2 },
                              }}
                            >
                              {/* Avatar + name */}
                              <Stack spacing={1.5} alignItems="center">
                                <Avatar
                                  className="provider-avatar"
                                  sx={{
                                    width: 48,
                                    height: 48,
                                    backgroundColor: isDark
                                      ? alpha(theme.palette.common.white, 0.9)
                                      : alpha(theme.palette.grey[100], 0.8),
                                    border: `1px solid ${theme.palette.divider}`,
                                    transition: theme.transitions.create('transform'),
                                  }}
                                >
                                  <Iconify
                                    icon={searchIcon}
                                    width={24}
                                    height={24}
                                    sx={{ color: theme.palette.primary.main }}
                                  />
                                </Avatar>
                                <Box sx={{ textAlign: 'center', width: '100%' }}>
                                  <Typography
                                    variant="subtitle2"
                                    sx={{ fontWeight: 600, color: theme.palette.text.primary, mb: 0.25, lineHeight: 1.2 }}
                                  >
                                    {provider?.name || configured.provider}
                                  </Typography>
                                  <Typography
                                    variant="caption"
                                    sx={{ color: theme.palette.text.secondary, fontSize: '0.6875rem' }}
                                  >
                                    Web Search
                                  </Typography>
                                </Box>
                              </Stack>

                              {/* Default chip OR Set as Default button */}
                              <Box sx={{ display: 'flex', justifyContent: 'center' }}>
                                {configured.isDefault ? (
                                  <Chip
                                    icon={<Iconify icon={starIcon} width={14} height={14} />}
                                    label="Default"
                                    size="small"
                                    sx={{
                                      height: 24,
                                      fontSize: '0.75rem',
                                      fontWeight: 500,
                                      backgroundColor: isDark
                                        ? alpha(theme.palette.primary.main, 0.8)
                                        : alpha(theme.palette.primary.main, 0.1),
                                      color: isDark ? theme.palette.background.paper : theme.palette.primary.main,
                                      border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                                      '& .MuiChip-icon': { color: isDark ? theme.palette.background.paper : theme.palette.primary.main },
                                    }}
                                  />
                                ) : (
                                  <Button
                                    fullWidth
                                    variant="outlined"
                                    size="small"
                                    startIcon={
                                      settingDefaultKey === configured.providerKey ? (
                                        <CircularProgress size={14} color="inherit" />
                                      ) : (
                                        <Iconify icon={starIcon} width={14} height={14} />
                                      )
                                    }
                                    onClick={() => handleSetDefault(configured.providerKey)}
                                    disabled={settingDefaultKey !== null}
                                    sx={{
                                      height: 30,
                                      borderRadius: 1.5,
                                      textTransform: 'none',
                                      fontWeight: 600,
                                      fontSize: '0.75rem',
                                      borderColor: alpha(theme.palette.primary.main, 0.3),
                                      '&:hover': { borderColor: theme.palette.primary.main, backgroundColor: alpha(theme.palette.primary.main, 0.04) },
                                    }}
                                  >
                                    Set as Default
                                  </Button>
                                )}
                              </Box>

                              {/* Bottom row */}
                              {isDuckDuckGo ? (
                                <Box
                                  sx={{
                                    mt: 'auto',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    gap: 0.75,
                                    px: 1.5,
                                    py: 1,
                                    borderRadius: 1,
                                    backgroundColor: isDark
                                      ? alpha(theme.palette.background.default, 0.3)
                                      : alpha(theme.palette.grey[50], 0.8),
                                    border: `1px solid ${theme.palette.divider}`,
                                  }}
                                >
                                  <Box sx={{ width: 4, height: 4, borderRadius: '50%', flexShrink: 0, backgroundColor: theme.palette.text.disabled }} />
                                  <Typography variant="caption" sx={{ fontSize: '0.75rem', fontWeight: 500, color: theme.palette.text.secondary }}>
                                    System provided
                                  </Typography>
                                </Box>
                              ) : (
                                <Stack direction="row" spacing={0.75} sx={{ mt: 'auto' }}>
                                  <Button
                                    fullWidth
                                    variant="outlined"
                                    size="small"
                                    startIcon={<Iconify icon={pencilIcon} width={15} height={15} />}
                                    onClick={() => handleEdit(configured)}
                                    sx={{
                                      height: 38,
                                      borderRadius: 1.5,
                                      textTransform: 'none',
                                      fontWeight: 600,
                                      fontSize: '0.8125rem',
                                      borderColor: alpha(theme.palette.primary.main, 0.3),
                                      '&:hover': { borderColor: theme.palette.primary.main, backgroundColor: alpha(theme.palette.primary.main, 0.04) },
                                    }}
                                  >
                                    Edit
                                  </Button>
                                  <Tooltip title="Delete" arrow>
                                    <IconButton
                                      size="small"
                                      onClick={() => handleDeleteClick(configured)}
                                      sx={{
                                        width: 38,
                                        height: 38,
                                        flexShrink: 0,
                                        borderRadius: 1.5,
                                        border: `1px solid ${alpha(theme.palette.error.main, 0.3)}`,
                                        color: theme.palette.error.main,
                                        '&:hover': { backgroundColor: alpha(theme.palette.error.main, 0.04), borderColor: theme.palette.error.main },
                                      }}
                                    >
                                      <Iconify icon={deleteIcon} width={16} height={16} />
                                    </IconButton>
                                  </Tooltip>
                                </Stack>
                              )}
                            </CardContent>
                          </Card>
                        </Grid>
                      );
                    })}
                  </Grid>
                )}
              </Box>
            </Box>

            {/* Available Providers Panel */}
            <Box
              sx={{
                borderRadius: 2,
                border: `1px solid ${theme.palette.divider}`,
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  px: 2.5,
                  py: 1.5,
                  borderBottom: `1px solid ${theme.palette.divider}`,
                  backgroundColor: isDark
                    ? alpha(theme.palette.background.default, 0.3)
                    : alpha(theme.palette.grey[50], 0.5),
                }}
              >
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                  Available Providers
                </Typography>
                <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                  Add a new web search provider to your configuration
                </Typography>
              </Box>
              <Box sx={{ p: 2.5 }}>
                <Grid container spacing={2.5}>
                  {ADDABLE_WEB_SEARCH_PROVIDERS.map((provider) => {
                    const isAlreadyConfigured = configuredProviders.some(
                      (c) => c.provider === provider.id
                    );
                    return (
                      <Grid item xs={12} sm={6} md={4} lg={3} key={provider.id}>
                        <Card
                          elevation={0}
                          sx={{
                            height: '100%',
                            display: 'flex',
                            flexDirection: 'column',
                            borderRadius: 2,
                            border: `1px solid ${theme.palette.divider}`,
                            backgroundColor: theme.palette.background.paper,
                            cursor: isAlreadyConfigured ? 'default' : 'pointer',
                            position: 'relative',
                            transition: theme.transitions.create(
                              ['transform', 'box-shadow', 'border-color'],
                              {
                                duration: theme.transitions.duration.shorter,
                                easing: theme.transitions.easing.easeOut,
                              }
                            ),
                            ...(!isAlreadyConfigured && {
                              '&:hover': {
                                transform: 'translateY(-2px)',
                                borderColor: alpha(theme.palette.primary.main, 0.5),
                                boxShadow: isDark
                                  ? `0 8px 32px ${alpha('#000', 0.3)}`
                                  : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`,
                                '& .provider-avatar': { transform: 'scale(1.05)' },
                              },
                            }),
                          }}
                          onClick={() => !isAlreadyConfigured && handleProviderSelect(provider)}
                        >
                          {/* Top-left badge */}
                          {provider.isPopular && (
                            <Box
                              sx={{
                                position: 'absolute',
                                top: 8,
                                left: 8,
                                px: 0.75,
                                py: 0.25,
                                borderRadius: 0.75,
                                fontSize: '0.6875rem',
                                fontWeight: 600,
                                color: theme.palette.success.main,
                                backgroundColor: alpha(theme.palette.success.main, 0.08),
                                border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                              }}
                            >
                              Popular
                            </Box>
                          )}

                          <CardContent
                            sx={{
                              p: 2,
                              display: 'flex',
                              flexDirection: 'column',
                              height: '100%',
                              gap: 1.5,
                              '&:last-child': { pb: 2 },
                            }}
                          >
                            <Stack spacing={1.5} alignItems="center">
                              <Avatar
                                className="provider-avatar"
                                sx={{
                                  width: 48,
                                  height: 48,
                                  backgroundColor: isDark
                                    ? alpha(theme.palette.common.white, 0.9)
                                    : alpha(theme.palette.grey[100], 0.8),
                                  border: `1px solid ${theme.palette.divider}`,
                                  transition: theme.transitions.create('transform'),
                                }}
                              >
                                <Iconify
                                  icon={searchIcon}
                                  width={24}
                                  height={24}
                                  sx={{ color: theme.palette.primary.main }}
                                />
                              </Avatar>
                              <Box sx={{ textAlign: 'center', width: '100%' }}>
                                <Typography
                                  variant="subtitle2"
                                  sx={{ fontWeight: 600, color: theme.palette.text.primary, mb: 0.25, lineHeight: 1.2 }}
                                >
                                  {provider.name}
                                </Typography>
                                <Typography
                                  variant="caption"
                                  sx={{ color: theme.palette.text.secondary, fontSize: '0.6875rem' }}
                                >
                                  Web Search
                                </Typography>
                              </Box>
                            </Stack>

                            {/* Description */}
                            <Typography
                              variant="caption"
                              sx={{ color: theme.palette.text.secondary, fontSize: '0.75rem', textAlign: 'center', display: 'block' }}
                            >
                              {provider.description}
                            </Typography>

                            <Button
                              fullWidth
                              variant="outlined"
                              size="small"
                              disabled={isAlreadyConfigured}
                              sx={{
                                mt: 'auto',
                                height: 38,
                                borderRadius: 1.5,
                                textTransform: 'none',
                                fontWeight: 600,
                                fontSize: '0.8125rem',
                                ...(!isAlreadyConfigured && {
                                  borderColor: alpha(theme.palette.primary.main, 0.3),
                                  '&:hover': {
                                    borderColor: theme.palette.primary.main,
                                    backgroundColor: alpha(theme.palette.primary.main, 0.04),
                                  },
                                }),
                              }}
                            >
                              {isAlreadyConfigured ? 'Already Configured' : 'Configure'}
                            </Button>
                          </CardContent>
                        </Card>
                      </Grid>
                    );
                  })}
                </Grid>
              </Box>
            </Box>

          </Stack>
        </Box>
      </Box>

        {/* Configuration Dialog */}
        <Dialog open={dialogOpen} onClose={handleDialogClose} maxWidth="sm" fullWidth>
          <DialogTitle>
            {editingProvider ? 'Edit' : 'Configure'} {selectedProvider?.name}
          </DialogTitle>
          <DialogContent>
            <Box sx={{ pt: 2 }}>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                {selectedProvider?.description}
              </Typography>

              <Stack spacing={2}>
                {selectedProvider?.requiredFields.map((field) => (
                  <TextField
                    key={field.name}
                    label={field.label}
                    type={field.type}
                    fullWidth
                    required
                    value={formData[field.name] || ''}
                    onChange={(e) =>
                      setFormData({ ...formData, [field.name]: e.target.value })
                    }
                    placeholder={field.placeholder}
                    helperText={field.description}
                  />
                ))}

                {selectedProvider?.optionalFields?.map((field) =>
                  field.type === 'select' ? (
                    <FormControl fullWidth key={field.name}>
                      <InputLabel>{field.label}</InputLabel>
                      <Select
                        value={formData[field.name] || ''}
                        onChange={(e) =>
                          setFormData({ ...formData, [field.name]: e.target.value })
                        }
                        label={field.label}
                      >
                        {field.options?.map((option) => (
                          <MenuItem key={option} value={option}>
                            {option}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  ) : (
                    <TextField
                      key={field.name}
                      label={field.label}
                      type={field.type}
                      fullWidth
                      value={formData[field.name] || ''}
                      onChange={(e) =>
                        setFormData({ ...formData, [field.name]: e.target.value })
                      }
                      placeholder={field.placeholder}
                      helperText={field.description}
                    />
                  )
                )}
              </Stack>
            </Box>
          </DialogContent>
          <DialogActions>
            <Button onClick={handleDialogClose}>Cancel</Button>
            <Button onClick={handleSave} variant="contained" color="primary" disabled={isSavingProvider}>
              {isSavingProvider ? 'Saving...' : editingProvider ? 'Update' : 'Add'}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Delete Provider Confirmation Dialog */}
        <Dialog
          open={deleteDialogOpen}
          onClose={() => !isDeleting && setDeleteDialogOpen(false)}
          maxWidth="sm"
          fullWidth
          PaperProps={{
            sx: {
              borderRadius: 1,
              maxHeight: '90vh',
              backgroundColor: theme.palette.background.paper,
            },
          }}
          BackdropProps={{
            sx: {
              backdropFilter: 'blur(1px)',
              backgroundColor: alpha(theme.palette.common.black, 0.3),
            },
          }}
        >
          <DialogTitle
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              pb: 1,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 1.5,
                  bgcolor: alpha(theme.palette.error.main, 0.1),
                }}
              >
                <Iconify
                  icon={deleteIcon}
                  width={22}
                  height={22}
                  sx={{ color: theme.palette.error.main }}
                />
              </Box>
              <Box>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Delete Provider
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Permanently remove this web search provider configuration
                </Typography>
              </Box>
            </Box>
            <IconButton
              onClick={() => !isDeleting && setDeleteDialogOpen(false)}
              size="small"
              disabled={isDeleting}
            >
              <Iconify icon={closeIcon} width={20} height={20} />
            </IconButton>
          </DialogTitle>
          <Divider />
          <DialogContent sx={{ px: 3, py: 3 }}>
            <Alert severity="warning" sx={{ mb: 2 }}>
              This action cannot be undone
            </Alert>
            <Typography variant="body1" sx={{ mb: 2 }}>
              Are you sure you want to delete the following provider?
            </Typography>
            {providerToDelete && (
              <Box
                sx={{
                  p: 2,
                  border: '1px solid',
                  borderColor: theme.palette.divider,
                  borderRadius: 1,
                  bgcolor: alpha(theme.palette.error.main, 0.02),
                  display: 'flex',
                  alignItems: 'center',
                  gap: 2,
                }}
              >
                <Box
                  sx={{
                    width: 32,
                    height: 32,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.error.main, 0.1),
                  }}
                >
                  <Iconify
                    icon={searchIcon}
                    width={16}
                    height={16}
                    sx={{ color: theme.palette.error.main }}
                  />
                </Box>
                <Box sx={{ flexGrow: 1 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                    {AVAILABLE_WEB_SEARCH_PROVIDERS.find(
                      (p) => p.id === providerToDelete.provider
                    )?.name || providerToDelete.provider}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Web search provider
                  </Typography>
                </Box>
              </Box>
            )}
          </DialogContent>
          <Divider />
          <DialogActions sx={{ px: 3, py: 2 }}>
            <Button
              onClick={() => setDeleteDialogOpen(false)}
              color="inherit"
              disabled={isDeleting}
            >
              Cancel
            </Button>
            <Button
              onClick={handleDeleteConfirm}
              color="error"
              variant="contained"
              disabled={isDeleting}
              startIcon={
                isDeleting ? (
                  <CircularProgress size={16} />
                ) : (
                  <Iconify icon={deleteIcon} width={16} height={16} />
                )
              }
              sx={{
                '&:hover': {
                  bgcolor: alpha(theme.palette.error.main, 0.8),
                },
              }}
            >
              {isDeleting ? 'Deleting...' : 'Delete Provider'}
            </Button>
          </DialogActions>
        </Dialog>

      {/* Snackbars */}
      <Snackbar
        open={!!error}
        autoHideDuration={6000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      </Snackbar>

      <Snackbar
        open={!!success}
        autoHideDuration={3000}
        onClose={() => setSuccess(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert severity="success" onClose={() => setSuccess(null)}>
          {success}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default WebSearchSettings;
