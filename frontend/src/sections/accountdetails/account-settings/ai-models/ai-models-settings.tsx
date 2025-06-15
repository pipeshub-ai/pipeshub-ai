import { useState, useEffect } from 'react';
import robotIcon from '@iconify-icons/mdi/robot';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Paper,
  Alert,
  Snackbar,
  Container,
  Typography,
  IconButton,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import ConfigureModelDialog from './components/configure-model-dialog';
import { MODEL_TYPE_NAMES, MODEL_TYPE_ICONS, MODEL_TYPE_DESCRIPTIONS } from './types';

// AI model types - updated to include embedding
const AI_MODEL_TYPES = ['llm', 'embedding'];

// Define the save result interface
interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

// AiModel interface
interface AiModel {
  type: string;
  enabled: boolean;
  configurations: ModelConfig[];
}

// Model configuration interface
interface ModelConfig {
  name: string;
  configuration: Record<string, any>;
}

const AiModelsSettings = () => {
  const theme = useTheme();
  const [aiModels, setAiModels] = useState<AiModel[]>([]);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [currentModel, setCurrentModel] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Notification states
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState(
    'AI model configuration updated successfully'
  );

  // Fetch AI models configuration
  useEffect(() => {
    fetchAiModelsConfig();
  }, []);

  const fetchAiModelsConfig = async () => {
    setIsLoading(true);
    try {
      // API call to get current AI models configuration
      const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
      const { data } = response;

      // Create a structured list of model types and their configurations
      const modelsList = AI_MODEL_TYPES.map((type) => {
        const configs = data[type] || [];
        return {
          type,
          enabled: configs.length > 0,
          configurations: configs,
        };
      });

      setAiModels(modelsList);
    } catch (err) {
      // setError('Failed to load AI models configuration');
    } finally {
      setIsLoading(false);
    }
  };

  // Handle closing of different snackbars
  const handleCloseSuccess = () => {
    setSuccess(false);
  };

  const handleCloseError = () => {
    setError(null);
  };

  const handleCloseWarning = () => {
    setWarning(null);
  };

  // Handle model selection
  const handleConfigureModel = (modelType: string) => {
    setCurrentModel(modelType);
    setConfigDialogOpen(true);
  };

  // Handle save configuration
  const handleSaveConfiguration = async (result?: SaveResult) => {
    if (result) {
      if (result.warning) {
        setWarning(result.warning);
      }

      if (result.error) {
        setError(result.error);
        // Keep dialog open if there's an error
        return;
      }

      if (result.success) {
        // Only show success if there was no error
        setSuccessMessage(
          `${currentModel ? getModelTitle(currentModel) : 'AI model'} configuration updated successfully`
        );
        setSuccess(true);

        // Refresh models after successful configuration
        fetchAiModelsConfig();
      }
    } else {
      // Legacy support - if no result object is provided, assume success
      setSuccessMessage(
        `${currentModel ? getModelTitle(currentModel) : 'AI model'} configuration updated successfully`
      );
      setSuccess(true);

      // Refresh models after successful configuration
      fetchAiModelsConfig();
    }

    setConfigDialogOpen(false);
    setCurrentModel(null);
  };

  // Helper to get model title for success message
  const getModelTitle = (modelType: string): string =>
    MODEL_TYPE_NAMES[modelType] || modelType.toUpperCase();

  // Get appropriate color for model type
  const getModelColor = (modelType: string): string => {
    const colors: Record<string, string> = {
      llm: '#4CAF50', // Green
      ocr: '#2196F3', // Blue
      embedding: '#9C27B0', // Purple
      slm: '#FF9800', // Orange
      reasoning: '#E91E63', // Pink
      multiModal: '#673AB7', // Deep Purple
    };

    return colors[modelType] || theme.palette.primary.main;
  };

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          position: 'relative',
          p: { xs: 2, md: 3 },
          borderRadius: 1,
          border: '1px solid',
          borderColor: theme.palette.divider,
          backgroundColor: theme.palette.mode === 'dark' 
            ? alpha(theme.palette.background.paper, 0.6)
            : theme.palette.background.paper,
        }}
      >
        {/* Header section */}
        <Box
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            justifyContent: 'space-between',
            alignItems: { xs: 'flex-start', sm: 'center' },
            mb: 3,
            gap: 2,
          }}
        >
          <Box>
            <Typography
              variant="h5"
              component="h1"
              sx={{
                fontWeight: 600,
                mb: 0.5,
                fontSize: '1.25rem',
                color: theme.palette.text.primary,
              }}
            >
              AI Models
            </Typography>
            <Typography 
              variant="body2" 
              color="text.secondary" 
              sx={{ 
                maxWidth: 500,
                lineHeight: 1.5 
              }}
            >
              Configure AI models to enable intelligent features in your application
            </Typography>
          </Box>
        </Box>

        {/* AI Models Grid */}
        <Grid container spacing={2} mb={3}>
          {aiModels.map((model) => {
            const displayName = MODEL_TYPE_NAMES[model.type] || model.type.toUpperCase();
            const description =
              MODEL_TYPE_DESCRIPTIONS[model.type] || 'AI model for advanced capabilities';
            const icon = MODEL_TYPE_ICONS[model.type] || robotIcon;
            const color = getModelColor(model.type);
            const configCount = model.configurations.length;

            return (
              <Grid item xs={12} key={model.type}>
                <Paper
                  elevation={0}
                  sx={{
                    p: 2,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    borderRadius: 1,
                    border: '1px solid',
                    borderColor: model.enabled 
                      ? alpha(color, theme.palette.mode === 'dark' ? 0.2 : 0.3) 
                      : theme.palette.divider,
                    bgcolor: model.enabled 
                      ? alpha(color, theme.palette.mode === 'dark' ? 0.05 : 0.03)
                      : 'transparent',
                    transition: 'all 0.15s ease-in-out',
                    '&:hover': {
                      transform: 'translateY(-2px)',
                      boxShadow: theme.palette.mode === 'dark'
                        ? `0 4px 12px ${alpha('#000', 0.15)}`
                        : `0 4px 12px ${alpha(theme.palette.grey[500], 0.1)}`,
                      borderColor: alpha(color, theme.palette.mode === 'dark' ? 0.3 : 0.4),
                    },
                  }}
                >
                  {/* Model info */}
                  <Box sx={{ display: 'flex', alignItems: 'center', flexGrow: 1 }}>
                    <Box
                      sx={{
                        width: 40,
                        height: 40,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        mr: 2,
                        bgcolor: alpha(color, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                        color,
                        borderRadius: 1,
                      }}
                    >
                      <Iconify icon={icon} width={22} height={22} />
                    </Box>

                    <Box>
                      <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
                        <Typography 
                          variant="subtitle1" 
                          sx={{ 
                            fontWeight: 600,
                            fontSize: '0.9375rem',
                          }}
                        >
                          {displayName}
                        </Typography>
                        
                        {model.enabled && (
                          <Box
                            component="span"
                            sx={{
                              px: 1,
                              py: 0.25,
                              borderRadius: 0.75,
                              fontSize: '0.6875rem',
                              fontWeight: 600,
                              bgcolor: alpha(theme.palette.success.main, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                              color: theme.palette.success.main,
                              display: 'flex',
                              alignItems: 'center',
                            }}
                          >
                            <Box
                              sx={{
                                width: 6,
                                height: 6,
                                borderRadius: '50%',
                                bgcolor: 'currentColor',
                                mr: 0.5,
                              }}
                            />
                            Enabled
                          </Box>
                        )}
                        
                        {configCount > 0 && (
                          <Box
                            component="span"
                            sx={{
                              px: 1,
                              py: 0.25,
                              borderRadius: 0.75,
                              fontSize: '0.6875rem',
                              fontWeight: 600,
                              bgcolor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                              color: theme.palette.info.main,
                            }}
                          >
                            {configCount} config{configCount !== 1 ? 's' : ''}
                          </Box>
                        )}
                      </Box>
                      
                      <Typography 
                        variant="body2" 
                        color="text.secondary"
                        sx={{ 
                          fontSize: '0.8125rem',
                          lineHeight: 1.5,
                          mt: 0.5 
                        }}
                      >
                        {description}
                      </Typography>
                    </Box>
                  </Box>

                  {/* Settings icon */}
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleConfigureModel(model.type);
                    }}
                    sx={{
                      p: 0.75,
                      color: theme.palette.text.secondary,
                      bgcolor: theme.palette.mode === 'dark' 
                        ? alpha(theme.palette.background.paper, 0.3)
                        : alpha(theme.palette.background.default, 0.8),
                      border: '1px solid',
                      borderColor: theme.palette.divider,
                      '&:hover': {
                        bgcolor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.15 : 0.08),
                        color: theme.palette.primary.main,
                      },
                    }}
                    aria-label={`Configure ${displayName}`}
                  >
                    <Iconify icon={settingsIcon} width={18} height={18} />
                  </IconButton>
                </Paper>
              </Grid>
            );
          })}
        </Grid>
      </Paper>

      {/* Configure Model Dialog */}
      <ConfigureModelDialog
        open={configDialogOpen}
        onClose={() => setConfigDialogOpen(false)}
        onSave={handleSaveConfiguration}
        modelType={currentModel}
      />

      {/* Success snackbar */}
      <Snackbar
        open={success}
        autoHideDuration={4000}
        onClose={handleCloseSuccess}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseSuccess}
          severity="success"
          variant="filled"
          sx={{
            width: '100%',
            boxShadow: theme.palette.mode === 'dark'
              ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
              : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {successMessage}
        </Alert>
      </Snackbar>

      {/* Error snackbar */}
      <Snackbar
        open={!!error}
        autoHideDuration={4000}
        onClose={handleCloseError}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: error && warning ? 20 : 12 }}
      >
        <Alert
          onClose={handleCloseError}
          severity="error"
          variant="filled"
          sx={{
            width: '100%',
            boxShadow: theme.palette.mode === 'dark'
              ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
              : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {error}
        </Alert>
      </Snackbar>

      {/* Warning snackbar */}
      <Snackbar
        open={!!warning}
        autoHideDuration={4000}
        onClose={handleCloseWarning}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 12 }}
      >
        <Alert
          onClose={handleCloseWarning}
          severity="warning"
          variant="filled"
          sx={{
            width: '100%',
            boxShadow: theme.palette.mode === 'dark'
              ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
              : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {warning}
        </Alert>
      </Snackbar>
      
      <Alert 
        variant="outlined" 
        severity="info" 
        sx={{ 
          mt: 3,
          mb: 1,
          borderRadius: 1,
          borderColor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.3 : 0.2),
          '& .MuiAlert-icon': {
            color: theme.palette.info.main,
          },
        }}
      >
        <Typography variant="body2">
          Refer to{' '}
          <Link 
            href="https://docs.pipeshub.com/ai-models/overview" 
            target="_blank" 
            rel="noopener"
            sx={{
              color: theme.palette.primary.main,
              textDecoration: 'none',
              fontWeight: 500,
              '&:hover': {
                textDecoration: 'underline',
              },
            }}
          >
            the documentation
          </Link>{' '}
          for more information.
        </Typography>
      </Alert>
    </Container>
  );
};

export default AiModelsSettings;