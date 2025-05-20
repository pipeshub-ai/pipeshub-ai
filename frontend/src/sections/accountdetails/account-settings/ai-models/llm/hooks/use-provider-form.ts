// hooks/use-provider-form.ts (Optimized for performance)

import { useState, useEffect, useCallback, useRef } from 'react';
import { useForm, UseFormReturn } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { ProviderType, LlmFormValues, ProviderConfig } from '../providers/types';
import { getProviderById, providers } from '../providers/constants';

// Pre-initialize all provider forms to avoid lag during switching
const createInitialForms = () => {
  const forms: Record<ProviderType, UseFormReturn<any>> = {} as any;
  
  // Create a form for each provider type
  providers.forEach(provider => {
    const providerConfig = getProviderById(provider.id);
    
    if (!providerConfig) {
      forms[provider.id] = useForm({
        mode: 'onTouched',
        defaultValues: {
          modelType: provider.id,
          apiKey: '',
          model: '',
          _provider: provider.id
        },
      });
    } else {
      forms[provider.id] = useForm({
        resolver: zodResolver(providerConfig.schema),
        mode: 'onTouched',
        defaultValues: {
          ...providerConfig.defaultValues,
          _provider: provider.id
        },
      });
    }
  });
  
  return forms;
};

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
  // Use ref to store form states with namespaced fields
  const formStatesRef = useRef<Record<ProviderType, LlmFormValues>>({} as any);
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
    if (isSwitchingProvider) return () => {};
    
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
    } else {
      // If data is for a different provider, don't use it
      console.warn(`Attempted to set data for ${data._provider || 'unknown'} provider to ${currentProvider} form`);
      
      // Instead, use previously stored data or defaults
      if (formStatesRef.current[currentProvider]) {
        reset(formStatesRef.current[currentProvider], {
          keepErrors: false,
          keepIsValid: false
        });
      } else {
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

  // Optimized provider switching function with smoother transitions
  const switchProvider = useCallback((newProvider: ProviderType, currentValues: any = null) => {
    // Don't do anything if switching to the same provider
    if (newProvider === currentProvider) return;
    
    // Set switching flag to prevent validation during switch
    setIsSwitchingProvider(true);
    
    try {
      // Save current form values before switching - but do this asynchronously
      // to avoid blocking the UI thread
      setTimeout(() => {
        try {
          const valuesToSave = currentValues || getValues();
          if (valuesToSave && Object.keys(valuesToSave).length > 0) {
            // Properly namespace the data before storing
            formStatesRef.current[currentProvider] = {
              ...valuesToSave,
              modelType: currentProvider,
              _provider: currentProvider,
            } as LlmFormValues;
          }
        } catch (error) {
          console.error("Error saving current values:", error);
        }
      }, 0);
      
      // Switch provider immediately for responsive UI
      setCurrentProvider(newProvider);
      
      // Preemptively clear errors
      clearErrors();
    } catch (error) {
      console.error("Error while switching provider:", error);
      setIsSwitchingProvider(false);
    }
  }, [currentProvider, getValues, clearErrors]);

  // New function to reset to a specific provider with data - optimized for performance
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
    
    // Turn off switching flag after a short delay
    setTimeout(() => {
      setIsSwitchingProvider(false);
    }, 50);
  }, [currentProvider, reset, clearErrors]);

  // Effect to handle provider changes and reset the form - optimized
  useEffect(() => {
    if (isSwitchingProvider) {
      // Clear any pending validation
      clearErrors();
      
      // Use requestAnimationFrame to ensure UI updates first
      requestAnimationFrame(() => {
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
          
          // Reset the form asynchronously for better performance
          setTimeout(() => {
            reset(newValues, {
              keepErrors: false,
              keepIsValid: false,
              keepTouched: false
            });
            
            // Turn off switching flag
            setIsSwitchingProvider(false);
          }, 0);
        } catch (error) {
          console.error("Error updating form for new provider:", error);
          setIsSwitchingProvider(false);
        }
      });
    }
  }, [currentProvider, reset, clearErrors, isSwitchingProvider]);

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