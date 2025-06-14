import React, { useState, useEffect, forwardRef, useImperativeHandle, useRef } from 'react';
import infoIcon from '@iconify-icons/mdi/info-outline';
import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Alert,
  Button,
  Typography,
  CircularProgress,
  Fade,
  Autocomplete,
  TextField,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';
import { useAuthContext } from 'src/auth/hooks';
import { UniversalField } from './universal-field';
import { useUniversalConfigForm } from '../hooks/use-universal-config-form';

// Import for account type support

type ConfigType = 'llm' | 'embedding' | 'storage' | 'url' | 'smtp';

interface UniversalConfigFormProps {
  configType: ConfigType;
  onValidationChange: (isValid: boolean, formData?: any) => void;
  onSaveSuccess?: () => void;
  initialProvider?: string;
  getConfig: () => Promise<any>;
  updateConfig: (config: any) => Promise<any>;
  title: string;
  description: string;
  infoMessage?: string;
  documentationUrl?: string;
  isRequired?: boolean;
  stepperMode?: boolean;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface UniversalConfigFormRef {
  handleSave: () => Promise<SaveResult>;
  getFormData: () => Promise<any>;
  validateForm: () => Promise<boolean>;
  hasFormData: () => Promise<boolean>;
}

const UniversalConfigForm = forwardRef<UniversalConfigFormRef, UniversalConfigFormProps>(
  (
    {
      configType,
      onValidationChange,
      onSaveSuccess,
      initialProvider,
      getConfig,
      updateConfig,
      title,
      description,
      infoMessage,
      documentationUrl,
      isRequired = false,
      stepperMode = false,
    },
    ref
  ) => {
    const theme = useTheme();
    const { user } = useAuthContext();
    const accountType = user?.accountType || 'individual';

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isEditing, setIsEditing] = useState(isRequired || stepperMode);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [formSubmitSuccess, setFormSubmitSuccess] = useState(false);
    const [fetchError, setFetchError] = useState<boolean>(false);
    const [formDataLoaded, setFormDataLoaded] = useState(stepperMode);

    const formInstanceKey = useRef(`${configType}-${Date.now()}-${Math.random()}`);

    // Initialize provider form system with isolated state
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
    } = useUniversalConfigForm(configType, '', accountType);

    useEffect(() => {
      if (stepperMode) {
        const subscription = watch((data, { name, type }) => {
          // Debounce validation changes to prevent excessive updates
          const timeoutId = setTimeout(() => {
            let hasData = false;
            let validationResult = false;

            const isSpecialProvider = providerConfig?.isSpecial;

            if (isSpecialProvider) {
              hasData = true;
              validationResult = true;
            } else if (configType === 'storage' && data.providerType === 'local') {
              hasData = true; // Local storage always has "data"
              validationResult = true; // Always valid for local storage, even with empty optional fields
            } else if (configType === 'url') {
              // For URLs, check if at least one URL is provided
              hasData = !!(data.frontendUrl?.trim() || data.connectorUrl?.trim());
              validationResult = hasData ? isValid : true; // Valid if no data (optional) or if data is valid
            } else {
              // Standard validation for other types
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
            // Only notify parent if this is a meaningful change
            onValidationChange(validationResult, hasData ? data : null);
          }, 100); // Reduced debounce time for better responsiveness

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
      configType,
      providerConfig,
      currentProvider,
    ]);

    // Expose methods to parent component
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => {
        if (stepperMode) {
          // In stepper mode, just validate without saving
          const formData = getValues();

          // Special providers are always valid
          if (providerConfig?.isSpecial) {
            return { success: true };
          }

          if (configType === 'storage' && formData.providerType === 'local') {
            return { success: true };
          }

          // Check if form has meaningful data
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
            // Required forms must have data and be valid
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
          // Optional forms are valid if they have no data, or if they have valid data
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
            handleSubmit(async (data) => {
              try {
                const saveData = {
                  ...data,
                  providerType: currentProvider,
                  modelType: currentProvider,
                  _provider: currentProvider,
                };

                await updateConfig(saveData);
                if (onSaveSuccess) {
                  onSaveSuccess();
                }

                setFormSubmitSuccess(true);
                setIsEditing(false);
                resolve({ success: true });
              } catch (error: any) {
                const errorMessage =
                  error.response?.data?.message ||
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
      },

      getFormData: async (): Promise<any> => {
        const formData = getValues();
        const result = {
          ...formData,
          providerType: currentProvider,
          modelType: currentProvider,
          _provider: currentProvider,
        };
        return result;
      },

      validateForm: async (): Promise<boolean> => {
        const formData = getValues();
        // For special providers (like default), always valid
        if (providerConfig?.isSpecial) {
          return true;
        }

        // Local storage with all optional fields - always valid
        if (configType === 'storage' && formData.providerType === 'local') {
          return true;
        }

        // Check if form has data in non-meta fields
        const nonMetaKeys = Object.keys(formData).filter(
          (key) => key !== 'providerType' && key !== 'modelType' && key !== '_provider'
        );

        const hasData = nonMetaKeys.some((key) => {
          const value = formData[key];
          return value && value.toString().trim() !== '';
        });

        // For optional forms, if no data it's valid, if has data it must be valid
        if (isRequired) {
          const result = hasData && isValid;
          return result;
        }
        const result = !hasData || isValid;
        return result;
      },

      hasFormData: async (): Promise<boolean> => {
        const formData = getValues();

        // For special providers, consider them as having data
        if (providerConfig?.isSpecial) {
          return true;
        }

        // Local storage always has "data" (even if fields are empty)
        if (configType === 'storage' && formData.providerType === 'local') {
          return true;
        }

        // For URLs, consider it has data if at least one URL is provided
        if (configType === 'url') {
          const hasUrlData = !!(formData.frontendUrl?.trim() || formData.connectorUrl?.trim());
          return hasUrlData;
        }

        // Check if any non-meta field has data
        const nonMetaKeys = Object.keys(formData).filter(
          (key) => key !== 'providerType' && key !== 'modelType' && key !== '_provider'
        );

        const hasData = nonMetaKeys.some((key) => {
          const value = formData[key];
          return value && value.toString().trim() !== '';
        });

        return hasData;
      },
    }));

    // Load config data (only in non-stepper mode)
    useEffect(() => {
      if (!stepperMode && !formDataLoaded) {
        const fetchConfig = async () => {
          setIsLoading(true);
          try {
            const config = await getConfig();
            setFetchError(false);

            if (config) {
              const providerType = config.providerType || config.modelType;
              if (providerType && providerType !== currentProvider) {
                switchProvider(providerType, null);
              }
              initializeForm(config);
            }
            setFormDataLoaded(true);
          } catch (error) {
            console.error(`Failed to load ${configType} configuration:`, error);
            setFetchError(true);
            setSaveError('Failed to load configuration. View-only mode enabled.');
          } finally {
            setIsLoading(false);
          }
        };

        fetchConfig();
      }
    }, [
      stepperMode,
      formDataLoaded,
      getConfig,
      configType,
      switchProvider,
      initializeForm,
      currentProvider,
    ]);

    // Reset saveError when it changes
    useEffect(() => {
      if (saveError) {
        const timer = setTimeout(() => {
          setSaveError(null);
        }, 5000);

        return () => clearTimeout(timer);
      }

      return () => {};
    }, [saveError]);

    // Notify parent of validation status (for non-stepper mode)
    useEffect(() => {
      if (!stepperMode) {
        if (isSwitchingProvider) {
          return () => {};
        }

        const handler = setTimeout(() => {
          const shouldReportValid = isRequired ? isValid : isValid && isEditing;
          onValidationChange(shouldReportValid && !isSwitchingProvider);
        }, 100);

        return () => clearTimeout(handler);
      }
      return () => {};
    }, [stepperMode, isValid, isEditing, onValidationChange, isSwitchingProvider, isRequired]);

    // Provider change handler
    const handleProviderChange = (event: any, newValue: any) => {
      if (newValue) {
        switchProvider(newValue.id);
      }
    };

    // Toggle edit mode
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - reload current data
        setIsEditing(false);
        setSaveError(null);
        setFormSubmitSuccess(false);
        // Reset form to original state
        if (formDataLoaded) {
          getConfig()
            .then((config) => {
              if (config) {
                const providerType = config.providerType || config.modelType;
                if (providerType) {
                  switchProvider(providerType, null);
                  initializeForm(config);
                }
              }
            })
            .catch((error) => {
              console.error('Error reloading configuration:', error);
              setFetchError(true);
              setSaveError('Failed to reload configuration.');
            });
        }
      } else {
        setIsEditing(true);
      }
    };

    // Render form fields dynamically
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

      // Special handling for providers with no fields (like 'default')
      if (providerConfig.isSpecial) {
        return (
          <Grid item xs={12}>
            <Alert severity="info" sx={{ mt: 1 }}>
              {providerConfig.description}
            </Alert>
          </Grid>
        );
      }

      // Get fields from providerConfig.allFields
      const fieldsToRender = providerConfig.allFields || [];

      if (fieldsToRender.length === 0) {
        console.warn(
          `No fields found for provider ${currentProvider} in config type ${configType} (instance: ${formInstanceKey.current})`
        );
        return (
          <Grid item xs={12}>
            <Alert severity="warning" sx={{ mt: 1 }}>
              No fields configured for this provider. Please check the provider configuration.
            </Alert>
          </Grid>
        );
      }

      // Render all fields for the current provider
      return fieldsToRender.map((field: any) => {
        const gridSize = field.gridSize || { xs: 12, md: 6 };

        return (
          <Grid item {...gridSize} key={`${field.name}-${formInstanceKey.current}`}>
            <UniversalField
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
              modelPlaceholder={
                field.name === 'model' ? providerConfig.modelPlaceholder : undefined
              }
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
                    }
                  });
                }
              }}
            />
          </Grid>
        );
      });
    };

    // Show loading state only for initial load (non-stepper mode)
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
              {infoMessage || description}
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

        {/* Provider selector (show if multiple providers) */}
        {providers.length > 1 && (
          <Grid container spacing={2.5} sx={{ mb: 2 }}>
            <Grid item xs={12}>
              <Autocomplete
                size="small"
                disabled={!isEditing || fetchError || isSwitchingProvider}
                value={providers.find((p) => p.id === currentProvider) || null}
                onChange={handleProviderChange}
                options={providers}
                getOptionLabel={(option) => option.label}
                isOptionEqualToValue={(option, value) => option.id === value.id}
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

        {/* Form fields content area */}
        <Box sx={{ position: 'relative' }}>
          {/* Main form fields with cross-fade transition */}
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
                {providers.find((p) => p.id === currentProvider)?.label || 'new provider'}...
              </Typography>
            </Box>
          </Fade>
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
      </Box>
    );
  }
);

export default UniversalConfigForm;