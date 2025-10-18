// ===================================================================
// 📁 Simplified Configured Models Display Component
// ===================================================================

import React, { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  IconButton,
  Button,
  Stack,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  alpha,
  useTheme,
  Tooltip,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
  Divider,
  Alert,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import robotIcon from '@iconify-icons/mdi/robot';
import generativeIcon from '@iconify-icons/mdi/brain';
import embeddingIcon from '@iconify-icons/mdi/vector-polygon';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import deleteIcon from '@iconify-icons/mdi/delete';
import starIcon from '@iconify-icons/mdi/star';   
import pencilIcon from '@iconify-icons/mdi/pencil';
import settingsIcon from '@iconify-icons/mdi/cog';
import arrowDownIcon from '@iconify-icons/mdi/chevron-down';
import moreVerticalIcon from '@iconify-icons/mdi/dots-vertical';
import closeIcon from '@iconify-icons/mdi/close';
import { AVAILABLE_MODEL_PROVIDERS, ConfiguredModel, ModelType } from '../types';

interface ConfiguredModelsDisplayProps {
  models: { [key: string]: ConfiguredModel[] };
  onEdit: (model: ConfiguredModel) => void;
  onDelete: (model: ConfiguredModel) => void;
  onSetDefault: (model: ConfiguredModel) => void;
  onRefresh: () => void;
  isLoading?: boolean;
}

const ConfiguredModelsDisplay: React.FC<ConfiguredModelsDisplayProps> = ({
  models,
  onEdit,
  onDelete,
  onSetDefault,
  onRefresh,
  isLoading = false,
}) => {
  const theme = useTheme();
  const [expandedTypes, setExpandedTypes] = useState<{ [key: string]: boolean }>({
    llm: true,
    embedding: true,
  });
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const [selectedModel, setSelectedModel] = useState<ConfiguredModel | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [modelToDelete, setModelToDelete] = useState<ConfiguredModel | null>(null);
  const [isPerformingAction, setIsPerformingAction] = useState(false);

  const modelTypes: ModelType[] = ['llm', 'embedding'];

  const handleAccordionChange =
    (type: string) => (event: React.SyntheticEvent, isExpanded: boolean) => {
      setExpandedTypes((prev) => ({
        ...prev,
        [type]: isExpanded,
      }));
    };

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, model: ConfiguredModel) => {
    event.stopPropagation();
    setMenuAnchor(event.currentTarget);
    setSelectedModel(model);
  };

  const handleMenuClose = () => {
    setMenuAnchor(null);
    setSelectedModel(null);
  };

  const handleDeleteClick = (model: ConfiguredModel) => {
    setModelToDelete(model);
    setDeleteDialogOpen(true);
    handleMenuClose();
  };

  const handleDeleteConfirm = async () => {
    if (modelToDelete) {
      setIsPerformingAction(true);
      try {
        await onDelete(modelToDelete);
        setDeleteDialogOpen(false);
        setModelToDelete(null);
      } catch (error) {
        console.error('Error deleting model:', error);
      } finally {
        setIsPerformingAction(false);
      }
    }
  };

  const handleSetDefault = async (model: ConfiguredModel) => {
    setIsPerformingAction(true);
    try {
      await onSetDefault(model);
      handleMenuClose();
    } catch (error) {
      console.error('Error setting default model:', error);
    } finally {
      setIsPerformingAction(false);
    }
  };

  const getProviderInfo = (providerId: string) =>
    AVAILABLE_MODEL_PROVIDERS.find((p) => p.id === providerId) || {
      name: providerId,
      src: '/src/assets/img/robot.svg',
      color: theme.palette.primary.main,
    };

  const getModelTypeConfig = (type: string) =>
    ({
      llm: {
        name: 'Generative Models',
        icon: generativeIcon,
        color: '#059669',
        description: 'Text generation and comprehension models',
      },
      embedding: {
        name: 'Embedding Models',
        icon: embeddingIcon,
        color: '#7c3aed',
        description: 'Vectorization for semantic search',
      },
    })[type] || {
      name: type,
      icon: robotIcon,
      color: theme.palette.primary.main,
      description: '',
    };

  const hasAnyModels = modelTypes.some((type) => models[type] && models[type].length > 0);

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!hasAnyModels) {
    return (
      <Box sx={{ mt: 4 }}>
        <Typography variant="h6" sx={{ mb: 2, fontWeight: 600 }}>
          Configured Models
        </Typography>
        <Card
          variant="outlined"
          sx={{
            p: 4,
            textAlign: 'center',
            bgcolor: alpha(theme.palette.primary.main, 0.02),
          }}
        >
          <Iconify
            icon={robotIcon}
            width={48}
            height={48}
            sx={{
              color: theme.palette.text.secondary,
              mb: 2,
            }}
          />
          <Typography variant="h6" color="text.secondary" sx={{ mb: 1 }}>
            No Models Configured Yet
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Add your first AI model using the provider cards below
          </Typography>
        </Card>
      </Box>
    );
  }

  return (
    <Box sx={{ mt: 4 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Typography
          variant="h6"
          sx={{
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            gap: 1,
          }}
        >
          <Iconify icon={settingsIcon} width={24} height={24} />
          Configured Models
        </Typography>
      </Box>

      {/* Model Type Accordions */}
      <Stack spacing={2}>
        {modelTypes.map((type) => {
          const typeModels = models[type] || [];
          if (typeModels.length === 0) return null;

          const typeConfig = getModelTypeConfig(type);
          const defaultModel = typeModels.find((m) => m.isDefault);
          const isExpanded = expandedTypes[type];

          return (
            <Accordion
              key={type}
              expanded={isExpanded}
              onChange={handleAccordionChange(type)}
              sx={{
                border: '1px solid',
                borderColor: alpha(typeConfig.color, 0.2),
                borderRadius: 1,
                '&:before': { display: 'none' },
                '&.Mui-expanded': {
                  boxShadow: `0 2px 8px ${alpha(typeConfig.color, 0.1)}`,
                },
              }}
            >
              <AccordionSummary
                expandIcon={<Iconify icon={arrowDownIcon} />}
                sx={{
                  px: 2.5,
                  py: 1.5,
                  minHeight: 56,
                  bgcolor: alpha(typeConfig.color, 0.05),
                  '&.Mui-expanded': { minHeight: 56 },
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, width: '100%' }}>
                  <Box
                    sx={{
                      width: 32,
                      height: 32,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      borderRadius: 1,
                      bgcolor: alpha(typeConfig.color, 0.1),
                      color: typeConfig.color,
                    }}
                  >
                    <Iconify icon={typeConfig.icon} width={16} height={16} />
                  </Box>

                  <Box sx={{ flexGrow: 1 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                      {typeConfig.name}
                    </Typography>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ fontSize: '0.8125rem' }}
                    >
                      {typeConfig.description} • {typeModels.length} model
                      {typeModels.length !== 1 ? 's' : ''}
                    </Typography>
                  </Box>
                </Box>
              </AccordionSummary>

              <AccordionDetails sx={{ px: 2.5, py: 2 }}>
                <Stack spacing={1.5}>
                  {typeModels.map((model: ConfiguredModel) => {
                    const providerInfo = getProviderInfo(model.provider);

                    return (
                      <Card
                        key={model.id || model.modelKey}
                        variant="outlined"
                        sx={{
                          border: '1px solid',
                          borderColor: model.isDefault
                            ? alpha(theme.palette.primary.main, 0.3)
                            : theme.palette.divider,
                          bgcolor: model.isDefault
                            ? alpha(theme.palette.primary.main, 0.02)
                            : 'transparent',
                          transition: 'all 0.2s ease-in-out',
                          cursor: 'pointer',
                          '&:hover': {
                            boxShadow: theme.customShadows?.z4 || '0 2px 8px rgba(0,0,0,0.1)',
                            borderColor: alpha(providerInfo.color, 0.3),
                          },
                        }}
                        onClick={() => onEdit(model)}
                      >
                        <CardContent sx={{ p: 2 }}>
                          <Box
                            sx={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                            }}
                          >
                            <Box
                              sx={{ display: 'flex', alignItems: 'center', gap: 2, flexGrow: 1 }}
                            >
                              <Box
                                sx={{
                                  width: 32,
                                  height: 32,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  borderRadius: 1,
                                  bgcolor: 'white',
                                  color: 'white',
                                }}
                              >
                                <img
                                  src={providerInfo.src}
                                  alt={providerInfo.name}
                                  width={16}
                                  height={16}
                                />
                              </Box>

                              <Box sx={{ flexGrow: 1 }}>
                                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                                  {providerInfo.name}
                                </Typography>
                                <Typography
                                  variant="body2"
                                  color="text.secondary"
                                  sx={{ fontSize: '0.8125rem' }}
                                >
                                  {model.configuration?.model || 'Custom Model'}
                                </Typography>
                              </Box>
                            </Box>

                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              {model.isDefault && (
                                <Chip
                                  label="Default"
                                  size="small"
                                  color="primary"
                                  variant="outlined"
                                  sx={{ fontSize: '0.6875rem' }}
                                />
                              )}

                              {model.isMultimodal && (
                                <Chip
                                  label="Multimodal"
                                  size="small"
                                  color="secondary"
                                  variant="outlined"
                                  sx={{ fontSize: '0.6875rem' }}
                                />
                              )}
                              {model.isReasoning && (
                                <Chip
                                  label="Reasoning"
                                  size="small"
                                  color="info"
                                  variant="outlined"
                                  sx={{ fontSize: '0.6875rem' }}
                                />
                              )}

                              <IconButton
                                size="small"
                                onClick={(e) => handleMenuOpen(e, model)}
                                disabled={isPerformingAction}
                              >
                                <Iconify icon={moreVerticalIcon} width={16} height={16} />
                              </IconButton>
                            </Box>
                          </Box>
                        </CardContent>
                      </Card>
                    );
                  })}
                </Stack>
              </AccordionDetails>
            </Accordion>
          );
        })}
      </Stack>

      {/* Model Actions Menu */}
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={handleMenuClose}
        PaperProps={{
          sx: {
            minWidth: 160,
            boxShadow: theme.customShadows?.z8 || '0 4px 12px rgba(0,0,0,0.1)',
          },
        }}
      >
        <MenuItem onClick={() => selectedModel && onEdit(selectedModel)}>
          <ListItemIcon>
            <Iconify icon={pencilIcon} width={16} height={16} />
          </ListItemIcon>
          <ListItemText>Edit</ListItemText>
        </MenuItem>

        {selectedModel && !selectedModel.isDefault && (
          <MenuItem onClick={() => handleSetDefault(selectedModel)}>
            <ListItemIcon>
              <Iconify icon={starIcon} width={16} height={16} />
            </ListItemIcon>
            <ListItemText>Set as Default</ListItemText>
          </MenuItem>
        )}

        <MenuItem
          onClick={() => selectedModel && handleDeleteClick(selectedModel)}
          sx={{ color: 'error.main' }}
        >
          <ListItemIcon>
            <Iconify icon={deleteIcon} width={16} height={16} color="error" />
          </ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
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
          sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', pb: 1 }}
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
                Delete Model
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Permanently remove this model configuration
              </Typography>
            </Box>
          </Box>

          <IconButton onClick={() => setDeleteDialogOpen(false)} size="small">
            <Iconify icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        <Divider />

        <DialogContent sx={{ px: 3, py: 3 }}>
          <Alert severity="warning" sx={{ mb: 2 }}>
            This action cannot be undone
          </Alert>

          <Typography variant="body1" sx={{ mb: 2 }}>
            Are you sure you want to delete the following model?
          </Typography>

          {modelToDelete && (
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
                  icon={
                    modelToDelete.modelType === 'llm'
                      ? robotIcon
                      : magnifyIcon
                  }
                  width={16}
                  height={16}
                  sx={{ color: theme.palette.error.main }}
                />
              </Box>

              <Box sx={{ flexGrow: 1 }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  {modelToDelete.name}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {modelToDelete.provider} • {modelToDelete.modelType.toUpperCase()}
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
            disabled={isPerformingAction}
          >
            Cancel
          </Button>
          <Button
            onClick={handleDeleteConfirm}
            color="error"
            variant="contained"
            disabled={isPerformingAction}
            startIcon={
              isPerformingAction ? (
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
            {isPerformingAction ? 'Deleting...' : 'Delete Model'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ConfiguredModelsDisplay;
