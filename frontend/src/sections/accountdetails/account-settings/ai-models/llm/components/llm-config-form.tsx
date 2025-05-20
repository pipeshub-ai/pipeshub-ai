// components/LlmConfigForm.tsx (ESLint Error Fixed)

import type { SubmitHandler } from 'react-hook-form';
import type { SelectChangeEvent } from '@mui/material';

import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';
import infoIcon from '@iconify-icons/mdi/info-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle, useCallback, useRef } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Alert,
  Select,
  Button,
  MenuItem,
  Typography,
  InputLabel,
  FormControl,
  CircularProgress,
  Fade,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';
import { ProviderField } from './provider-field';
import { providers, ProviderType, LlmFormValues } from '../providers';
import { useProviderForms } from '../hooks/use-provider-form';
import { getLlmConfig, updateLlmConfig } from '../services/llm-config';

interface LlmConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  initialProvider?: ProviderType;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface LlmConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

const LlmConfigForm = forwardRef<LlmConfigFormRef, LlmConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, initialProvider = 'openAI' }, ref) => {
    const theme = useTheme();
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [formSubmitSuccess, setFormSubmitSuccess] = useState(false);
    const [fetchError, setFetchError] = useState<boolean>(false);
    const [formDataLoaded, setFormDataLoaded] = useState(false);
    
    // Store the original API configuration for reverting on cancel
    const originalApiConfigRef = useRef<LlmFormValues | null>(null);
    
    // Track provider heights to manage transitions
    const formContainerRef = useRef<HTMLDivElement>(null);
    const [providerHeights, setProviderHeights] = useState<Record<ProviderType, number>>({} as any);
    const [isInitialProviderLoaded, setIsInitialProviderLoaded] = useState(false);

    // Initialize provider form system with enhanced state management
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
    } = useProviderForms(initialProvider);

    // Memoized fetch config function with optimized performance
    const fetchConfig = useCallback(async (forceRefresh = false) => {
      // Don't load if already loading or data is already loaded and no refresh requested
      if (isLoading || (formDataLoaded && !forceRefresh)) return; 
      
      setIsLoading(true);
      try {
        const config = await getLlmConfig();
        setFetchError(false);

        if (config) {
          // Store original API config for use when canceling edits
          originalApiConfigRef.current = config;
          
          // If data is loaded for the first time, set provider to match config
          if (!formDataLoaded) {
            if (config.modelType) {
              switchProvider(config.modelType, null);
            }
          }
          
          // Initialize form data without triggering validation
          initializeForm(config);
          setFormDataLoaded(true);
        }
      } catch (error) {
        console.error('Failed to load LLM configuration:', error);
        setFetchError(true);
        setSaveError('Failed to load configuration. View-only mode enabled.');
      } finally {
        setIsLoading(false);
      }
    }, [isLoading, formDataLoaded, switchProvider, initializeForm]);

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => {
        try {
          setIsSaving(true);
          setSaveError(null);
          setFormSubmitSuccess(false);

          // Create a promise that will resolve with the result of the form submission
          return await new Promise<SaveResult>((resolve) => {
            handleSubmit(async (data) => {
              try {
                // Ensure the data has the correct model type
                const saveData = {
                  ...data,
                  modelType: currentProvider,
                  _provider: currentProvider,
                } as LlmFormValues;
                
                await updateLlmConfig(saveData);
                if (onSaveSuccess) {
                  onSaveSuccess();
                }
                setIsEditing(false);
                setFormSubmitSuccess(true);
                
                // Update our original API config reference with the newly saved data
                originalApiConfigRef.current = saveData;
                
                // After successful save, reload the config to ensure consistency
                // but set a short timeout to allow UI to update first
                setTimeout(() => {
                  fetchConfig(true);
                }, 100);
                
                resolve({ success: true });
              } catch (error) {
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

    // Load existing configuration on mount only once
    useEffect(() => {
      if (!formDataLoaded) {
        fetchConfig();
      }
    }, [fetchConfig, formDataLoaded]);

    // Measure and store container height for the current provider
    useEffect(() => {
      if (formContainerRef.current && !isSwitchingProvider && !isLoading) {
        // Measure height after a short delay to ensure all elements are rendered
        const timer = setTimeout(() => {
          if (formContainerRef.current) {
            const height = formContainerRef.current.getBoundingClientRect().height;
            if (height > 0) {
              // Only store heights for providers that have fully loaded
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

    // Improved provider change handler for better UX
    const handleProviderChange = (event: SelectChangeEvent) => {
      const newProvider = event.target.value as ProviderType;
      switchProvider(newProvider);
    };

    // Handle edit mode toggle with improved cancel behavior
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - reset to original API config
        setIsEditing(false);
        setSaveError(null);
        
        // Use the original API config to revert changes
        if (originalApiConfigRef.current) {
          // This will reset to the correct provider and values from the original API config
          const originalProvider = originalApiConfigRef.current.modelType;
          resetToProvider(originalProvider, originalApiConfigRef.current);
        } else {
          // Fallback to reloading from API if we don't have an original config stored
          fetchConfig(true);
        }
      } else {
        // Simply enable editing mode without any heavy operations or validation
        setIsEditing(true);
      }
    };

    // Calculate expected height for the current transition
    const getTransitionHeight = () => {
      // During switching, use the target provider's height if we have it
      if (isSwitchingProvider && providerHeights[currentProvider]) {
        return providerHeights[currentProvider];
      }
      
      // If we don't have the target provider's height, use current height
      // or return auto if we don't have any heights yet
      return providerHeights[currentProvider] || 'auto';
    };

    // Pre-render all fields to keep consistent structure
    const renderFieldStructure = () => {
      // Common fields that all providers have
      const commonFields = (
        <>
          <Grid item xs={12} md={6}>
            <ProviderField
              name="apiKey"
              label="API Key"
              control={control}
              isEditing={isEditing}
              isDisabled={fetchError || isSwitchingProvider}
              type="password"
              placeholder="Your API Key"
            />
          </Grid>

          <Grid item xs={12} md={6}>
            <ProviderField
              name="model"
              label="Model Name"
              control={control}
              isEditing={isEditing}
              isDisabled={fetchError || isSwitchingProvider}
              placeholder={providerConfig?.modelPlaceholder || ''}
            />
          </Grid>
        </>
      );

      // Optional provider-specific fields
      const additionalFields = providerConfig?.additionalFields?.map(field => (
        <Grid item xs={12} md={6} key={field.name}>
          <ProviderField
            name={field.name}
            label={field.label}
            control={control}
            isEditing={isEditing}
            isDisabled={fetchError || isSwitchingProvider}
            type={field.type || 'text'}
            placeholder={field.placeholder}
            icon={field.icon}
          />
        </Grid>
      )) || [];

      return (
        <>
          {commonFields}
          {additionalFields}
        </>
      );
    };

    // Show loading state only for initial load
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
              Configure your LLM provider to enable AI capabilities in your application.
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
          {/* Provider Type selector - always visible */}
          <Grid container spacing={2.5} sx={{ mb: 2 }}>
            <Grid item xs={12}>
              <FormControl fullWidth size="small" disabled={!isEditing || fetchError || isSwitchingProvider}>
                <InputLabel>Provider Type</InputLabel>
                <Select
                  name="modelType"
                  value={currentProvider}
                  label="Provider Type"
                  onChange={handleProviderChange}
                >
                  {providers.map(provider => (
                    <MenuItem key={provider.id} value={provider.id}>
                      {provider.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
          </Grid>

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

            {/* Switching provider overlay - only shown during transition */}
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

        {/* Show appropriate loading indicators */}
        {isSaving && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
            <CircularProgress size={24} />
          </Box>
        )}
        
        {isLoading && formDataLoaded && (
          <Box sx={{ display: 'flex', justifyContent: 'center', my: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}
        
        <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
          Refer to{' '}
          <Link href="https://docs.pipeshub.com/ai-models/overview" target="_blank" rel="noopener">
            the documentation
          </Link>{' '}
          for more information.
        </Alert>
      </>
    );
  }
);

export default LlmConfigForm;