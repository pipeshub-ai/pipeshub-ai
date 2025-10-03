import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';
import infoIcon from '@iconify-icons/mdi/info-outline';
import React, { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Fade,
  Alert,
  Button,
  TextField,
  Typography,
  Autocomplete,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import { useAuthContext } from 'src/auth/hooks';

import DynamicField from './dynamic-field';
import { useDynamicForm } from '../hooks/use-dynamic-form';

import type { ConfigType } from '../core/config-registry';

interface DynamicFormProps {
  // Core configuration
  configType: ConfigType;

  // Callbacks
  onValidationChange: (isValid: boolean, formData?: any) => void;
  onSaveSuccess?: () => void;
  onProviderChange?: (newProviderId: string) => void; // New callback for provider changes

  // Data management
  getConfig: () => Promise<any>;
  updateConfig: (config: any) => Promise<any>;

  // UI customization
  title?: string;
  description?: string;
  infoMessage?: string;
  documentationUrl?: string;

  // Behavior modes
  stepperMode?: boolean; // For stepper/wizard mode
  isRequired?: boolean; // For required fields validation
  initialProvider?: string; // Initial provider selection

  // Legacy support (backward compatibility)
  modelType?: 'llm' | 'embedding'; // Deprecated: Use configType instead
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface DynamicFormRef {
  handleSave: () => Promise<SaveResult>;
  getFormData: () => Promise<any>;
  validateForm: () => Promise<boolean>;
  hasFormData: () => Promise<boolean>;
  rehydrateForm?: (data: any) => Promise<void>;

  // Legacy method names for backward compatibility
  handleSubmit?: () => Promise<SaveResult>; // Alias for handleSave
}

const DynamicForm = forwardRef<DynamicFormRef, DynamicFormProps>((props, ref) => {
  const {
    configType,
    modelType, // Legacy support
    onValidationChange,
    onSaveSuccess,
    onProviderChange, // New prop
    getConfig,
    updateConfig,
    title,
    description,
    infoMessage,
    documentationUrl,
    stepperMode = false,
    isRequired = false,
    initialProvider,
  } = props;

  const theme = useTheme();
  const { user } = useAuthContext();
  const accountType = user?.accountType || 'individual';

  // State management
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isEditing, setIsEditing] = useState(isRequired || stepperMode);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [formSubmitSuccess, setFormSubmitSuccess] = useState(false);
  const [fetchError, setFetchError] = useState<boolean>(false);
  const [formDataLoaded, setFormDataLoaded] = useState(stepperMode);
  const [providerHeights, setProviderHeights] = useState<Record<string, number>>({});

  // Refs
  const formContainerRef = useRef<HTMLDivElement>(null);
  const formInstanceKey = useRef(`${configType}-${Date.now()}-${Math.random()}`);
  const originalApiConfigRef = useRef<any>(null);

  // Determine final config type (support legacy modelType)
  const finalConfigType = configType || modelType;
  if (!finalConfigType) {
    throw new Error('Either configType or modelType must be provided');
  }

  // Use the unified dynamic form hook
  const {
    currentProvider,
    switchProvider,
    control,
    handleSubmit,
    reset,
    initializeForm,
    isValid,
    isSwitchingProvider,
    providerConfig,
    providers,
    getValues,
    watch,
    resetToProvider,
  } = useDynamicForm(finalConfigType as ConfigType, initialProvider || '', accountType);

  // Enhanced validation change handler for stepper mode
  useEffect(() => {
    if (stepperMode) {
      const subscription = watch((data: any, { name, type }: any) => {
        const timeoutId = setTimeout(() => {
          let hasData = false;
          let validationResult = false;

          const isSpecialProvider = providerConfig?.isSpecial;

          if (isSpecialProvider) {
            hasData = true;
            validationResult = true;
          } else if (finalConfigType === 'storage' && data.providerType === 'local') {
            hasData = true;
            validationResult = true;
          } else if (finalConfigType === 'url') {
            hasData = !!(data.frontendUrl?.trim() || data.connectorUrl?.trim());
            validationResult = hasData ? isValid : true;
          } else {
            const nonMetaKeys = Object.keys(data).filter(
              (key) => key !== 'providerType' && key !== 'modelType' && key !== '_provider'
            );

            hasData = nonMetaKeys.some((key) => {
              const value = data[key];
              return value && value.toString().trim() !== '';
            });

            if (isRequired) {
              validationResult = hasData && isValid;
            } else {
              validationResult = hasData ? isValid : true;
            }
          }

          onValidationChange(validationResult, hasData ? data : null);
        }, 100);

        return () => clearTimeout(timeoutId);
      });

      return () => subscription.unsubscribe();
    }

    return () => {};
  }, [
    watch,
    isValid,
    isRequired,
    onValidationChange,
    stepperMode,
    finalConfigType,
    providerConfig,
    currentProvider,
  ]);

  // Regular validation change handler for non-stepper mode
  useEffect(() => {
    if (!stepperMode) {
      if (isSwitchingProvider) return () => {};

      const handler = setTimeout(() => {
        const isLegacyModelType = ['llm', 'embedding'].includes(finalConfigType);
        const shouldReportValid = isLegacyModelType
          ? isValid && isEditing && !isSwitchingProvider
          : isRequired
            ? isValid
            : isValid && isEditing;

        onValidationChange(shouldReportValid && !isSwitchingProvider);
      }, 100);

      return () => clearTimeout(handler);
    }
    return () => {};
  }, [
    stepperMode,
    isValid,
    isEditing,
    onValidationChange,
    isSwitchingProvider,
    isRequired,
    finalConfigType,
  ]);

  // Config loading for non-stepper mode
  const fetchConfig = React.useCallback(
    async (forceRefresh = false) => {
      if (!stepperMode && (!formDataLoaded || forceRefresh)) {
        setIsLoading(true);
        try {
          const config = await getConfig();
          setFetchError(false);

          if (config) {
            originalApiConfigRef.current = config;

            const providerType = config.providerType || config.modelType;
            if (providerType && providerType !== currentProvider) {
              switchProvider(providerType, null);
            }
            initializeForm(config);
          }
          setFormDataLoaded(true);
        } catch (error) {
          console.error(`Failed to load ${finalConfigType} configuration:`, error);
          setFetchError(true);
          setSaveError('Failed to load configuration. View-only mode enabled.');
        } finally {
          setIsLoading(false);
        }
      }
    },
    [
      stepperMode,
      formDataLoaded,
      getConfig,
      finalConfigType,
      switchProvider,
      initializeForm,
      currentProvider,
    ]
  );

  // Expose methods to parent component
  useImperativeHandle(ref, () => {
    const handleSaveImpl = async (): Promise<SaveResult> => {
      if (stepperMode) {
        // Stepper mode validation without saving
        const formData = getValues();

        if (providerConfig?.isSpecial) {
          return { success: true };
        }

        if (finalConfigType === 'storage' && formData.providerType === 'local') {
          return { success: true };
        }

        const hasData =
          Object.keys(formData).filter(
            (key) =>
              key !== 'providerType' &&
              key !== 'modelType' &&
              key !== '_provider' &&
              formData[key] &&
              formData[key].toString().trim() !== ''
          ).length > 0;

        if (isRequired) {
          if (hasData && isValid) {
            return { success: true };
          }
          if (!hasData) {
            return {
              success: false,
              error: 'This configuration is required. Please complete all required fields.',
            };
          }
          return { success: false, error: 'Please complete all required fields correctly.' };
        }

        if (!hasData || (hasData && isValid)) {
          return { success: true };
        }
        return {
          success: false,
          error: 'Please complete all required fields or leave empty to skip.',
        };
      }

      // Regular save mode
      try {
        setIsSaving(true);
        setSaveError(null);
        setFormSubmitSuccess(false);

        return await new Promise<SaveResult>((resolve) => {
          handleSubmit(async (data: any) => {
            try {
              const isLegacyModelType = ['llm', 'embedding'].includes(finalConfigType);
              const saveData = {
                ...data,
                [isLegacyModelType ? 'modelType' : 'providerType']: currentProvider,
                _provider: currentProvider,
              };

              await updateConfig(saveData);

              if (onSaveSuccess) {
                onSaveSuccess();
              }

              setFormSubmitSuccess(true);
              setIsEditing(false);

              // Store for legacy mode cancel functionality
              if (isLegacyModelType) {
                originalApiConfigRef.current = saveData;

                // Refresh config in legacy mode
                setTimeout(() => {
                  fetchConfig(true);
                }, 100);
              }

              resolve({ success: true });
            } catch (error: any) {
              console.error('Error saving configuration:', error);
              const errorMessage =
                error.response?.data?.message || error.message ||
                `Failed to save ${providerConfig?.label} configuration`;
              setSaveError(errorMessage);
              resolve({ success: false, error: errorMessage });
            } finally {
              setIsSaving(false);
            }
          })();
        });
      } catch (error) {
        setIsSaving(false);
        return {
          success: false,
          error: 'Unexpected error occurred during save operation',
        };
      }
    };

    // ✅ FIXED: Return all methods including aliases in one place
    return {
      handleSave: handleSaveImpl,

      getFormData: async (): Promise<any> => {
        const formData = getValues();
        const isLegacyModelType = ['llm', 'embedding'].includes(finalConfigType);
        return {
          ...formData,
          [isLegacyModelType ? 'modelType' : 'providerType']: currentProvider,
          _provider: currentProvider,
        };
      },

      validateForm: async (): Promise<boolean> => {
        const formData = getValues();

        if (providerConfig?.isSpecial) {
          return true;
        }

        if (finalConfigType === 'storage' && formData.providerType === 'local') {
          return true;
        }

        const nonMetaKeys = Object.keys(formData).filter(
          (key) => key !== 'providerType' && key !== 'modelType' && key !== '_provider'
        );

        const hasData = nonMetaKeys.some((key) => {
          const value = formData[key];
          return value && value.toString().trim() !== '';
        });

        if (isRequired) {
          return hasData && isValid;
        }
        return !hasData || isValid;
      },

      hasFormData: async (): Promise<boolean> => {
        const formData = getValues();

        if (providerConfig?.isSpecial) {
          return true;
        }

        if (finalConfigType === 'storage' && formData.providerType === 'local') {
          return true;
        }

        if (finalConfigType === 'url') {
          return !!(formData.frontendUrl?.trim() || formData.connectorUrl?.trim());
        }

        const nonMetaKeys = Object.keys(formData).filter(
          (key) => key !== 'providerType' && key !== 'modelType' && key !== '_provider'
        );

        return nonMetaKeys.some((key) => {
          const value = formData[key];
          return value && value.toString().trim() !== '';
        });
      },

      // Allow parent to rehydrate form with previously captured data (including provider)
      rehydrateForm: async (data: any): Promise<void> => {
        if (!data) return;
        const providerType = data.providerType || (data as any).modelType || currentProvider;
        if (providerType && resetToProvider) {
          resetToProvider(providerType, data);
        } else if (reset) {
          reset(data);
        }
      },

      handleSubmit: handleSaveImpl,
    };
  }, [
    stepperMode,
    getValues,
    providerConfig,
    finalConfigType,
    isRequired,
    isValid,
    handleSubmit,
    currentProvider,
    updateConfig,
    onSaveSuccess,
    fetchConfig,
    resetToProvider,
    reset,
  ]);

  // Add legacy method alias
  const refMethods = ref as any;
  if (refMethods?.current) {
    refMethods.current.handleSubmit = refMethods.current.handleSave;
  }

  useEffect(() => {
    if (!stepperMode && !formDataLoaded) {
      fetchConfig();
    }
  }, [fetchConfig, stepperMode, formDataLoaded]);

  // Height tracking for smooth transitions
  useEffect(() => {
    if (formContainerRef.current && !isSwitchingProvider && !isLoading) {
      const timer = setTimeout(() => {
        if (formContainerRef.current) {
          const { height } = formContainerRef.current.getBoundingClientRect();
          if (height > 0) {
            setProviderHeights((prev) => ({
              ...prev,
              [currentProvider]: height,
            }));
          }
        }
      }, 100);

      return () => clearTimeout(timer);
    }
    return () => {};
  }, [currentProvider, isSwitchingProvider, isLoading, formDataLoaded]);

  // Auto-clear save errors
  useEffect(() => {
    if (saveError) {
      const timer = setTimeout(() => setSaveError(null), 5000);
      return () => clearTimeout(timer);
    }
    return () => {};
  }, [saveError]);

  // Event handlers
  const handleProviderChange = (event: any, newValue: any) => {
    if (newValue && newValue.id !== currentProvider) {
      // Call the callback to notify parent of provider change

      if (onProviderChange) {
        onProviderChange(newValue.id);
      } else {
        switchProvider(newValue.id);
      }
    }
  };

  const handleToggleEdit = () => {
    if (isEditing) {
      setIsEditing(false);
      setSaveError(null);
      setFormSubmitSuccess(false);

      const isLegacyModelType = ['llm', 'embedding'].includes(finalConfigType);
      if (isLegacyModelType && originalApiConfigRef.current) {
        // For legacy model types, reset to the original configuration
        const originalProvider =
          originalApiConfigRef.current.providerType || originalApiConfigRef.current.modelType;
        if (resetToProvider && originalProvider) {
          resetToProvider(originalProvider, originalApiConfigRef.current);
        }
      } else if (!stepperMode) {
        // Reload config for non-legacy types
        fetchConfig(true);
      }
    } else {
      setIsEditing(true);
    }
  };

  const getTransitionHeight = () => {
    if (isSwitchingProvider && providerHeights[currentProvider]) {
      return providerHeights[currentProvider];
    }
    return providerHeights[currentProvider] || 'auto';
  };

  // Generate default description based on config type
  const getDefaultDescription = () => {
    switch (finalConfigType) {
      case 'llm':
        return 'Configure your LLM model to enable AI capabilities in your application.';
      case 'embedding':
        return 'Configure your embedding model to enable semantic search and document retrieval in your application.';
      case 'storage':
        return 'Configure your storage settings for file management.';
      case 'smtp':
        return 'Configure SMTP settings for email notifications.';
      case 'url':
        return 'Configure the public URLs for your services.';
      default:
        return `Configure your ${String(finalConfigType).toUpperCase()} settings.`;
    }
  };

  // Render form fields
  const renderFieldStructure = () => {
    if (!providerConfig) {
      return (
        <Grid item xs={12}>
          <Alert severity="warning" sx={{ mt: 1 }}>
            No provider configuration found. Please check the provider setup.
          </Alert>
        </Grid>
      );
    }

    // Handle special providers (like default)
    if (providerConfig.isSpecial || currentProvider === 'default') {
      return (
        <Grid item xs={12}>
          <Alert severity="info" sx={{ mt: 1 }}>
            {providerConfig.description}
          </Alert>
        </Grid>
      );
    }

    const fieldsToRender = providerConfig.allFields || [];

    if (fieldsToRender.length === 0) {
      return (
        <Grid item xs={12}>
          <Alert severity="warning" sx={{ mt: 1 }}>
            No fields configured for this provider. Please check the provider configuration.
          </Alert>
        </Grid>
      );
    }

    return fieldsToRender.map((field: any) => {
      const gridSize = field.gridSize || {
        xs: 12,
        md: 6,
      };

      return (
        <Grid item {...gridSize} key={`${field.name}-${formInstanceKey.current}`}>
          <DynamicField
            name={field.name}
            label={field.label}
            control={control}
            isEditing={isEditing}
            isDisabled={fetchError || isSwitchingProvider}
            type={field.type || 'text'}
            placeholder={field.placeholder || ''}
            icon={field.icon}
            required={field.required}
            options={field.options}
            multiline={field.multiline}
            rows={field.rows}
            modelPlaceholder={field.name === 'model' ? providerConfig.modelPlaceholder : undefined}
            acceptedFileTypes={field.acceptedFileTypes}
            maxFileSize={field.maxFileSize}
            fileProcessor={field.fileProcessor}
            onFileProcessed={(data, fileName) => {
              // Handle file processing results
              if (field.fileProcessor && data) {
                Object.keys(data).forEach((key) => {
                  if (key !== field.name) {
                    // Auto-populate other fields from file data
                    const otherField = fieldsToRender.find((f: any) => f.name === key);
                    // Could implement auto-population logic here
                  }
                });
              }
            }}
          />
        </Grid>
      );
    });
  };

  // Loading state for initial load
  if (!stepperMode && isLoading && !formDataLoaded) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  return (
    <Box sx={{ position: 'relative' }} key={formInstanceKey.current}>
      {/* Info message */}
      <Box
        sx={{
          mb: 3,
          p: 2,
          borderRadius: 1,
          bgcolor: alpha(theme.palette.info.main, 0.04),
          border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
          display: 'flex',
          alignItems: 'flex-start',
          gap: 1,
        }}
      >
        <Iconify
          icon={infoIcon}
          width={20}
          height={20}
          color={theme.palette.info.main}
          style={{ marginTop: 2 }}
        />
        <Box>
          <Typography variant="body2" color="text.secondary">
            {infoMessage || description || getDefaultDescription()}
            {providerConfig?.description && ` ${providerConfig.description}`}
            {fetchError && ' (View-only mode due to connection error)'}
          </Typography>
        </Box>
      </Box>

      {/* Edit button (only for non-stepper mode) */}
      {!stepperMode && !fetchError && (
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
          <Button
            onClick={handleToggleEdit}
            startIcon={<Iconify icon={isEditing ? closeIcon : pencilIcon} />}
            color={isEditing ? 'error' : 'primary'}
            size="small"
            disabled={isSaving}
          >
            {isEditing ? 'Cancel' : 'Edit'}
          </Button>
        </Box>
      )}

      {/* Form container with transition support */}
      <Box
        ref={formContainerRef}
        sx={{
          position: 'relative',
          ...(isSwitchingProvider && {
            height: getTransitionHeight(),
            overflow: 'hidden',
          }),
          transition: 'height 0.3s ease-in-out',
          mb: 2,
        }}
      >
        {/* Provider selector (show if multiple providers) */}
        {providers.length > 1 && (
          <Grid container spacing={2.5} sx={{ mb: 2 }}>
            <Grid item xs={12}>
              <Autocomplete
                size="small"
                disabled={!isEditing || fetchError || isSwitchingProvider}
                value={providers.find((p: any) => p.id === currentProvider) || null}
                onChange={handleProviderChange}
                options={providers}
                getOptionLabel={(option: any) => option.label}
                isOptionEqualToValue={(option: any, value: any) => option.id === value.id}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Provider Type"
                    variant="outlined"
                    sx={{
                      '& .MuiOutlinedInput-root': {
                        '& fieldset': {
                          borderColor:
                            theme.palette.mode === 'dark'
                              ? alpha(theme.palette.common.white, 0.23)
                              : alpha(theme.palette.common.black, 0.23),
                        },
                        '&:hover fieldset': {
                          borderColor:
                            theme.palette.mode === 'dark'
                              ? alpha(theme.palette.common.white, 0.5)
                              : alpha(theme.palette.common.black, 0.87),
                        },
                        '&.Mui-focused fieldset': {
                          borderColor: theme.palette.primary.main,
                        },
                      },
                    }}
                  />
                )}
              />
            </Grid>
          </Grid>
        )}

        {/* Form fields content area with cross-fade transition */}
        <Box sx={{ position: 'relative' }}>
          <Fade
            in={!isSwitchingProvider}
            timeout={{ enter: 300, exit: 200 }}
            style={{
              position: 'relative',
              width: '100%',
              visibility: isSwitchingProvider ? 'hidden' : 'visible',
            }}
          >
            <Grid container spacing={2.5}>
              {renderFieldStructure()}
            </Grid>
          </Fade>

          {/* Switching provider overlay */}
          <Fade
            in={isSwitchingProvider}
            timeout={{ enter: 200, exit: 300 }}
            style={{
              position: 'absolute',
              width: '100%',
              height: '100%',
              top: 0,
              left: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: alpha(theme.palette.background.paper, 0.7),
              backdropFilter: 'blur(2px)',
              zIndex: 10,
              borderRadius: '4px',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <CircularProgress size={20} />
              <Typography variant="body2" color="text.secondary">
                Switching to{' '}
                {providers.find((p: any) => p.id === currentProvider)?.label || 'new provider'}...
              </Typography>
            </Box>
          </Fade>
        </Box>
      </Box>

      {/* Error/Success alerts */}
      {saveError && (
        <Alert severity="error" sx={{ mt: 3 }}>
          {saveError}
        </Alert>
      )}

      {formSubmitSuccess && !saveError && (
        <Alert severity="success" sx={{ mt: 3 }}>
          Configuration saved successfully.
        </Alert>
      )}

      {/* Documentation link */}
      {documentationUrl && (
        <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
          Refer to{' '}
          <a
            href={documentationUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: theme.palette.primary.main }}
          >
            the documentation
          </a>{' '}
          for more information.
        </Alert>
      )}

      {/* Saving indicator */}
      {isSaving && (
        <Box
          sx={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: alpha(theme.palette.background.paper, 0.5),
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            backdropFilter: 'blur(0.1px)',
            borderRadius: 1,
          }}
        >
          <CircularProgress size={32} />
          <Typography variant="body2" sx={{ mt: 2, fontWeight: 500 }}>
            Saving configuration...
          </Typography>
        </Box>
      )}

      {/* Loading indicator for data refresh */}
      {isLoading && formDataLoaded && (
        <Box sx={{ display: 'flex', justifyContent: 'center', my: 2 }}>
          <CircularProgress size={20} />
        </Box>
      )}
    </Box>
  );
});

DynamicForm.displayName = 'DynamicForm';

export default DynamicForm;
