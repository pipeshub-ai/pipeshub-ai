import { useState, useEffect, useCallback, useRef } from 'react';
import { useForm, UseFormReturn } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';

import { 
  getLlmProviderById, 
  getLlmProviders,
  type LlmFormValues,
} from '../models/llm/providers';

import { 
  getEmbeddingProviders,
  getEmbeddingProviderById,
  type EmbeddingFormValues,
} from '../models/embedding/providers';

type AnyFormValues = LlmFormValues | EmbeddingFormValues;

type UniversalProviderFormReturn = UseFormReturn<any> & {
  providerConfig: any;
};

export const useUniversalProviderForm = (
  modelType: 'llm' | 'embedding',
  providerType: string
): UniversalProviderFormReturn => {
  const getProviderById = modelType === 'llm' ? getLlmProviderById : getEmbeddingProviderById;
  const providerConfig = getProviderById(providerType);

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

  return {
    ...form,
    providerConfig,
  };
};

export const useUniversalForm = (modelType: 'llm' | 'embedding', initialProvider: string) => {
  const [currentProvider, setCurrentProvider] = useState<string>(initialProvider);
  const providers = modelType === 'llm' ? getLlmProviders() : getEmbeddingProviders();
  
  const providersFormStateRef = useRef<Record<string, AnyFormValues>>({} as any);
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  const [isSwitchingProvider, setIsSwitchingProvider] = useState(false);

  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid, isDirty, errors },
    getValues,
    watch,
    clearErrors,
    providerConfig,
  } = useUniversalProviderForm(modelType, currentProvider);

  useEffect(() => {
    if (isSwitchingProvider) return () => {};

    const subscription = watch((data) => {
      if (data && Object.keys(data).length > 0) {
        providersFormStateRef.current[currentProvider] = {
          ...data,
          modelType: currentProvider,
          _provider: currentProvider,
        } as AnyFormValues;
      }
    });

    return () => subscription.unsubscribe();
  }, [currentProvider, watch, isSwitchingProvider]);

  const resetForm = useCallback(
    (data: any) => {
      if (!data) return;

      const isDataForThisProvider =
        data.modelType === currentProvider || data._provider === currentProvider || !data._provider;

      if (isDataForThisProvider) {
        providersFormStateRef.current[currentProvider] = {
          ...data,
          modelType: currentProvider,
          _provider: currentProvider,
        } as AnyFormValues;

        clearErrors();
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

  const initializeForm = useCallback(
    (apiData: AnyFormValues | null) => {
      if (!apiData) return;

      if (apiData.modelType) {
        const provider = apiData.modelType;

        providersFormStateRef.current[provider] = {
          ...apiData,
          _provider: provider,
        } as AnyFormValues;

        if (provider === currentProvider) {
          clearErrors();
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

  const switchProvider = useCallback(
    (newProvider: string, currentValues: any = null) => {
      if (newProvider === currentProvider) return;

      setIsSwitchingProvider(true);

      if (currentValues || Object.keys(getValues()).length > 0) {
        const valuesToSave = currentValues || getValues();
        providersFormStateRef.current[currentProvider] = {
          ...valuesToSave,
          modelType: currentProvider,
          _provider: currentProvider,
        } as AnyFormValues;
      }

      requestAnimationFrame(() => {
        setCurrentProvider(newProvider);
        clearErrors();

        setTimeout(() => {
          try {
            if (providersFormStateRef.current[newProvider]) {
              reset(providersFormStateRef.current[newProvider], {
                keepErrors: false,
                keepIsValid: false,
              });
            } else {
              const getProviderById = modelType === 'llm' ? getLlmProviderById : getEmbeddingProviderById;
              const newProviderConfig = getProviderById(newProvider);
              const defaults = newProviderConfig?.defaultValues
                ? { ...newProviderConfig.defaultValues, _provider: newProvider }
                : { modelType: newProvider, _provider: newProvider };

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
    [currentProvider, getValues, reset, clearErrors, modelType]
  );

  const resetToProvider = useCallback(
    (providerType: string, data: AnyFormValues) => {
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

        providersFormStateRef.current[providerType] = {
          ...data,
          modelType: providerType,
          _provider: providerType,
        } as AnyFormValues;

        return;
      }

      setIsSwitchingProvider(true);

      providersFormStateRef.current[providerType] = {
        ...data,
        modelType: providerType,
        _provider: providerType,
      } as AnyFormValues;

      requestAnimationFrame(() => {
        setCurrentProvider(providerType);

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

          setTimeout(() => {
            setIsSwitchingProvider(false);
          }, 150);
        }, 50);
      });
    },
    [currentProvider, reset, clearErrors]
  );

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
    providers,
    getAllFormStates,
    formStates: providersFormStateRef.current,
    initialDataLoaded,
    errors,
  };
};