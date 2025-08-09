import React, { useState, useCallback, useEffect } from 'react';

import {
  Box,
  alpha,
  Button,
  IconButton,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip,
  Tooltip,
  Snackbar,
  useTheme,
  Card,
  CardContent,
  Stack,
  Divider,
  Alert,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';
import robotIcon from '@iconify-icons/mdi/robot';
import { ModelProvider, AVAILABLE_MODEL_PROVIDERS, ConfiguredModel } from '../types';
import ProviderCards from './available-models-card';
import ModelConfigurationDialog from './configure-model-dialog';
import { getAllModels, deleteModel, setDefaultModel } from '../services/universal-config';


interface MultiModelAccordionProps {
  modelType: 'llm' | 'embedding';
  title: string;
  description: string;
  icon: string;
  color: string;
  expanded: boolean;
  onAccordionChange: (event: React.SyntheticEvent, isExpanded: boolean) => void;
  onModelsChange: (models: ConfiguredModel[]) => void;
}

const MultiModelAccordion: React.FC<MultiModelAccordionProps> = ({
  modelType,
  title,
  description,
  icon,
  color,
  expanded,
  onAccordionChange,
  onModelsChange,
}) => {
  const theme = useTheme();
  const [models, setModels] = useState<ConfiguredModel[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [editingModel, setEditingModel] = useState<ConfiguredModel | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<ModelProvider | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Load existing models
  const loadModels = useCallback(async () => {
    setIsLoading(true);
    try {
      const modelDataList = await getAllModels(modelType);
      setModels(modelDataList);
      onModelsChange(modelDataList);
    } catch (err) {
      console.error(`Error loading ${modelType} models:`, err);
      setError(`Failed to load ${modelType} models`);
    } finally {
      setIsLoading(false);
    }
  }, [modelType, onModelsChange]);

  // Model configuration success handler
  const handleConfigSuccess = useCallback(() => {
    loadModels();
    setConfigDialogOpen(false);
    setSelectedProvider(null);
    setEditingModel(null);
    setSuccess(`Model ${editingModel ? 'updated' : 'added'} successfully`);
  }, [loadModels, editingModel]);

  // Delete model
  const handleDeleteModel = useCallback(async (model: ConfiguredModel) => {
    try {
      await deleteModel(modelType, model.modelKey || model.id!);
      await loadModels();
      setSuccess(`${model.name} deleted successfully`);
    } catch (err) {
      console.error('Error deleting model:', err);
      setError(`Failed to delete ${model.name}`);
    }
  }, [modelType, loadModels]);

  // Set active model
  const handleSetActiveModel = useCallback(async (model: ConfiguredModel) => {
    try {
      await setDefaultModel(modelType, model.modelKey || model.id!);
      await loadModels();
      setSuccess(`${model.name} is now active`);
    } catch (err) {
      console.error('Error setting active model:', err);
      setError(`Failed to activate ${model.name}`);
    }
  }, [modelType, loadModels]);

  // Handle provider card click
  const handleProviderCardClick = (provider: ModelProvider) => {
    setSelectedProvider(provider);
    setEditingModel(null);
    setConfigDialogOpen(true);
  };

  // Handle edit model
  const handleEditModel = (model: ConfiguredModel) => {
    const provider = AVAILABLE_MODEL_PROVIDERS.find(p => p.id === model.provider);
    setSelectedProvider(provider || null);
    setEditingModel(model);
    setConfigDialogOpen(true);
  };

  // Load models when accordion expands
  useEffect(() => {
    if (expanded) {
      loadModels();
    }
  }, [expanded, loadModels]);

  // Create provider count map
  const configuredProviders = models.reduce((acc, model) => {
    if (!acc[model.provider]) {
      acc[model.provider] = { llm: 0, embedding: 0 };
    }
    acc[model.provider][modelType]+=1;
    return acc;
  }, {} as { [key: string]: { llm: number; embedding: number } });

  const activeModel = models.find(m => m.isActive);

  return (
    <>
      <Accordion
        expanded={expanded}
        onChange={onAccordionChange}
        sx={{
          borderRadius: 1,
          border: '1px solid',
          borderColor: 'divider',
          '&:before': { display: 'none' },
          '&.Mui-expanded': {
            boxShadow: theme.customShadows?.z8 || '0 4px 12px rgba(0,0,0,0.1)',
          },
        }}
      >
        <AccordionSummary
          expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" />}
          sx={{
            px: 2.5,
            py: 1.5,
            minHeight: 64,
            '&.Mui-expanded': { minHeight: 64 },
            '& .MuiAccordionSummary-content': {
              margin: '12px 0',
              '&.Mui-expanded': { margin: '12px 0' },
            },
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexGrow: 1 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 40,
                height: 40,
                borderRadius: '8px',
                bgcolor: alpha(color, 0.1),
                color,
              }}
            >
              <Iconify icon={icon} width={20} height={20} />
            </Box>
            <Box sx={{ flexGrow: 1 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                {title}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {description}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              {models.length > 0 && (
                <Chip
                  label={`${models.length} model${models.length !== 1 ? 's' : ''}`}
                  size="small"
                  color="primary"
                  variant="outlined"
                />
              )}
              {activeModel && (
                <Chip
                  label="1 active"
                  size="small"
                  color="success"
                  variant="outlined"
                />
              )}
            </Box>
          </Box>
        </AccordionSummary>
        
        <AccordionDetails sx={{ p: 0, pt: 0 }}>
          <Box sx={{ px: 2.5, pb: 2.5 }}>
            {/* Configured Models Section */}
            {models.length > 0 && (
              <Box sx={{ mb: 3 }}>
                <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
                  Configured Models
                </Typography>
                
                <Stack spacing={1.5}>
                  {models.map((model) => (
                    <Card
                      key={model.id}
                      variant="outlined"
                      sx={{
                        borderColor: model.isActive 
                          ? alpha(theme.palette.success.main, 0.5)
                          : 'divider',
                        bgcolor: model.isActive
                          ? alpha(theme.palette.success.main, 0.03)
                          : 'background.paper',
                      }}
                    >
                      <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
                        <Box sx={{ 
                          display: 'flex', 
                          alignItems: 'center', 
                          justifyContent: 'space-between' 
                        }}>
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <Box
                              sx={{
                                width: 32,
                                height: 32,
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                borderRadius: 1,
                                bgcolor: alpha(theme.palette.primary.main, 0.1),
                              }}
                            >
                              <Iconify 
                                icon={
                                  AVAILABLE_MODEL_PROVIDERS.find(p => p.id === model.provider)?.src || 
                                  robotIcon
                                } 
                                width={18} 
                                height={18}
                                sx={{ color: theme.palette.primary.main }}
                              />
                            </Box>
                            
                            <Box>
                              <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                                {model.name}
                              </Typography>
                              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                                Provider: {AVAILABLE_MODEL_PROVIDERS.find(p => p.id === model.provider)?.name || model.provider}
                              </Typography>
                            </Box>
                            
                            {model.isActive && (
                              <Chip
                                label="Active"
                                size="small"
                                color="success"
                                variant="outlined"
                                sx={{ fontSize: '0.6875rem' }}
                              />
                            )}
                          </Box>
                          
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                            {!model.isActive && (
                              <Button
                                size="small"
                                variant="text"
                                onClick={() => handleSetActiveModel(model)}
                                sx={{ fontSize: '0.75rem', minWidth: 'auto', px: 1 }}
                              >
                                Activate
                              </Button>
                            )}
                            
                            <Tooltip title="Edit model">
                              <IconButton
                                size="small"
                                onClick={() => handleEditModel(model)}
                                sx={{ color: 'text.secondary' }}
                              >
                                <Iconify icon="eva:edit-outline" width={16} height={16} />
                              </IconButton>
                            </Tooltip>
                            
                            <Tooltip title="Delete model">
                              <IconButton
                                size="small"
                                onClick={() => handleDeleteModel(model)}
                                sx={{ color: 'error.main' }}
                              >
                                <Iconify icon="eva:trash-outline" width={16} height={16} />
                              </IconButton>
                            </Tooltip>
                          </Box>
                        </Box>
                      </CardContent>
                    </Card>
                  ))}
                </Stack>
                
                <Divider sx={{ my: 3 }} />
              </Box>
            )}

            {/* Available Models Cards */}
            <ProviderCards
              onProviderSelect={handleProviderCardClick}
              configuredProviders={configuredProviders}
            />
          </Box>
        </AccordionDetails>
      </Accordion>

      {/* Model Configuration Dialog */}
      <ModelConfigurationDialog
        open={configDialogOpen}
        onClose={() => {
          setConfigDialogOpen(false);
          setSelectedProvider(null);
          setEditingModel(null);
        }}
        selectedProvider={selectedProvider as ModelProvider & { editingModel?: ConfiguredModel }}
        // modelType={modelType}
        // editingModel={editingModel}
        onSuccess={handleConfigSuccess}
      />

      {/* Notifications */}
      <Snackbar
        open={!!error}
        autoHideDuration={5000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{mt:6}}
      >
        <Alert severity="error" variant="filled">{error}</Alert>
      </Snackbar>

      <Snackbar
        open={!!success}
        autoHideDuration={3000}
        onClose={() => setSuccess(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{mt:6}}
      >
        <Alert severity="success" variant="filled">{success}</Alert>
      </Snackbar>
    </>
  );
};

export default MultiModelAccordion;