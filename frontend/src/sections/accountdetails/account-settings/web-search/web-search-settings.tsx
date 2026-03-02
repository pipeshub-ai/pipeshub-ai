import { useState, useEffect, useMemo } from 'react';
import {
  Box,
  Container,
  Paper,
  Typography,
  Alert,
  Snackbar,
  Divider,
  Button,
  Card,
  CardContent,
  CardActions,
  Grid,
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

  const [configuredProviders, setConfiguredProviders] = useState<ConfiguredWebSearchProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<WebSearchProvider | null>(null);
  const [editingProvider, setEditingProvider] = useState<ConfiguredWebSearchProvider | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [providerToDelete, setProviderToDelete] = useState<ConfiguredWebSearchProvider | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
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
  };

  const handleSave = async () => {
    if (!selectedProvider) return;

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
      await webSearchService.deleteProvider(providerToDelete.providerKey);
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
    try {
      await webSearchService.setDefaultProvider(providerKey);
      setSuccess('Default provider updated successfully');
      loadConfiguredProviders();
    } catch (err: any) {
      setError(err.message || 'Failed to set default provider');
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
    <Container maxWidth="xl">
      <Box sx={{ py: 3 }}>
        <Typography variant="h4" gutterBottom>
          Web Search Configuration
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          Configure web search providers for the chatbot to use when searching the web.
        </Typography>

        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            Web Search Mode Image Settings
          </Typography>
          <Divider sx={{ mb: 2 }} />
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
                  control={<Radio />}
                  label="Send images to LLM"
                />
                <FormControlLabel
                  value="exclude"
                  control={<Radio />}
                  label="Do not send images to LLM"
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
                  sx={{ maxWidth: 320 }}
                />
                <Alert severity="warning">
                  Including images may increase cost and add latency to web search queries.
                </Alert>
              </>
            )}

            <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button
                variant="contained"
                onClick={handleSaveWebSearchSettings}
                disabled={isLoading || isSavingSettings}
              >
                {isSavingSettings ? 'Saving...' : 'Save Image Settings'}
              </Button>
            </Box>
          </Stack>
        </Paper>

        {/* Configured Providers */}
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            Configured Providers
          </Typography>
          <Divider sx={{ mb: 2 }} />
          {isLoading ? (
            <Typography>Loading...</Typography>
          ) : displayConfiguredProviders.length === 0 ? (
            <Alert severity="info">
              No providers configured yet. Add a provider below to get started.
            </Alert>
          ) : (
            <Grid container spacing={2}>
              {displayConfiguredProviders.map((configured) => {
                const provider = AVAILABLE_WEB_SEARCH_PROVIDERS.find(
                  (p) => p.id === configured.provider
                );
                const isDuckDuckGo = configured.provider === DUCKDUCKGO_PROVIDER_ID;
                return (
                  <Grid item xs={12} md={6} key={configured.providerKey}>
                    <Card
                      variant="outlined"
                      sx={{
                        border: configured.isDefault
                          ? `2px solid ${theme.palette.primary.main}`
                          : undefined,
                      }}
                    >
                      <CardContent>
                        <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                          <Typography variant="h6" sx={{ flexGrow: 1 }}>
                            {provider?.name || configured.provider}
                          </Typography>
                          {configured.isDefault && (
                            <Chip label="Default" color="primary" size="small" />
                          )}
                        </Box>
                        <Typography variant="body2" color="text.secondary">
                          {provider?.description || 'Web search provider'}
                        </Typography>
                      </CardContent>
                      <CardActions>
                        {!isDuckDuckGo && (
                          <Button size="small" onClick={() => handleEdit(configured)}>
                            Edit
                          </Button>
                        )}
                        {!configured.isDefault && (
                          <Button
                            size="small"
                            onClick={() => handleSetDefault(configured.providerKey)}
                          >
                            Set as Default
                          </Button>
                        )}
                        {!isDuckDuckGo && (
                          <Button
                            size="small"
                            color="error"
                            onClick={() => handleDeleteClick(configured)}
                          >
                            Delete
                          </Button>
                        )}
                      </CardActions>
                    </Card>
                  </Grid>
                );
              })}
            </Grid>
          )}
        </Paper>

        {/* Available Providers */}
        <Paper sx={{ p: 3 }}>
          <Typography variant="h6" gutterBottom>
            Available Providers
          </Typography>
          <Divider sx={{ mb: 2 }} />
          <Grid container spacing={2}>
            {ADDABLE_WEB_SEARCH_PROVIDERS.map((provider) => {
              const isAlreadyConfigured = configuredProviders.some(
                (c) => c.provider === provider.id
              );
              return (
                <Grid item xs={12} sm={6} md={4} key={provider.id}>
                  <Card
                    variant="outlined"
                    sx={{
                      height: '100%',
                      display: 'flex',
                      flexDirection: 'column',
                      cursor: isAlreadyConfigured ? 'default' : 'pointer',
                      opacity: isAlreadyConfigured ? 0.85 : 1,
                      ...(!isAlreadyConfigured && {
                        '&:hover': {
                          borderColor: theme.palette.primary.main,
                          boxShadow: theme.shadows[4],
                        },
                      }),
                    }}
                    onClick={() => !isAlreadyConfigured && handleProviderSelect(provider)}
                  >
                    <CardContent sx={{ flexGrow: 1 }}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                        <Typography variant="h6">{provider.name}</Typography>
                        {provider.isPopular && (
                          <Chip label="Popular" size="small" color="primary" />
                        )}
                      </Stack>
                      <Typography variant="body2" color="text.secondary">
                        {provider.description}
                      </Typography>
                    </CardContent>
                    <CardActions>
                      {isAlreadyConfigured ? (
                        <Chip
                          label="Already configured"
                          size="small"
                          color="success"
                          variant="outlined"
                        />
                      ) : (
                        <Button size="small" color="primary">
                          Configure
                        </Button>
                      )}
                    </CardActions>
                  </Card>
                </Grid>
              );
            })}
          </Grid>
        </Paper>

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
            <Button onClick={handleSave} variant="contained" color="primary">
              {editingProvider ? 'Update' : 'Add'}
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
      </Box>
    </Container>
  );
};

export default WebSearchSettings;
