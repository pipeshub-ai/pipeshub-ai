import React, { useState, useEffect, forwardRef, useImperativeHandle, useCallback, useRef } from 'react';
import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';
import infoIcon from '@iconify-icons/mdi/info-outline';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Alert,
  Select,
  Button,
  MenuItem,
  Typography,
  InputLabel,
  FormControl,
  CircularProgress,
  Fade,
  Autocomplete,
  TextField,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';
import { UniversalField } from './universal-field';
import { useUniversalForm } from '../hooks/use-universal-form';

import { 
  getLlmProviders, 
  getLlmProviderById,
  type LlmFormValues,
} from '../models/llm/providers';

import { 
  getEmbeddingProviders, 
  getEmbeddingProviderById,
  type EmbeddingFormValues,
} from '../models/embedding/providers';

interface UniversalModelFormProps {
  modelType: 'llm' | 'embedding';
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  initialProvider?: string;
  getConfig: () => Promise<any>;
  updateConfig: (config: any) => Promise<any>;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface UniversalModelFormRef {
  handleSave: () => Promise<SaveResult>;
}

const UniversalModelForm = forwardRef<UniversalModelFormRef, UniversalModelFormProps>(
  ({ modelType, onValidationChange, onSaveSuccess, initialProvider, getConfig, updateConfig }, ref) => {
    const theme = useTheme();
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [formSubmitSuccess, setFormSubmitSuccess] = useState(false);
    const [fetchError, setFetchError] = useState<boolean>(false);
    const [formDataLoaded, setFormDataLoaded] = useState(false);
    
    const providers = modelType === 'llm' ? getLlmProviders() : getEmbeddingProviders();
    const getProviderById = modelType === 'llm' ? getLlmProviderById : getEmbeddingProviderById;
    
    const originalApiConfigRef = useRef<any>(null);
    
    const formContainerRef = useRef<HTMLDivElement>(null);
    const [providerHeights, setProviderHeights] = useState<Record<string, number>>({});
    const [isInitialProviderLoaded, setIsInitialProviderLoaded] = useState(false);

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
      initialDataLoaded,
      errors,
      resetToProvider,
    } = useUniversalForm(modelType, initialProvider || providers[0]?.id || '');

    const fetchConfig = useCallback(async (forceRefresh = false) => {
      if (isLoading || (formDataLoaded && !forceRefresh)) return; 
      
      setIsLoading(true);
      try {
        const config = await getConfig();
        setFetchError(false);

        if (config) {
          originalApiConfigRef.current = config;
          
          if (!formDataLoaded) {
            if (config.modelType) {
              switchProvider(config.modelType, null);
            }
          }
          
          initializeForm(config);
          setFormDataLoaded(true);
        }
      } catch (error) {
        console.error(`Failed to load ${modelType} configuration:`, error);
        setFetchError(true);
        setSaveError('Failed to load configuration. View-only mode enabled.');
      } finally {
        setIsLoading(false);
      }
    }, [isLoading, formDataLoaded, switchProvider, initializeForm, getConfig, modelType]);

    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => {
        try {
          setIsSaving(true);
          setSaveError(null);
          setFormSubmitSuccess(false);

          return await new Promise<SaveResult>((resolve) => {
            handleSubmit(async (data) => {
              try {
                // Ensure the data has the correct model type
                const saveData = {
                  ...data,
                  modelType: currentProvider,
                  _provider: currentProvider,
                };
                
                await updateConfig(saveData);
                if (onSaveSuccess) {
                  onSaveSuccess();
                }
                setIsEditing(false);
                setFormSubmitSuccess(true);
                
                originalApiConfigRef.current = saveData;

                setTimeout(() => {
                  fetchConfig(true);
                }, 100);
                
                resolve({ success: true });
              } catch (error: any) {
                const errorMessage =
                  error.response?.data?.message || `Failed to save ${providerConfig?.label} configuration`;
                setSaveError(errorMessage);
                console.error(`Error saving ${providerConfig?.label} configuration:`, error);
                setFormSubmitSuccess(false);
                resolve({ success: false, error: errorMessage });
              } finally {
                setIsSaving(false);
              }
            })();
          });
        } catch (error) {
          setIsSaving(false);
          console.error('Error in handleSave:', error);
          return {
            success: false,
            error: 'Unexpected error occurred during save operation',
          };
        }
      },
    }));

    useEffect(() => {
      if (!formDataLoaded) {
        fetchConfig();
      }
    }, [fetchConfig, formDataLoaded]);

    useEffect(() => {
      if (formContainerRef.current && !isSwitchingProvider && !isLoading) {
        const timer = setTimeout(() => {
          if (formContainerRef.current) {
            const height = formContainerRef.current.getBoundingClientRect().height;
            if (height > 0) {
              setProviderHeights(prev => ({
                ...prev,
                [currentProvider]: height
              }));
              
              // Mark initial provider as loaded
              if (!isInitialProviderLoaded) {
                setIsInitialProviderLoaded(true);
              }
            }
          }
        }, 100);
        
        // Return cleanup function
        return () => {
          clearTimeout(timer);
        };
      }
      
      // Return empty cleanup function for consistent return
      return () => {
        // No cleanup needed when condition isn't met
      };
    }, [currentProvider, isSwitchingProvider, isLoading, formDataLoaded, isInitialProviderLoaded]);

    // Reset saveError when it changes
    useEffect(() => {
      if (saveError) {
        const timer = setTimeout(() => {
          setSaveError(null);
        }, 5000);
        
        return () => {
          clearTimeout(timer);
        };
      }
      
      // Return empty cleanup function for consistent return
      return () => {
        // No cleanup needed when condition isn't met
      };
    }, [saveError]);

    // Notify parent of validation status - debounced to prevent excessive updates
    useEffect(() => {
      // Don't update validation during provider switch
      if (isSwitchingProvider) {
        return () => {
          // No cleanup needed
        };
      }
      
      const handler = setTimeout(() => {
        // Only consider validation when editing and not switching providers
        onValidationChange(isValid && isEditing && !isSwitchingProvider);
      }, 100);
      
      return () => {
        clearTimeout(handler);
      };
    }, [isValid, isEditing, onValidationChange, isSwitchingProvider]);

    const handleProviderChange = (event: any, newValue: any) => {
      if (newValue) {
        switchProvider(newValue.id);
      }
    };

    const handleToggleEdit = () => {
      if (isEditing) {
        setIsEditing(false);
        setSaveError(null);
        
        if (originalApiConfigRef.current) {
          const originalProvider = originalApiConfigRef.current.modelType;
          resetToProvider(originalProvider, originalApiConfigRef.current);
        } else {
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

    const renderFieldStructure = () => {
      if (!providerConfig) return null;
      if (currentProvider === 'default') {
        return (
          <Grid item xs={12}>
            <Alert severity="info" sx={{ mt: 1 }}>
              Using the default embedding model provided by the system. No additional
              configuration required.
            </Alert>
          </Grid>
        );
      }

      return providerConfig.allFields?.map((field:any) => (
        <Grid item xs={12} md={field.name === 'model' && providerConfig.allFields?.length === 2 ? 6 : 6} key={field.name}>
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
            modelPlaceholder={field.name === 'model' ? providerConfig.modelPlaceholder : undefined}          />
        </Grid>
      ));
    };

    if (isLoading && !formDataLoaded) {
      return (
        <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
          <CircularProgress size={24} />
        </Box>
      );
    }

    return (
      <>
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
              Configure your {modelType.toUpperCase()} model to enable {modelType === 'llm' ? 'AI capabilities' : 'semantic search and document retrieval'} in
              your application.
              {' '}{providerConfig?.description}
              {fetchError && ' (View-only mode due to connection error)'}
            </Typography>
          </Box>
        </Box>

        {/* Only show Edit button if there was no fetch error */}
        {!fetchError && (
          <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
            <Button
              onClick={handleToggleEdit}
              startIcon={<Iconify icon={isEditing ? closeIcon : pencilIcon} />}
              color={isEditing ? 'error' : 'primary'}
              size="small"
            >
              {isEditing ? 'Cancel' : 'Edit'}
            </Button>
          </Box>
        )}

        {/* Adaptive height container */}
        <Box
          ref={formContainerRef}
          sx={{
            position: 'relative',
            // Apply height during transitions to prevent jumps
            ...(isSwitchingProvider && {
              height: getTransitionHeight(),
              overflow: 'hidden'
            }),
            // Smooth height transitions
            transition: 'height 0.3s ease-in-out',
            mb: 2
          }}
        >
          {/* Provider Type selector - now with Autocomplete */}
          <Grid container spacing={2.5} sx={{ mb: 2 }}>
            <Grid item xs={12}>
              <Autocomplete
                size="small"
                disabled={!isEditing || fetchError || isSwitchingProvider}
                value={providers.find(p => p.id === currentProvider) || null}
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
                          borderColor: theme.palette.mode === 'dark' 
                            ? alpha(theme.palette.common.white, 0.23)
                            : alpha(theme.palette.common.black, 0.23),
                        },
                        '&:hover fieldset': {
                          borderColor: theme.palette.mode === 'dark'
                            ? alpha(theme.palette.common.white, 0.5)
                            : alpha(theme.palette.common.black, 0.87),
                        },
                        '&.Mui-focused fieldset': {
                          borderColor: theme.palette.primary.main,
                        },
                      },
                      '& .MuiInputLabel-root': {
                        color: theme.palette.text.secondary,
                        '&.Mui-focused': {
                          color: theme.palette.primary.main,
                        },
                      },
                    }}
                  />
                )}
                renderOption={(props, option) => (
                  <Box
                    component="li"
                    {...props}
                    sx={{
                      '&:hover': {
                        backgroundColor: theme.palette.mode === 'dark'
                          ? alpha(theme.palette.common.white, 0.08)
                          : alpha(theme.palette.common.black, 0.04),
                      },
                      '&.Mui-focused': {
                        backgroundColor: theme.palette.mode === 'dark'
                          ? alpha(theme.palette.primary.main, 0.12)
                          : alpha(theme.palette.primary.main, 0.12),
                      },
                    }}
                  >
                    {option.label}
                  </Box>
                )}
                sx={{
                  '& .MuiAutocomplete-listbox': {
                    backgroundColor: theme.palette.background.paper,
                    border: `1px solid ${theme.palette.mode === 'dark' 
                      ? alpha(theme.palette.common.white, 0.23)
                      : alpha(theme.palette.common.black, 0.23)}`,
                    borderRadius: 1,
                    boxShadow: theme.shadows[8],
                  },
                  '& .MuiAutocomplete-paper': {
                    backgroundColor: theme.palette.background.paper,
                  },
                }}
              />
            </Grid>
          </Grid>

          {/* Form fields content area */}
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
                  Switching to {providers.find(p => p.id === currentProvider)?.label || 'new provider'}...
                </Typography>
              </Box>
            </Fade>
          </Box>
        </Box>
        
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

        {/* Show appropriate loading indicators */}
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
        
        {isLoading && formDataLoaded && (
          <Box sx={{ display: 'flex', justifyContent: 'center', my: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}
      </>
    );
  }
);

export default UniversalModelForm;