// ===================================================================
// 📁 Enhanced Main Settings with Perfect UX Flow
// ===================================================================

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
} from '@mui/material';

import { Iconify } from 'src/components/iconify';
import robotIcon from '@iconify-icons/mdi/robot';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle';
import { 
  AVAILABLE_MODEL_PROVIDERS, 
  ConfiguredModel, 
  ModelProvider, 
  ModelType 
} from './types';
import ProviderCards from './components/available-models-card';
import ModelConfigurationDialog from './components/configure-model-dialog';
import ConfiguredModelsDisplay from './components/configured-models-display';
import { modelService } from './services/universal-config';

const AiModelsSettings: React.FC = () => {
  const theme = useTheme();
  
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
    // Enhanced provider object with better targeting
    const enhancedProvider = {
      ...provider,
      targetModelType: modelType,
      // If specific model type selected, override supportedTypes for focused experience
      supportedTypes: modelType ? [modelType] : provider.supportedTypes
    };
    
    setSelectedProvider(enhancedProvider);
    setDialogOpen(true);
  };

  const handleDialogClose = () => {
    setDialogOpen(false);
    // Small delay to prevent flickering during transition
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
    
    // Refresh data with smooth indication
    loadConfiguredModels(Boolean(true));
  };

  const handleEdit = (model: ConfiguredModel) => {
    const provider = AVAILABLE_MODEL_PROVIDERS.find(p => p.id === model.provider);
    if (provider) {
      setSelectedProvider({ 
        ...provider, 
        editingModel: model,
        targetModelType: model.modelType,
        // Focus on the specific model type being edited
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
      loadConfiguredModels(Boolean(true));
    } catch (err: any) {
      console.error('Error deleting model:', err);
      setError(err.message || `Failed to delete ${model.name}`);
    }
  };

  const handleSetDefault = async (model: ConfiguredModel) => {
    try {
      await modelService.setDefaultModel(model.modelType, model.modelKey || model.id);
      setSuccess(`${model.name} set as default ${model.modelType.toUpperCase()} model`);
      loadConfiguredModels(Boolean(true));
    } catch (err: any) {
      console.error('Error setting default model:', err);
      setError(err.message || `Failed to set ${model.name} as default`);
    }
  };

  const handleCloseSnackbar = () => {
    setError(null);
    setSuccess(null);
  };

  // Calculate total configured models for better UX messaging
  const totalModels = Object.values(configuredModels).reduce((sum, models) => sum + models.length, 0);

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Paper
        elevation={0}
        sx={{
          p: { xs: 2, md: 3 },
          borderRadius: 1,
          border: '1px solid',
          borderColor: theme.palette.divider,
          backgroundColor: theme.palette.mode === 'dark' 
            ? alpha(theme.palette.background.paper, 0.6)
            : theme.palette.background.paper,
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* Loading overlay for refresh operations */}
        {isRefreshing && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 3,
              bgcolor: 'transparent',
              zIndex: 1000,
            }}
          >
            <Box
              sx={{
                height: '100%',
                bgcolor: theme.palette.primary.main,
                animation: 'loading-bar 1.5s ease-in-out infinite',
                '@keyframes loading-bar': {
                  '0%': { transform: 'translateX(-100%)' },
                  '50%': { transform: 'translateX(0%)' },
                  '100%': { transform: 'translateX(100%)' },
                },
              }}
            />
          </Box>
        )}

        {/* Header */}
        <Fade in={!isLoading} timeout={600}>
          <Box sx={{ mb: 4 }}>
            <Typography
              variant="h5"
              component="h1"
              sx={{
                fontWeight: 600,
                mb: 1,
                fontSize: '1.375rem',
                color: 'text.primary',
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
              }}
            >
              <Iconify 
                icon={robotIcon}
                width={28} 
                height={28}
                sx={{ color: theme.palette.primary.main }}
              />
              AI Models Configuration
            </Typography>
            <Typography 
              variant="body1" 
              color="text.secondary" 
              sx={{ 
                maxWidth: 700,
                lineHeight: 1.6,
                fontSize: '0.9375rem'
              }}
            >
              Configure and manage AI models from different providers. 
              {totalModels > 0 
                ? ` You have ${totalModels} model${totalModels !== 1 ? 's' : ''} configured.`
                : ' Get started by adding your first model below.'
              }
            </Typography>
          </Box>
        </Fade>

        {/* Loading skeleton */}
        {isLoading ? (
          <Box sx={{ mb: 4 }}>
            <Skeleton variant="rectangular" height={200} sx={{ borderRadius: 1, mb: 3 }} />
            <Skeleton variant="text" height={40} width={200} sx={{ mb: 2 }} />
            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 2 }}>
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} variant="rectangular" height={180} sx={{ borderRadius: 1 }} />
              ))}
            </Box>
          </Box>
        ) : (
          <Fade in={Boolean(true)} timeout={800}>
            <Box>
              {/* Configured Models Section */}
              <ConfiguredModelsDisplay
                models={configuredModels}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onSetDefault={handleSetDefault}
                onRefresh={() => loadConfiguredModels(Boolean(true))}
                isLoading={isRefreshing}
              />

              {totalModels > 0 && <Divider sx={{ my: 4 }} />}

              {/* Provider Cards Section */}
              <ProviderCards
                onProviderSelect={handleProviderSelect}
                configuredProviders={configuredProviders}
              />

              {/* Enhanced Info Alert */}
              <Alert 
                variant="outlined" 
                severity="info" 
                sx={{ 
                  mt: 4,
                  borderRadius: 1,
                  borderColor: alpha(theme.palette.info.main, 0.3),
                  bgcolor: alpha(theme.palette.info.main, 0.02),
                }}
              >
                <Typography variant="body2">
                  <strong>Quick Setup:</strong> Click the specific model type button (LLM or Embedding) for each provider to configure exactly what you need. 
                  Each model type can have one default model for your organization.{' '}
                  <Box 
                    component="a"
                    href="https://docs.pipeshub.com/ai-models/overview" 
                    target="_blank" 
                    rel="noopener"
                    sx={{
                      color: 'info.main',
                      textDecoration: 'none',
                      fontWeight: 500,
                      '&:hover': { textDecoration: 'underline' },
                    }}
                  >
                    Learn more →
                  </Box>
                </Typography>
              </Alert>
            </Box>
          </Fade>
        )}
      </Paper>

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

      {/* Enhanced Success/Error Snackbars */}
      <Snackbar
        open={!!success}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{mt:6}}
      >
        <Alert 
          onClose={handleCloseSnackbar} 
          severity="success" 
          sx={{ 
            width: '100%',
            boxShadow: theme.customShadows?.z8 || '0 4px 12px rgba(0,0,0,0.1)',
          }}
          icon={<Iconify icon={checkCircleIcon} width={20} height={20} />}
        >
          {success}
        </Alert>
      </Snackbar>

      {/* <Snackbar
        open={!!error}
        autoHideDuration={6000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert 
          onClose={handleCloseSnackbar} 
          severity="error" 
          sx={{ 
            width: '100%',
            boxShadow: theme.customShadows?.z8 || '0 4px 12px rgba(0,0,0,0.1)',
          }}
          icon={<Iconify icon={alertCircleIcon} width={20} height={20} />}
        >
          {error}
        </Alert>
      </Snackbar> */}
    </Container>
  );
};

export default AiModelsSettings;