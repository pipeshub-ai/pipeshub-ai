// ===================================================================
// 📁 Fixed Model Configuration Dialog - ESLint Issues Resolved
// ===================================================================

import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Typography,
  IconButton,
  Alert,
  Divider,
  Chip,
  useTheme,
  alpha,
  CircularProgress,
  Snackbar,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import robotIcon from '@iconify-icons/mdi/robot';
import closeIcon from '@iconify-icons/mdi/close';
import DynamicForm, { DynamicFormRef } from 'src/components/dynamic-form/components/dynamic-form';
import { ModelProvider, ConfiguredModel, ModelType } from '../types';
import { modelService } from '../services/universal-config';


interface ModelConfigurationDialogProps {
  open: boolean;
  onClose: () => void;
  selectedProvider: ModelProvider & {
    editingModel?: ConfiguredModel;
    targetModelType?: ModelType;
  };
  onSuccess: () => void;
}

const ModelConfigurationDialog: React.FC<ModelConfigurationDialogProps> = ({
  open,
  onClose,
  selectedProvider,
  onSuccess,
}) => {
  const theme = useTheme();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formValidations, setFormValidations] = useState<{ [key: string]: boolean }>({});
  const [expandedAccordion, setExpandedAccordion] = useState<string>('');
  const [showHealthCheckInfo, setShowHealthCheckInfo] = useState(false);

  const formRefs = useRef<{ [key: string]: DynamicFormRef | null }>({});
  const isEditMode = !!selectedProvider?.editingModel;

  // Reset state when dialog opens/closes and handle auto-expand
  useEffect(() => {
    if (open && selectedProvider) {
      setError(null);
      setIsSubmitting(false);
      setFormValidations({});

      // Auto-expand logic
      if (isEditMode) {
        // In edit mode, don't use accordions - show the single form directly
        setExpandedAccordion('');
      } else if (selectedProvider.targetModelType) {
        // Auto-expand the target model type accordion
        setExpandedAccordion(selectedProvider.targetModelType);
      } else if (selectedProvider.supportedTypes.length === 1) {
        // Auto-expand if only one model type is supported
        setExpandedAccordion(selectedProvider.supportedTypes[0]);
      } else {
        // Default to first supported type
        setExpandedAccordion(selectedProvider.supportedTypes[0] || '');
      }
    } else {
      setExpandedAccordion('');
    }
  }, [open, selectedProvider, isEditMode]);

  if (!selectedProvider) return null;

  const handleValidationChange = (modelType: string, isValid: boolean) => {
    setFormValidations((prev) => ({
      ...prev,
      [modelType]: isValid,
    }));
  };

  const handleAccordionChange =
    (panel: string) => (event: React.SyntheticEvent, isExpanded: boolean) => {
      setExpandedAccordion(isExpanded ? panel : '');
    };

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setError(null);
    setShowHealthCheckInfo(true); // Show health check info immediately

    try {
      if (isEditMode) {
        // Edit mode - the DynamicForm will handle the update via updateConfig
        const modelType = selectedProvider.editingModel!.modelType;
        const formRef = formRefs.current[modelType];

        if (formRef) {
          const result = await formRef.handleSave();
          if (result.success) {
            setShowHealthCheckInfo(false); // Hide health check info on success
            onSuccess();
            onClose();
          } else {
            setShowHealthCheckInfo(false); // Hide health check info on error
            setError(result.error || 'Failed to update model');
          }
        } else {
          setShowHealthCheckInfo(false);
          setError('Form reference not found');
        }
      } else {
        // Add mode - create new models for each valid form
        const promises: Promise<any>[] = [];
        const configuredTypes: string[] = [];

        // Get the types to process - either the target type or all supported types
        const typesToProcess = selectedProvider.targetModelType
          ? [selectedProvider.targetModelType]
          : selectedProvider.supportedTypes;

        // FIXED: Replace for...of loop with Promise.all and map
        // Collect all form data first (parallel async operations)
        const formDataPromises = typesToProcess
          .map((type) => {
            const formRef = formRefs.current[type];
            if (formRef && formValidations[type]) {
              return formRef.getFormData().then((formData) => ({ type, formData }));
            }
            return null;
          })
          .filter(Boolean) as Promise<{ type: string; formData: any }>[];

        const formDataResults = await Promise.all(formDataPromises);

        // Process the form data
        formDataResults.forEach(({ type, formData }) => {
          // Clean up form data
          const { providerType, modelType, _provider, ...cleanConfig } = formData;

          promises.push(
            modelService.addModel(type as ModelType, {
              provider: selectedProvider.id,
              configuration: cleanConfig,
              name: formData.name || `${selectedProvider.name} ${type.toUpperCase()} Model`,
            })
          );
          configuredTypes.push(type);
        });

        if (promises.length === 0) {
          setShowHealthCheckInfo(false);
          if (selectedProvider.targetModelType) {
            setError(
              `Please configure the ${selectedProvider.targetModelType.toUpperCase()} model`
            );
          } else {
            setError('Please configure at least one model type');
          }
          return;
        }

        await Promise.all(promises);
        setShowHealthCheckInfo(false); // Hide health check info on success
        onSuccess();
        onClose();
      }
    } catch (err: any) {
      console.error('Submit error:', err);
      setShowHealthCheckInfo(false); // Hide health check info on error
      setError(err.message || 'Failed to save models');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Calculate if we can submit
  const canSubmit = isEditMode
    ? formValidations[selectedProvider.editingModel!.modelType]
    : selectedProvider.targetModelType
      ? formValidations[selectedProvider.targetModelType] // Specific model type selected
      : Object.values(formValidations).some((valid) => valid); // Any model type configured

  // Helper function to create config handlers for DynamicForm
  const createConfigHandlers = (modelType: string) => ({
    getConfig: async () => {
      if (isEditMode && selectedProvider.editingModel!.modelType === modelType) {
        return {
          ...selectedProvider.editingModel!.configuration,
          providerType: selectedProvider.id,
          modelType: selectedProvider.id,
          _provider: selectedProvider.id,
        };
      }
      return {
        providerType: selectedProvider.id,
        modelType: selectedProvider.id,
        _provider: selectedProvider.id,
      };
    },
    updateConfig: async (config: any) => {
      if (isEditMode && selectedProvider.editingModel!.modelType === modelType) {
        // Extract the clean configuration data
        const { providerType, modelType: configModelType, _provider, ...cleanConfig } = config;

        // Call the model service update method
        const result = await modelService.updateModel(
          selectedProvider.editingModel!.modelType as ModelType,
          selectedProvider.editingModel!.modelKey || selectedProvider.editingModel!.id,
          {
            provider: selectedProvider.id,
            configuration: cleanConfig,
            isDefault: selectedProvider.editingModel!.isDefault,
            isMultimodal: selectedProvider.editingModel!.isMultimodal,
            name: config.name || selectedProvider.editingModel!.name,
          }
        );

        return result;
      }

      // For non-edit mode, just return the config (not used in add mode)
      return config;
    },
  });

  // FIXED: Removed unnecessary else statements
  const getDialogTitle = () => {
    if (isEditMode) {
      return `Edit ${selectedProvider.name}`;
    }
    if (selectedProvider.targetModelType) {
      return `Add ${selectedProvider.name} ${selectedProvider.targetModelType.toUpperCase()} Model`;
    }
    return `Configure ${selectedProvider.name}`;
  };

  const getDialogSubtitle = () => {
    if (isEditMode) {
      return `Update ${selectedProvider.editingModel!.name} configuration`;
    }
    if (selectedProvider.targetModelType) {
      return `Configure ${selectedProvider.targetModelType.toUpperCase()} model settings`;
    }
    return `Set up models for ${selectedProvider.supportedTypes.join(' & ').toUpperCase()}`;
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth="lg"
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
                bgcolor: 'white',
              }}
            >
              {selectedProvider.src ? (
                <img src={selectedProvider.src} alt={selectedProvider.name} width={22} height={22} />
              ) : (
                <Iconify icon={robotIcon} width={22} height={22} />
              )}
            </Box>

            <Box>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                {getDialogTitle()}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {getDialogSubtitle()}
              </Typography>
            </Box>
          </Box>

          <IconButton onClick={onClose} size="small">
            <Iconify icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        <Divider />

        <DialogContent sx={{ px: 0, py: 0 }}>
          {error && (
            <Alert severity="error" sx={{ mx: 3, mt: 3 }}>
              {error}
            </Alert>
          )}

          <Box sx={{ px: 3, py: 3 }}>
            {isEditMode ? (
              // Edit mode - single model type (no accordion needed)
              <Box>
                <Typography variant="h6" sx={{ mb: 2 }}>
                  {selectedProvider.editingModel!.modelType.toUpperCase()} Configuration
                </Typography>
                <DynamicForm
                  ref={(ref) => {
                    formRefs.current[selectedProvider.editingModel!.modelType] = ref;
                  }}
                  configType={selectedProvider.editingModel!.modelType as 'llm' | 'embedding'}
                  onValidationChange={(isValid) =>
                    handleValidationChange(selectedProvider.editingModel!.modelType, isValid)
                  }
                  initialProvider={selectedProvider.id}
                  {...createConfigHandlers(selectedProvider.editingModel!.modelType)}
                />
              </Box>
            ) : selectedProvider.targetModelType ? (
              // Specific model type selected - no accordion needed
              <Box>
                <Typography
                  variant="h6"
                  sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 1.5 }}
                >
                  <Iconify
                    icon={
                      selectedProvider.targetModelType === 'llm'
                        ? 'carbon:machine-learning-model'
                        : 'mdi:magnify'
                    }
                    width={20}
                    height={20}
                    sx={{
                      color: selectedProvider.targetModelType === 'llm' ? '#4CAF50' : '#9C27B0',
                    }}
                  />
                  {selectedProvider.targetModelType.toUpperCase()} Configuration
                </Typography>
                <DynamicForm
                  ref={(ref) => {
                    formRefs.current[selectedProvider.targetModelType!] = ref;
                  }}
                  configType={selectedProvider.targetModelType as 'llm' | 'embedding'}
                  onValidationChange={(isValid) =>
                    handleValidationChange(selectedProvider.targetModelType!, isValid)
                  }
                  initialProvider={selectedProvider.id}
                  stepperMode={Boolean(true)}
                  isRequired={Boolean(true)}
                  {...createConfigHandlers(selectedProvider.targetModelType!)}
                />
              </Box>
            ) : (
              // Multiple model types - show accordions
              selectedProvider.supportedTypes.map((type) => {
                const isExpanded = expandedAccordion === type;
                const typeColor = type === 'llm' ? '#4CAF50' : '#9C27B0';
                const typeIcon = type === 'llm' ? 'carbon:machine-learning-model' : 'mdi:magnify';

                return (
                  <Accordion
                    key={type}
                    expanded={isExpanded}
                    onChange={handleAccordionChange(type)}
                    sx={{
                      mb: 2,
                      border: '1px solid',
                      borderColor: formValidations[type]
                        ? alpha(theme.palette.success.main, 0.3)
                        : theme.palette.divider,
                      borderRadius: 1,
                      '&:before': { display: 'none' },
                      '&.Mui-expanded': {
                        borderColor: alpha(typeColor, 0.3),
                      },
                    }}
                  >
                    <AccordionSummary
                      expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" />}
                      sx={{
                        px: 2.5,
                        py: 1,
                        minHeight: 56,
                        bgcolor: formValidations[type]
                          ? alpha(theme.palette.success.main, 0.04)
                          : isExpanded
                            ? alpha(typeColor, 0.04)
                            : 'transparent',
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
                            bgcolor: alpha(typeColor, 0.1),
                            color: typeColor,
                          }}
                        >
                          <Iconify icon={typeIcon} width={16} height={16} />
                        </Box>

                        <Typography variant="subtitle1" sx={{ fontWeight: 600, flexGrow: 1 }}>
                          {type.toUpperCase()} Configuration
                        </Typography>

                        {formValidations[type] && (
                          <Chip
                            label="Configured"
                            size="small"
                            color="success"
                            variant="outlined"
                          />
                        )}
                      </Box>
                    </AccordionSummary>

                    <AccordionDetails sx={{ px: 2.5, py: 2 }}>
                      <DynamicForm
                        ref={(ref) => {
                          formRefs.current[type] = ref;
                        }}
                        configType={type as 'llm' | 'embedding'}
                        onValidationChange={(isValid) => handleValidationChange(type, isValid)}
                        initialProvider={selectedProvider.id}
                        stepperMode={Boolean(true)}
                        isRequired={Boolean(true)}
                        {...createConfigHandlers(type)}
                      />
                    </AccordionDetails>
                  </Accordion>
                );
              })
            )}
          </Box>
        </DialogContent>

        <Divider />

        <DialogActions sx={{ px: 3, py: 2 }}>
          <Button onClick={onClose} color="inherit" disabled={isSubmitting}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={!canSubmit || isSubmitting}
            startIcon={isSubmitting ? <CircularProgress size={16} /> : undefined}
            sx={{
              bgcolor: 'white',
              '&:hover': {
                bgcolor: 'white',
              },
            }}
          >
            {isSubmitting
              ? isEditMode
                ? 'Updating...'
                : selectedProvider.targetModelType
                  ? 'Adding Model...'
                  : 'Adding Models...'
              : isEditMode
                ? 'Update Model'
                : selectedProvider.targetModelType
                  ? 'Add Model'
                  : 'Add Models'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Health Check Info Snackbar */}
      {createPortal(
        <Snackbar
          open={showHealthCheckInfo}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          sx={{ mt: 8 }}
        >
          <Alert
            severity="info"
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              minWidth: 350,
              boxShadow: theme.customShadows?.z8 || '0 4px 12px rgba(0,0,0,0.15)',
            }}
            icon={<CircularProgress size={18} sx={{ color: 'info.main' }} />}
          >
            <Typography variant="body2">
              {isEditMode
                ? 'Updating model configuration and performing health check...'
                : 'Adding model and performing health check...'}
              <br />
              <Typography variant="caption" color="text.secondary">
                This may take a few seconds
              </Typography>
            </Typography>
          </Alert>
        </Snackbar>,
        document.body
      )}
    </>
  );
};

export default ModelConfigurationDialog;
