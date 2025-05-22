// hooks/use-provider-form.ts 

import { useState, useEffect, useCallback, useRef } from 'react';
import { useForm, UseFormReturn } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { ProviderType, LlmFormValues, ProviderConfig } from '../providers/types';
import { getProviderById, providers } from '../providers/constants';

// Define a custom return type that extends UseFormReturn with providerConfig
type ProviderFormReturn = UseFormReturn<any> & {
  providerConfig: ProviderConfig | undefined;
};

export const useProviderForm = (providerType: ProviderType): ProviderFormReturn => {
  const providerConfig = getProviderById(providerType);
  
  // Create a form for this provider type
  const form = useForm({
    resolver: providerConfig ? zodResolver(providerConfig.schema) : undefined,
    mode: 'onTouched',
    defaultValues: providerConfig ? {
      ...providerConfig.defaultValues,
      _provider: providerType
    } : {
      modelType: providerType,
      apiKey: '',
      model: '',
      _provider: providerType
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
export const useProviderForms = (initialProvider: ProviderType = 'openAI') => {
  const [currentProvider, setCurrentProvider] = useState<ProviderType>(initialProvider);
  // Use ref to store form states with namespaced fields - Fixed typing with Partial
  const formStatesRef = useRef<Partial<Record<ProviderType, LlmFormValues>>>({});
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
  } = useProviderForm(currentProvider);

  // Watch for form changes and update the form states ref with namespaced values
  useEffect(() => {
    // Skip watching when switching providers to avoid validation
    if (isSwitchingProvider) {
      return () => {
        // No cleanup needed when switching providers
      };
    }
    
    const subscription = watch((data) => {
      if (data && Object.keys(data).length > 0) {
        // Create a properly namespaced copy to avoid field leakage between providers
        const namespacedData = {
          ...data,
          // Always preserve the model type
          modelType: currentProvider,
          // Add a namespace field to track which provider this belongs to
          _provider: currentProvider,
        } as LlmFormValues;
        
        // Store in our ref
        formStatesRef.current[currentProvider] = namespacedData;
      }
    });
    
    return () => subscription.unsubscribe();
  }, [currentProvider, watch, isSwitchingProvider]);

  // Efficient reset that avoids unnecessary re-renders and preserves isolation
  const resetForm = useCallback((data: any) => {
    if (!data) return;
    
    // Ensure we're only setting data that belongs to this provider
    // or data that has been explicitly marked for this provider
    const isDataForThisProvider = 
      data.modelType === currentProvider || 
      data._provider === currentProvider || 
      !data._provider; // Also accept data without a provider tag (like initial API data)

    if (isDataForThisProvider) {
      // Store the data in our ref for the current provider with proper namespace
      const namespacedData = {
        ...data,
        modelType: currentProvider,
        _provider: currentProvider,
      } as LlmFormValues;
      
      formStatesRef.current[currentProvider] = namespacedData;
      
      // Clear errors before reset to prevent validation during reset
      clearErrors();
      
      // Reset the form with this data
      reset({
        ...data,
        _provider: currentProvider
      }, {
        keepDirty: false,
        keepValues: true,
        keepDefaultValues: true,
        keepErrors: false,
        keepIsValid: false,
        keepTouched: false,
      });
    } else if (formStatesRef.current[currentProvider]) {
      // If data is for a different provider but we have stored data for current provider
      reset(formStatesRef.current[currentProvider], {
        keepErrors: false,
        keepIsValid: false
      });
    } else {
      // Use provider defaults as fallback if no stored data exists
      const defaults = providerConfig?.defaultValues ? {
        ...providerConfig.defaultValues,
        _provider: currentProvider
      } : {
        modelType: currentProvider,
        apiKey: '',
        model: '',
        _provider: currentProvider
      };
      
      reset(defaults, {
        keepErrors: false,
        keepIsValid: false
      });
    }
  }, [reset, currentProvider, providerConfig, clearErrors]);

  // Initialize form with API data - optimized
  const initializeForm = useCallback((apiData: LlmFormValues | null) => {
    if (!apiData) return;
    
    // First store the initial values for all providers to avoid loss
    if (apiData.modelType) {
      // Store the API data for its specific provider
      const provider = apiData.modelType;
      formStatesRef.current[provider] = {
        ...apiData,
        _provider: provider
      } as LlmFormValues;
      
      // If this is the current provider, reset its form with the data
      if (provider === currentProvider) {
        // Efficient reset without triggering validation
        clearErrors();
        reset({
          ...apiData,
          _provider: provider
        }, {
          keepErrors: false,
          keepIsValid: false,
          keepTouched: false
        });
      }
    }
    
    setInitialDataLoaded(true);
  }, [currentProvider, reset, clearErrors]);

  // Simplified provider switching function with smoother transitions
  const switchProvider = useCallback((newProvider: ProviderType, currentValues: any = null) => {
    // Don't do anything if switching to the same provider
    if (newProvider === currentProvider) return;
    
    // Set switching flag to prevent validation during switch
    setIsSwitchingProvider(true);
    
    // Save current form values before switching
    const valuesToSave = currentValues || getValues();
    if (valuesToSave && Object.keys(valuesToSave).length > 0) {
      // Properly namespace the data before storing
      formStatesRef.current[currentProvider] = {
        ...valuesToSave,
        modelType: currentProvider,
        _provider: currentProvider,
      } as LlmFormValues;
    }
    
    // Switch provider immediately for responsive UI
    setCurrentProvider(newProvider);
    
    // Preemptively clear errors
    clearErrors();
  }, [currentProvider, getValues, clearErrors]);

  // Function to reset to a specific provider with data
  const resetToProvider = useCallback((providerType: ProviderType, data: LlmFormValues) => {
    // If already on the right provider, just update the data
    if (providerType === currentProvider) {
      clearErrors();
      reset({
        ...data,
        modelType: providerType,
        _provider: providerType
      }, {
        keepErrors: false,
        keepIsValid: false,
        keepTouched: false
      });
      
      // Also update the stored form state
      formStatesRef.current[providerType] = {
        ...data,
        modelType: providerType,
        _provider: providerType
      } as LlmFormValues;
      
      return;
    }
    
    // Set switching flag to prevent validation during reset
    setIsSwitchingProvider(true);
    
    // Store the data first
    formStatesRef.current[providerType] = {
      ...data,
      modelType: providerType,
      _provider: providerType
    } as LlmFormValues;
    
    // Then switch to the provider
    setCurrentProvider(providerType);
  }, [currentProvider, reset, clearErrors]);

  // Simplified effect to handle provider changes and reset the form
  useEffect(() => {
    // Only run when switching provider state is true
    if (!isSwitchingProvider) return () => {
      // No cleanup needed when not switching
    };
    
    // We use a single timeout for simplicity and reliability
    const timeoutId = setTimeout(() => {
      try {
        // Get values for the new provider
        let newValues;
        if (formStatesRef.current[currentProvider]) {
          // Use stored values if available
          newValues = formStatesRef.current[currentProvider];
        } else {
          // Use defaults if no stored values
          const newProviderConfig = getProviderById(currentProvider);
          newValues = newProviderConfig?.defaultValues 
            ? { ...newProviderConfig.defaultValues, _provider: currentProvider }
            : { modelType: currentProvider, apiKey: '', model: '', _provider: currentProvider };
        }
        
        reset(newValues, {
          keepErrors: false,
          keepIsValid: false,
          keepTouched: false
        });
      } catch (error) {
        console.error("Error updating form for new provider:", error);
      } finally {
        // Turn off switching flag
        setIsSwitchingProvider(false);
      }
    }, 50); // Slight delay for better rendering
    
    return () => clearTimeout(timeoutId);
  }, [currentProvider, reset, isSwitchingProvider]);

  // Getter for all form states (for debugging)
  const getAllFormStates = useCallback(() => ({ ...formStatesRef.current }), []);

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
    providers,
    getAllFormStates,
    formStates: formStatesRef.current,
    initialDataLoaded,
    errors
  };
};