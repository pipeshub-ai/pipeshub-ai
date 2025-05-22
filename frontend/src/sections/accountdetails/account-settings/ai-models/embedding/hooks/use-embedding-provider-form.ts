// hooks/use-embedding-provider-form.ts

import { useState, useEffect, useCallback, useRef } from 'react';
import { useForm, UseFormReturn } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import {
  EmbeddingProviderType,
  EmbeddingFormValues,
  EmbeddingProviderConfig,
  getEmbeddingProviderById,
  embeddingProviders,
} from '../providers';

// Define a custom return type that extends UseFormReturn with providerConfig
type EmbeddingProviderFormReturn = UseFormReturn<any> & {
  providerConfig: EmbeddingProviderConfig | undefined;
};

export const useEmbeddingProviderForm = (
  providerType: EmbeddingProviderType
): EmbeddingProviderFormReturn => {
  const providerConfig = getEmbeddingProviderById(providerType);

  // Create a form for this provider type
  const form = useForm({
    resolver: providerConfig ? zodResolver(providerConfig.schema) : undefined,
    mode: 'onChange',
    defaultValues: providerConfig
      ? {
          ...providerConfig.defaultValues,
          _provider: providerType,
        }
      : {
          modelType: providerType,
          _provider: providerType,
        },
  });

  // Return form with providerConfig
  return {
    ...form,
    providerConfig,
  };
};

/**
 * This hook manages form state across different provider types
 * with proper isolation of field values to prevent cross-contamination
 */
export const useEmbeddingProviderForms = (initialProvider: EmbeddingProviderType = 'openAI') => {
  const [currentProvider, setCurrentProvider] = useState<EmbeddingProviderType>(initialProvider);
  // Use ref to store all provider forms to avoid recreation
  const providersFormStateRef = useRef<Record<EmbeddingProviderType, EmbeddingFormValues>>(
    {} as any
  );
  // Track if initial data has been loaded
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  // Track if we're currently switching providers to prevent validation
  const [isSwitchingProvider, setIsSwitchingProvider] = useState(false);

  // Get form for current provider
  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid, isDirty, errors },
    getValues,
    watch,
    clearErrors,
    providerConfig,
  } = useEmbeddingProviderForm(currentProvider);

  // Cache form values when they change to maintain state across provider switches
  useEffect(() => {
    // Skip watching when switching providers to avoid validation
    if (isSwitchingProvider)
      return () => {
        // No cleanup needed when switching
      };

    const subscription = watch((data) => {
      if (data && Object.keys(data).length > 0) {
        // Store form values for current provider
        providersFormStateRef.current[currentProvider] = {
          ...data,
          modelType: currentProvider,
          _provider: currentProvider,
        } as EmbeddingFormValues;
      }
    });

    return () => subscription.unsubscribe();
  }, [currentProvider, watch, isSwitchingProvider]);

  // Efficient reset that avoids unnecessary re-renders
  const resetForm = useCallback(
    (data: any) => {
      if (!data) return;

      // Ensure we're only setting data that belongs to this provider
      // or data that has been explicitly marked for this provider
      const isDataForThisProvider =
        data.modelType === currentProvider || data._provider === currentProvider || !data._provider; // Also accept data without a provider tag (like initial API data)

      if (isDataForThisProvider) {
        // Store the data for the current provider
        providersFormStateRef.current[currentProvider] = {
          ...data,
          modelType: currentProvider,
          _provider: currentProvider,
        } as EmbeddingFormValues;

        // Clear validation before resetting to prevent flashes
        clearErrors();

        // Reset the form with this data
        reset(data, {
          keepDirty: false,
          keepValues: true,
          keepDefaultValues: true,
          keepErrors: false,
          keepIsValid: false,
          keepTouched: false,
        });
      } else if (providersFormStateRef.current[currentProvider]) {
        reset(providersFormStateRef.current[currentProvider], {
          keepErrors: false,
          keepIsValid: false,
        });
      } else {
        // Use provider defaults as fallback
        const defaults = providerConfig?.defaultValues
          ? {
              ...providerConfig.defaultValues,
              _provider: currentProvider,
            }
          : {
              modelType: currentProvider,
              _provider: currentProvider,
            };

        reset(defaults, {
          keepErrors: false,
          keepIsValid: false,
        });
      }
    },
    [reset, currentProvider, providerConfig, clearErrors]
  );

  // Initialize form with API data - optimized to avoid unnecessary validations
  const initializeForm = useCallback(
    (apiData: EmbeddingFormValues | null) => {
      if (!apiData) return;

      // Store the API data for all providers to avoid data loss during provider switching
      if (apiData.modelType) {
        const provider = apiData.modelType;

        // Keep a clean copy of the API data for each provider
        providersFormStateRef.current[provider] = {
          ...apiData,
          _provider: provider,
        } as EmbeddingFormValues;

        // If this is the current provider, initialize its form
        if (provider === currentProvider) {
          // Disable validation during initialization
          clearErrors();

          // Reset form with API data
          reset(
            {
              ...apiData,
              _provider: provider,
            },
            {
              keepErrors: false,
              keepIsValid: false,
              keepTouched: false,
            }
          );
        }
      }

      setInitialDataLoaded(true);
    },
    [currentProvider, reset, clearErrors]
  );

  // Optimized provider switching with minimal UI flicker
  const switchProvider = useCallback(
    (newProvider: EmbeddingProviderType, currentValues: any = null) => {
      // Don't do anything if switching to the same provider
      if (newProvider === currentProvider) return;

      // Set switching flag to disable validation during switch
      setIsSwitchingProvider(true);

      // Save current form values before switching
      if (currentValues || Object.keys(getValues()).length > 0) {
        const valuesToSave = currentValues || getValues();
        providersFormStateRef.current[currentProvider] = {
          ...valuesToSave,
          modelType: currentProvider,
          _provider: currentProvider,
        } as EmbeddingFormValues;
      }

      // Performance optimization: Use requestAnimationFrame to ensure UI updates
      requestAnimationFrame(() => {
        // Switch to new provider
        setCurrentProvider(newProvider);

        // Preemptively clear errors to prevent validation during transition
        clearErrors();

        // Schedule the switch completion after a short delay
        // This creates a smoother transition effect
        setTimeout(() => {
          try {
            // Reset form with the new provider's values
            if (providersFormStateRef.current[newProvider]) {
              reset(providersFormStateRef.current[newProvider], {
                keepErrors: false,
                keepIsValid: false,
              });
            } else {
              // Use defaults if no stored values
              const newProviderConfig = getEmbeddingProviderById(newProvider);
              const defaults = newProviderConfig?.defaultValues
                ? { ...newProviderConfig.defaultValues, _provider: newProvider }
                : { modelType: newProvider, _provider: newProvider };

              reset(defaults, {
                keepErrors: false,
                keepIsValid: false,
              });
            }

            // Complete the transition after form is reset
            setTimeout(() => {
              setIsSwitchingProvider(false);
            }, 150); // Short delay for smoother animation
          } catch (error) {
            console.error('Error during provider switch:', error);
            setIsSwitchingProvider(false);
          }
        }, 100); // Short delay for smoother transition
      });
    },
    [currentProvider, getValues, reset, clearErrors]
  );

  // Reset to a specific provider with data
  const resetToProvider = useCallback(
    (providerType: EmbeddingProviderType, data: EmbeddingFormValues) => {
      // If already on the right provider, just update the data
      if (providerType === currentProvider) {
        clearErrors();
        reset(
          {
            ...data,
            modelType: providerType,
            _provider: providerType,
          },
          {
            keepErrors: false,
            keepIsValid: false,
            keepTouched: false,
          }
        );

        // Also update the stored form state
        providersFormStateRef.current[providerType] = {
          ...data,
          modelType: providerType,
          _provider: providerType,
        } as EmbeddingFormValues;

        return;
      }

      // Otherwise, switch to the new provider with data
      setIsSwitchingProvider(true);

      // Store the data first
      providersFormStateRef.current[providerType] = {
        ...data,
        modelType: providerType,
        _provider: providerType,
      } as EmbeddingFormValues;

      // Switch provider with animation
      requestAnimationFrame(() => {
        setCurrentProvider(providerType);

        // Reset form with stored data after a short delay
        setTimeout(() => {
          clearErrors();
          reset(
            {
              ...data,
              modelType: providerType,
              _provider: providerType,
            },
            {
              keepErrors: false,
              keepIsValid: false,
              keepTouched: false,
            }
          );

          // Complete the transition
          setTimeout(() => {
            setIsSwitchingProvider(false);
          }, 150);
        }, 50);
      });
    },
    [currentProvider, reset, clearErrors]
  );

  // Getter for all form states (for debugging)
  const getAllFormStates = useCallback(() => ({ ...providersFormStateRef.current }), []);

  return {
    currentProvider,
    switchProvider,
    resetToProvider,
    control,
    handleSubmit,
    reset: resetForm,
    initializeForm,
    isValid,
    isSwitchingProvider,
    providerConfig,
    providers: embeddingProviders,
    getAllFormStates,
    formStates: providersFormStateRef.current,
    initialDataLoaded,
    errors,
  };
};
