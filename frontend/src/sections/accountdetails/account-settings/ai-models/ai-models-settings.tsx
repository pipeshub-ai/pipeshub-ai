import React, { useState, useEffect } from 'react';
import {
  Box,
  Container,
  Paper,
  Typography,
  Alert,
  Snackbar,
  Divider,
  alpha,
  useTheme,
  Fade,
  Skeleton,
  Stack,
  Chip,
  IconButton,
  Tooltip,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';
import robotIcon from '@iconify-icons/mdi/robot';
import llmIcon from '@iconify-icons/mdi/chat';
import embeddingIcon from '@iconify-icons/mdi/vector-polygon';
import refreshIcon from '@iconify-icons/mdi/refresh';
import infoIcon from '@iconify-icons/mdi/info-circle';
import arrowRightIcon from '@iconify-icons/mdi/arrow-right';
import checkIcon from '@iconify-icons/mdi/check';
import closeIcon from '@iconify-icons/mdi/close';
import { modelService } from './services/universal-config';
import ProviderCards from './components/available-models-card';
import ModelConfigurationDialog from './components/configure-model-dialog';
import ConfiguredModelsDisplay from './components/configured-models-display';
import { 
  AVAILABLE_MODEL_PROVIDERS, 
  ConfiguredModel, 
  ModelProvider, 
  ModelType 
} from './types';

const AiModelsSettings: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  
  const [configuredModels, setConfiguredModels] = useState<{ [key: string]: ConfiguredModel[] }>({
    llm: [],
    embedding: []
  });
  const [configuredProviders, setConfiguredProviders] = useState<{ [key: string]: { llm: number; embedding: number } }>({});
  const [selectedProvider, setSelectedProvider] = useState<(ModelProvider & { 
    editingModel?: ConfiguredModel;
    targetModelType?: ModelType;
  }) | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Load all configured models with enhanced error handling
  const loadConfiguredModels = async (showRefreshIndicator = false) => {
    if (showRefreshIndicator) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }
    
    try {
      const [llmModels, embeddingModels] = await Promise.all([
        modelService.getAllModels('llm'),
        modelService.getAllModels('embedding')
      ]);

      setConfiguredModels({
        llm: llmModels,
        embedding: embeddingModels
      });

      // Calculate provider counts
      const providerCounts: { [key: string]: { llm: number; embedding: number } } = {};
      
      [...llmModels, ...embeddingModels].forEach(model => {
        if (!providerCounts[model.provider]) {
          providerCounts[model.provider] = { llm: 0, embedding: 0 };
        }
        providerCounts[model.provider][model.modelType]+=1;
      });

      setConfiguredProviders(providerCounts);
      
      // Clear any previous errors on successful load
      setError(null);
    } catch (err: any) {
      console.error('Error loading models:', err);
      setError(err.message || 'Failed to load configured models');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  // Initial load
  useEffect(() => {
    loadConfiguredModels();
  }, []);

  // Enhanced provider selection with better UX
  const handleProviderSelect = (provider: ModelProvider, modelType?: ModelType) => {
    const enhancedProvider = {
      ...provider,
      targetModelType: modelType,
      supportedTypes: modelType ? [modelType] : provider.supportedTypes
    };
    
    setSelectedProvider(enhancedProvider);
    setDialogOpen(true);
  };

  const handleDialogClose = () => {
    setDialogOpen(false);
    setTimeout(() => setSelectedProvider(null), 150);
  };

  const handleDialogSuccess = () => {
    const modelTypeText = selectedProvider?.targetModelType 
      ? selectedProvider.targetModelType.toUpperCase() 
      : '';
    const successMessage = selectedProvider?.targetModelType
      ? `${selectedProvider.name} ${modelTypeText} model configured successfully`
      : `${selectedProvider?.name} models configured successfully`;
      
    setSuccess(successMessage);
    setDialogOpen(false);
    setSelectedProvider(null);
    
    loadConfiguredModels(true);
  };

  const handleEdit = (model: ConfiguredModel) => {
    const provider = AVAILABLE_MODEL_PROVIDERS.find(p => p.id === model.provider);
    if (provider) {
      setSelectedProvider({ 
        ...provider, 
        editingModel: model,
        targetModelType: model.modelType,
        supportedTypes: [model.modelType]
      });
      setDialogOpen(true);
    } else {
      setError(`Provider "${model.provider}" not found. The provider may have been removed.`);
    }
  };

  const handleDelete = async (model: ConfiguredModel) => {
    try {
      await modelService.deleteModel(model.modelType, model.modelKey || model.id);
      setSuccess(`${model.name} deleted successfully`);
      loadConfiguredModels(true);
    } catch (err: any) {
      console.error('Error deleting model:', err);
      setError(err.message || `Failed to delete ${model.name}`);
    }
  };

  const handleSetDefault = async (model: ConfiguredModel) => {
    try {
      await modelService.setDefaultModel(model.modelType, model.modelKey || model.id);
      setSuccess(`${model.name} set as default ${model.modelType.toUpperCase()} model`);
      loadConfiguredModels(true);
    } catch (err: any) {
      console.error('Error setting default model:', err);
      setError(err.message || `Failed to set ${model.name} as default`);
    }
  };

  const handleCloseSnackbar = () => {
    setError(null);
    setSuccess(null);
  };

  // Calculate totals for better UX messaging
  const totalModels = Object.values(configuredModels).reduce((sum, models) => sum + models.length, 0);
  const totalLLM = configuredModels.llm?.length || 0;
  const totalEmbedding = configuredModels.embedding?.length || 0;

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* Loading Progress Bar */}
        {isRefreshing && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 2,
              zIndex: 1000,
              overflow: 'hidden',
            }}
          >
            <Box
              sx={{
                height: '100%',
                width: '30%',
                backgroundColor: theme.palette.primary.main,
                animation: 'loading-slide 1.5s ease-in-out infinite',
                '@keyframes loading-slide': {
                  '0%': { transform: 'translateX(-100%)' },
                  '100%': { transform: 'translateX(400%)' },
                },
              }}
            />
          </Box>
        )}

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
          <Fade in={!isLoading} timeout={600}>
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
                      icon={robotIcon}
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
                      AI Models
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{
                        color: theme.palette.text.secondary,
                        fontSize: '0.875rem',
                      }}
                    >
                      Configure and manage AI models from different providers
                    </Typography>
                  </Box>
                </Stack>

                {/* Quick Stats */}
                {totalModels > 0 && (
                  <Stack direction="row" spacing={1}>
                    {totalLLM > 0 && (
                      <Chip
                        icon={<Iconify icon={llmIcon} width={14} height={14} />}
                        label={`${totalLLM} LLM`}
                        size="small"
                        sx={{
                          height: 28,
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          backgroundColor: isDark ? alpha(theme.palette.primary.main, 0.8) : alpha(theme.palette.primary.main, 0.1),
                          color: isDark ? theme.palette.common.white : theme.palette.primary.main,
                          border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                        }}
                      />
                    )}
                    {totalEmbedding > 0 && (
                      <Chip
                        icon={<Iconify icon={embeddingIcon} width={14} height={14} />}
                        label={`${totalEmbedding} Embedding`}
                        size="small"
                        sx={{
                          height: 28,
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          backgroundColor: isDark ? alpha(theme.palette.info.main, 0.8) : alpha(theme.palette.info.main, 0.1),
                          color: isDark ? theme.palette.info.contrastText : theme.palette.info.main,
                          border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                        }}
                      />
                    )}
                    <Tooltip title="Refresh models" arrow>
                      <IconButton
                        size="small"
                        onClick={() => loadConfiguredModels(true)}
                        disabled={isRefreshing}
                        sx={{
                          width: 32,
                          height: 32,
                          backgroundColor: isDark 
                            ? alpha(theme.palette.background.default, 0.4)
                            : theme.palette.background.paper,
                          border: `1px solid ${theme.palette.divider}`,
                          '&:hover': {
                            backgroundColor: alpha(theme.palette.primary.main, 0.08),
                            borderColor: theme.palette.primary.main,
                          },
                        }}
                      >
                        <Iconify
                          icon={refreshIcon}
                          width={16}
                          height={16}
                          sx={{ 
                            color: theme.palette.text.secondary,
                            ...(isRefreshing && {
                              animation: 'spin 1s linear infinite',
                              '@keyframes spin': {
                                '0%': { transform: 'rotate(0deg)' },
                                '100%': { transform: 'rotate(360deg)' },
                              },
                            }),
                          }}
                        />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                )}
              </Stack>
            </Stack>
          </Fade>
        </Box>

        {/* Content */}
        <Box sx={{ p: 3 }}>
          {isLoading ? (
            <Stack spacing={3}>
              {/* Loading Configured Models */}
              <Box>
                <Skeleton variant="text" height={32} width={200} sx={{ mb: 2 }} />
                <Stack spacing={2}>
                  {[1, 2].map((i) => (
                    <Skeleton key={i} variant="rectangular" height={120} sx={{ borderRadius: 1.5 }} />
                  ))}
                </Stack>
              </Box>
              
              <Divider />
              
              {/* Loading Provider Cards */}
              <Box>
                <Skeleton variant="text" height={32} width={250} sx={{ mb: 2 }} />
                <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 2 }}>
                  {[1, 2, 3, 4].map((i) => (
                    <Skeleton key={i} variant="rectangular" height={160} sx={{ borderRadius: 1.5 }} />
                  ))}
                </Box>
              </Box>
            </Stack>
          ) : (
            <Fade in timeout={800}>
              <Stack spacing={3}>
                {/* Configured Models Section */}
                <ConfiguredModelsDisplay
                  models={configuredModels}
                  onEdit={handleEdit}
                  onDelete={handleDelete}
                  onSetDefault={handleSetDefault}
                  onRefresh={() => loadConfiguredModels(true)}
                  isLoading={isRefreshing}
                />

                {totalModels > 0 && (
                  <Divider 
                    sx={{ 
                      mx: -3, 
                      borderColor: alpha(theme.palette.divider, 0.6) 
                    }} 
                  />
                )}

                {/* Provider Cards Section */}
                <ProviderCards
                  onProviderSelect={handleProviderSelect}
                  configuredProviders={configuredProviders}
                />

                {/* Info Alert */}
                <Alert 
                  variant="outlined" 
                  severity="info"
                  icon={<Iconify icon={infoIcon} width={20} height={20} />}
                  sx={{ 
                    borderRadius: 1.5,
                    borderColor: alpha(theme.palette.info.main, 0.2),
                    backgroundColor: alpha(theme.palette.info.main, 0.04),
                    '& .MuiAlert-message': {
                      width: '100%',
                    },
                  }}
                >
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                      Click the specific model type (LLM or Embedding) for each provider to configure exactly what you need.
                    </Typography>
                    <Box
                      component="a"
                      href="https://docs.pipeshub.com/ai-models/overview" 
                      target="_blank" 
                      rel="noopener"
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.5,
                        color: theme.palette.info.main,
                        textDecoration: 'none',
                        fontWeight: 600,
                        fontSize: '0.875rem',
                        '&:hover': { 
                          textDecoration: 'underline',
                          color: theme.palette.info.dark,
                        },
                      }}
                    >
                      Learn more
                      <Iconify icon={arrowRightIcon} width={14} height={14} />
                    </Box>
                  </Stack>
                </Alert>
              </Stack>
            </Fade>
          )}
        </Box>
      </Box>

      {/* Configuration Dialog */}
      {selectedProvider && (
        <ModelConfigurationDialog
          key={`${selectedProvider.id}-${selectedProvider.targetModelType}-${selectedProvider.editingModel ? 'edit' : 'add'}-${Date.now()}`}
          open={dialogOpen}
          onClose={handleDialogClose}
          selectedProvider={selectedProvider}
          onSuccess={handleDialogSuccess}
        />
      )}

      {/* Snackbars */}
      <Snackbar
        open={!!success}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 8 }}
      >
        <Alert 
          onClose={handleCloseSnackbar} 
          severity="success" 
          variant="filled"
          sx={{ 
            borderRadius: 1.5,
            fontWeight: 600,
          }}
          icon={<Iconify icon={checkIcon} width={20} height={20} />}
        >
          {success}
        </Alert>
      </Snackbar>

      <Snackbar
        open={!!error}
        autoHideDuration={6000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 8 }}
      >
        <Alert 
          onClose={handleCloseSnackbar} 
          severity="error" 
          variant="filled"
          sx={{ 
            borderRadius: 1.5,
            fontWeight: 600,
          }}
          icon={<Iconify icon={closeIcon} width={20} height={20} />}
        >
          {error}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default AiModelsSettings;