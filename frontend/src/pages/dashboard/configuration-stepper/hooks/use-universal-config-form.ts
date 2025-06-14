import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useForm, UseFormReturn } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';

import { 
  getLlmProviders, 
  getLlmProviderById,
  getEmbeddingProviders,
  getEmbeddingProviderById,
  getStorageProviders,
  getStorageProviderById,
  getUrlProviders,
  getUrlProviderById,
  getSmtpProviders,
  getSmtpProviderById,
  type LlmFormValues,
  type EmbeddingFormValues,
  type StorageFormValues,
  type UrlFormValues,
  type SmtpFormValues,
  type BaseFormValues,
} from '../core/universal-model-factory';

type ConfigType = 'llm' | 'embedding' | 'storage' | 'url' | 'smtp';
type AnyFormValues = LlmFormValues | EmbeddingFormValues | StorageFormValues | UrlFormValues | SmtpFormValues;

type UniversalProviderFormReturn = UseFormReturn<any> & {
  providerConfig: any;
};

// Helper functions to get providers and provider getters
const getProvidersForType = (configType: ConfigType, accountType?: string) => {  
  const providers = (() => {
    switch (configType) {
      case 'llm':
        return getLlmProviders();
      case 'embedding':
        return getEmbeddingProviders();
      case 'storage':
        return getStorageProviders();
      case 'url':
        return getUrlProviders();
      case 'smtp':
        return getSmtpProviders();
      default:
        console.error(`Unknown config type: ${configType}`);
        return [];
    }
  })();
  return providers;
};

const getProviderByIdForType = (configType: ConfigType, id: string) => {  
  const provider = (() => {
    switch (configType) {
      case 'llm':
        return getLlmProviderById(id);
      case 'embedding':
        return getEmbeddingProviderById(id);
      case 'storage':
        return getStorageProviderById(id);
      case 'url':
        return getUrlProviderById(id);
      case 'smtp':
        return getSmtpProviderById(id);
      default:
        console.error(`Unknown config type: ${configType}`);
        return null;
    }
  })();
  return provider;
};

export const useUniversalProviderForm = (
  configType: ConfigType,
  providerType: string
): UniversalProviderFormReturn => {
  
  const providerConfig = useMemo(() => {
    const config = getProviderByIdForType(configType, providerType);
    return config;
  }, [configType, providerType]);

  const form = useForm({
    resolver: providerConfig ? zodResolver(providerConfig.schema) : undefined,
    mode: 'onChange',
    defaultValues: providerConfig
      ? {
          ...providerConfig.defaultValues,
          _provider: providerType,
        }
      : {
          providerType,
          _provider: providerType,
        },
  });

  return {
    ...form,
    providerConfig,
  };
};

export const useUniversalConfigForm = (configType: ConfigType, initialProvider: string, accountType?: string) => {
  const stateKey = useMemo(() => `${configType}-${Date.now()}-${Math.random()}`, [configType]);
  
  // Get providers based on config type - memoize to prevent re-initialization
  const providers = useMemo(() => {
    const providerList = getProvidersForType(configType, accountType);
    return providerList;
  }, [configType, accountType]);
  
  const [currentProvider, setCurrentProvider] = useState<string>(() => {
    let provider = '';
    
    // If we have an initial provider and it exists in the providers list, use it
    if (initialProvider && providers.find(p => p.id === initialProvider)) {
      provider = initialProvider;
    } else {
      // Otherwise, use the first available provider for this config type
      provider = providers[0]?.id || '';
    }
    
    return provider;
  });
  
  const configFormStateRef = useRef<Record<string, BaseFormValues>>({});
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  const [isSwitchingProvider, setIsSwitchingProvider] = useState(false);

  // Update currentProvider when providers change - CRITICAL FIX
  useEffect(() => {
    if (providers.length > 0) {
      // Check if current provider still exists in the new providers list
      const currentExists = providers.find(p => p.id === currentProvider);
      
      if (!currentExists) {
        // Current provider doesn't exist, switch to the first available provider
        const newProvider = providers[0]?.id;
        if (newProvider && newProvider !== currentProvider) {
          setCurrentProvider(newProvider);
        }
      }
    }
  }, [providers, currentProvider, configType, stateKey]);

  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid, isDirty, errors },
    getValues,
    watch,
    clearErrors,
    providerConfig,
  } = useUniversalProviderForm(configType, currentProvider);


  useEffect(() => {
    if (isSwitchingProvider) return () => {};

    const subscription = watch((data: any) => {
      if (data && Object.keys(data).length > 0) {
        const uniqueStateKey = `${configType}__${currentProvider}__${stateKey}`;
        configFormStateRef.current[uniqueStateKey] = {
          ...data,
          providerType: currentProvider,
          _provider: currentProvider,
        };
      }
    });

    return () => subscription.unsubscribe();
  }, [currentProvider, watch, isSwitchingProvider, configType, stateKey]);

  const resetForm = useCallback(
    (data: any) => {
      if (!data) return;

      const isDataForThisProvider =
        data.providerType === currentProvider || 
        data.modelType === currentProvider || 
        data._provider === currentProvider || 
        !data._provider;

      if (isDataForThisProvider) {
        const uniqueStateKey = `${configType}__${currentProvider}__${stateKey}`;
        configFormStateRef.current[uniqueStateKey] = {
          ...data,
          providerType: currentProvider,
          _provider: currentProvider,
        };

        clearErrors();
        reset(data, {
          keepDirty: false,
          keepValues: true,
          keepDefaultValues: true,
          keepErrors: false,
          keepIsValid: false,
          keepTouched: false,
        });
      } else {
        const uniqueStateKey = `${configType}__${currentProvider}__${stateKey}`;
        if (configFormStateRef.current[uniqueStateKey]) {
          reset(configFormStateRef.current[uniqueStateKey], {
            keepErrors: false,
            keepIsValid: false,
          });
        } else {
          const defaults = providerConfig?.defaultValues
            ? {
                ...providerConfig.defaultValues,
                _provider: currentProvider,
              }
            : {
                providerType: currentProvider,
                _provider: currentProvider,
              };

          reset(defaults, {
            keepErrors: false,
            keepIsValid: false,
          });
        }
      }
    },
    [reset, currentProvider, providerConfig, clearErrors, configType, stateKey]
  );

  const initializeForm = useCallback(
    (apiData: BaseFormValues | null) => {
      
      if (!apiData) return;

      // Handle both providerType and modelType for backward compatibility
      const providerType = apiData.providerType || (apiData as any).modelType;
      
      if (providerType) {
        const uniqueStateKey = `${configType}__${providerType}__${stateKey}`;
        configFormStateRef.current[uniqueStateKey] = {
          ...apiData,
          providerType,
          _provider: providerType,
        };
        
        if (providerType === currentProvider) {
          clearErrors();
          reset(
            {
              ...apiData,
              providerType,
              _provider: providerType,
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
    [currentProvider, reset, clearErrors, configType, stateKey]
  );

  const switchProvider = useCallback(
    (newProvider: string, currentValues: any = null) => {      
      if (newProvider === currentProvider) return;

      setIsSwitchingProvider(true);

      // Save current values to the specific config type + provider + instance state
      if (currentValues || Object.keys(getValues()).length > 0) {
        const valuesToSave = currentValues || getValues();
        const currentUniqueStateKey = `${configType}__${currentProvider}__${stateKey}`;
        configFormStateRef.current[currentUniqueStateKey] = {
          ...valuesToSave,
          providerType: currentProvider,
          _provider: currentProvider,
        };
      }

      requestAnimationFrame(() => {
        setCurrentProvider(newProvider);
        clearErrors();

        setTimeout(() => {
          try {
            // Check if we have saved state for this specific config type + provider + instance combination
            const newUniqueStateKey = `${configType}__${newProvider}__${stateKey}`;
          
            if (configFormStateRef.current[newUniqueStateKey]) {
              reset(configFormStateRef.current[newUniqueStateKey], {
                keepErrors: false,
                keepIsValid: false,
              });
            } else {
              const newProviderConfig = getProviderByIdForType(configType, newProvider);
              const defaults = newProviderConfig?.defaultValues
                ? { ...newProviderConfig.defaultValues, _provider: newProvider }
                : { providerType: newProvider, _provider: newProvider };

              reset(defaults, {
                keepErrors: false,
                keepIsValid: false,
              });
            }

            setTimeout(() => {
              setIsSwitchingProvider(false);
            }, 150);
          } catch (error) {
            console.error('Error during provider switch:', error);
            setIsSwitchingProvider(false);
          }
        }, 100);
      });
    },
    [currentProvider, getValues, reset, clearErrors, configType, stateKey]
  );

  const resetToProvider = useCallback(
    (providerType: string, data: BaseFormValues) => {      
      if (providerType === currentProvider) {
        clearErrors();
        reset(
          {
            ...data,
            providerType,
            _provider: providerType,
          },
          {
            keepErrors: false,
            keepIsValid: false,
            keepTouched: false,
          }
        );

        const uniqueStateKey = `${configType}__${providerType}__${stateKey}`;
        configFormStateRef.current[uniqueStateKey] = {
          ...data,
          providerType,
          _provider: providerType,
        };

        return;
      }

      setIsSwitchingProvider(true);

      const uniqueStateKey = `${configType}__${providerType}__${stateKey}`;
      configFormStateRef.current[uniqueStateKey] = {
        ...data,
        providerType,
        _provider: providerType,
      };

      requestAnimationFrame(() => {
        setCurrentProvider(providerType);

        setTimeout(() => {
          clearErrors();
          reset(
            {
              ...data,
              providerType,
              _provider: providerType,
            },
            {
              keepErrors: false,
              keepIsValid: false,
              keepTouched: false,
            }
          );

          setTimeout(() => {
            setIsSwitchingProvider(false);
          }, 150);
        }, 50);
      });
    },
    [currentProvider, reset, clearErrors, configType, stateKey]
  );

  const getAllFormStates = useCallback(() => ({ ...configFormStateRef.current }), []);

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
    providers, // This is now safely memoized
    getAllFormStates,
    formStates: configFormStateRef.current,
    initialDataLoaded,
    errors,
    getValues,
    watch,
  };
};