// ===================================================================
// 📁 Fixed Model Configuration Dialog - Provider Switching Issues Resolved
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
  Fade,
  Backdrop,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import robotIcon from '@iconify-icons/mdi/robot';
import closeIcon from '@iconify-icons/mdi/close';
import DynamicForm, { DynamicFormRef } from 'src/components/dynamic-form/components/dynamic-form';
import { ModelProvider, ConfiguredModel, ModelType, AVAILABLE_MODEL_PROVIDERS } from '../types';
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
  const [isProviderChanging, setIsProviderChanging] = useState(false);

  const [currentProvider, setCurrentProvider] = useState<
    | (ModelProvider & {
        editingModel?: ConfiguredModel;
        targetModelType?: ModelType;
      })
    | null
  >(selectedProvider);

  const formRefs = useRef<{ [key: string]: DynamicFormRef | null }>({});
  const isEditMode = !!currentProvider?.editingModel;

  // A stable key for re-rendering forms, but not the dialog itself.
  const formContainerKey = `${currentProvider?.id || 'unknown'}-${currentProvider?.targetModelType || 'none'}-${isEditMode ? 'edit' : 'add'}-${currentProvider?.editingModel?.id || 'new'}`;

  useEffect(() => {
    if (selectedProvider) {
      setCurrentProvider(selectedProvider);
    }
  }, [selectedProvider]);

  useEffect(() => {
    if (open && currentProvider) {
      setError(null);
      setIsSubmitting(false);
      setFormValidations({});

      if (isEditMode) {
        setExpandedAccordion('');
      } else if (currentProvider.targetModelType) {
        setExpandedAccordion(currentProvider.targetModelType);
      } else if (currentProvider.supportedTypes.length === 1) {
        setExpandedAccordion(currentProvider.supportedTypes[0]);
      } else {
        setExpandedAccordion(currentProvider.supportedTypes[0] || '');
      }
    } else {
      setExpandedAccordion('');
    }
  }, [
    open,
    currentProvider?.id,
    currentProvider?.targetModelType,
    isEditMode,
    currentProvider?.editingModel?.id,
    currentProvider,
  ]);

  useEffect(() => {
    if (open) {
      formRefs.current = {};
    }
  }, [formContainerKey, open]);

  if (!currentProvider) return null;

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
    setShowHealthCheckInfo(true);

    try {
      if (isEditMode) {
        const modelType = selectedProvider.editingModel!.modelType;
        const formRef = formRefs.current[modelType];

        if (formRef) {
          const result = await formRef.handleSave();
          if (result.success) {
            setShowHealthCheckInfo(false);
            onSuccess();
            onClose();
          } else {
            setShowHealthCheckInfo(false);
            setError(result.error || 'Failed to update model');
          }
        } else {
          setShowHealthCheckInfo(false);
          setError('Form reference not found');
        }
      } else {
        const promises: Promise<any>[] = [];
        const configuredTypes: string[] = [];
        const typesToProcess = currentProvider.targetModelType
          ? [currentProvider.targetModelType]
          : currentProvider.supportedTypes;
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

        formDataResults.forEach(({ type, formData }) => {
          const { providerType, modelType, _provider, isMultimodal, isReasoning, ...cleanConfig } = formData;
          promises.push(
            modelService.addModel(type as ModelType, {
              provider: currentProvider.id,
              configuration: cleanConfig,
              name: formData.name || `${currentProvider.name} ${type.toUpperCase()} Model`,
              isMultimodal,
              isReasoning,
            })
          );
          configuredTypes.push(type);
        });

        if (promises.length === 0) {
          setShowHealthCheckInfo(false);
          if (currentProvider.targetModelType) {
            setError(`Please configure the ${currentProvider.targetModelType.toUpperCase()} model`);
          } else {
            setError('Please configure at least one model type');
          }
          return;
        }

        await Promise.all(promises);
        setShowHealthCheckInfo(false);
        onSuccess();
        onClose();
      }
    } catch (err: any) {
      console.error('Submit error:', err);
      setShowHealthCheckInfo(false);
      setError(err.message || 'Failed to save models');
    } finally {
      setIsSubmitting(false);
    }
  };

  const canSubmit = isEditMode
    ? formValidations[currentProvider.editingModel!.modelType]
    : currentProvider.targetModelType
      ? formValidations[currentProvider.targetModelType]
      : Object.values(formValidations).some((valid) => valid);

  const createConfigHandlers = (modelType: string) => ({
    getConfig: async () => {
      if (isEditMode && currentProvider.editingModel!.modelType === modelType) {
        return {
          ...currentProvider.editingModel!.configuration,
          providerType: currentProvider.id,
          modelType: currentProvider.id,
          _provider: currentProvider.id,
          isMultimodal: currentProvider.editingModel!.isMultimodal === true,
          isReasoning: currentProvider.editingModel!.isReasoning === true,
        };
      }
      return {
        providerType: currentProvider.id,
        modelType: currentProvider.id,
        _provider: currentProvider.id,
      };
    },
    updateConfig: async (config: any) => {
      if (isEditMode && currentProvider.editingModel!.modelType === modelType) {
        const {
          providerType,
          modelType: configModelType,
          _provider,
          isMultimodal,
          isReasoning,
          ...cleanConfig
        } = config;
        try {
          const result = await modelService.updateModel(
            currentProvider.editingModel!.modelType as ModelType,
            currentProvider.editingModel!.modelKey || currentProvider.editingModel!.id,
            {
              provider: currentProvider.id,
              configuration: cleanConfig,
              isDefault: currentProvider.editingModel!.isDefault,
              isMultimodal,
              isReasoning,
              name: config.name || currentProvider.editingModel!.name,
            }
          );
          return result;
        } catch (err: any) {
          console.error('Error updating model:', err);
          setError(err.message || 'Failed to update model');
          throw new Error(err.message || 'Failed to update model');
        }
      }
      return config;
    },
    onProviderChange: async (newProviderId: string) => {
      setIsProviderChanging(true);
      const newProvider = AVAILABLE_MODEL_PROVIDERS.find((p) => p.id === newProviderId);
      if (newProvider) {
        const enhancedProvider = {
          ...newProvider,
          targetModelType: currentProvider.targetModelType,
          supportedTypes: currentProvider.targetModelType
            ? [currentProvider.targetModelType]
            : newProvider.supportedTypes,
          editingModel: undefined,
        };
        setCurrentProvider(enhancedProvider);
        setFormValidations({});
        setError(null);
      }
      setIsProviderChanging(false);
    },
  });

  const getDialogTitle = () => {
    if (isEditMode) {
      return `Edit ${currentProvider.name}`;
    }
    if (currentProvider.targetModelType) {
      return `Add ${currentProvider.name} ${currentProvider.targetModelType.toUpperCase()} Model`;
    }
    return `Configure ${currentProvider.name}`;
  };

  const getDialogSubtitle = () => {
    if (isEditMode) {
      return `Update ${currentProvider.editingModel!.name} configuration`;
    }
    if (currentProvider.targetModelType) {
      return `Configure ${currentProvider.targetModelType.toUpperCase()} model settings`;
    }
    return `Set up models for ${currentProvider.supportedTypes.join(' & ').toUpperCase()}`;
  };

  return (
    <>
      <Dialog
        // REMOVED KEY FROM HERE - THIS IS THE FIX
        open={open}
        onClose={onClose}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 1,
            maxHeight: '90vh',
            backgroundColor: theme.palette.background.paper,
            position: 'relative',
            transition: 'all 300ms ease-in-out', // Ensure smooth transitions
          },
        }}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
      >
        <Backdrop
          open={isProviderChanging}
          sx={{
            position: 'absolute',
            borderRadius: 'inherit',
            backgroundColor: alpha(theme.palette.background.paper, 0.75),
            backdropFilter: 'blur(3px)',
            zIndex: (themeValue) => themeValue.zIndex.modal + 1,
          }}
        >
          <CircularProgress color="primary" />
        </Backdrop>

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
              {currentProvider.src ? (
                <img src={currentProvider.src} alt={currentProvider.name} width={22} height={22} />
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
          {/* Use the key on the content container to force re-render of forms */}
          <Box key={formContainerKey} sx={{ px: 3, py: 3 }}>
            {isEditMode ? (
              <Box>
                <Typography variant="h6" sx={{ mb: 2 }}>
                  {currentProvider.editingModel!.modelType.toUpperCase()} Configuration
                </Typography>
                <DynamicForm
                  ref={(ref) => {
                    formRefs.current[currentProvider.editingModel!.modelType] = ref;
                  }}
                  configType={currentProvider.editingModel!.modelType as 'llm' | 'embedding'}
                  onValidationChange={(isValid) =>
                    handleValidationChange(currentProvider.editingModel!.modelType, isValid)
                  }
                  initialProvider={currentProvider.id}
                  {...createConfigHandlers(currentProvider.editingModel!.modelType)}
                />
              </Box>
            ) : currentProvider.targetModelType ? (
              <Box>
                <Typography
                  variant="h6"
                  sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 1.5 }}
                >
                  <Iconify
                    icon={
                      currentProvider.targetModelType === 'llm'
                        ? 'carbon:machine-learning-model'
                        : 'mdi:magnify'
                    }
                    width={20}
                    height={20}
                    sx={{
                      color: currentProvider.targetModelType === 'llm' ? '#4CAF50' : '#9C27B0',
                    }}
                  />
                  {currentProvider.targetModelType.toUpperCase()} Configuration
                </Typography>
                <DynamicForm
                  ref={(ref) => {
                    formRefs.current[currentProvider.targetModelType!] = ref;
                  }}
                  configType={currentProvider.targetModelType as 'llm' | 'embedding'}
                  onValidationChange={(isValid) =>
                    handleValidationChange(currentProvider.targetModelType!, isValid)
                  }
                  initialProvider={currentProvider.id}
                  stepperMode={Boolean(true)}
                  isRequired={Boolean(true)}
                  {...createConfigHandlers(currentProvider.targetModelType!)}
                />
              </Box>
            ) : (
              currentProvider.supportedTypes.map((type) => {
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
                        initialProvider={currentProvider.id}
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
              bgcolor: 'primary.main',
            }}
          >
            {isSubmitting
              ? isEditMode
                ? 'Updating...'
                : currentProvider.targetModelType
                  ? 'Adding Model...'
                  : 'Adding Models...'
              : isEditMode
                ? 'Update Model'
                : currentProvider.targetModelType
                  ? 'Add Model'
                  : 'Add Models'}
          </Button>
        </DialogActions>
      </Dialog>

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
