import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useForm, UseFormReturn } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { ConfigType, getProviderById, getProvidersForType } from '../core/config-registry';
import { GeneratedProvider } from '../core/config-factory';
import { BaseFormValues } from '../core/types';

export const useDynamicProviderForm = (
  configType: ConfigType,
  providerType: string
): UseFormReturn<any> & { providerConfig: GeneratedProvider | null } => {
  const providerConfig = useMemo(
    () => getProviderById(configType, providerType),
    [configType, providerType]
  );

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

export const useDynamicForm = (
  configType: ConfigType,
  initialProvider: string,
  accountType?: string
) => {
  const stateKey = useMemo(() => `${configType}-${Date.now()}-${Math.random()}`, [configType]);

  // Get providers based on config type - memoize to prevent re-initialization
  const providers = useMemo(() => getProvidersForType(configType), [configType]);

  const [currentProvider, setCurrentProvider] = useState<string>(() => {
    // If we have an initial provider and it exists in the providers list, use it
    if (initialProvider && providers.find((p) => p.id === initialProvider)) {
      return initialProvider;
    }
    // Otherwise, use the first available provider for this config type
    return providers[0]?.id || '';
  });

  const configFormStateRef = useRef<Record<string, BaseFormValues>>({});
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  const [isSwitchingProvider, setIsSwitchingProvider] = useState(false);

  // Update currentProvider when providers change
  useEffect(() => {
    if (providers.length > 0) {
      const currentExists = providers.find((p) => p.id === currentProvider);
      if (!currentExists) {
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
    formState: { isDirty, errors },
    getValues,
    watch,
    clearErrors,
    providerConfig,
  } = useDynamicProviderForm(configType, currentProvider);

  const calculateIsValid = useCallback(() => {
    if (!providerConfig?.allFields) return false;

    const formData = getValues();
    const requiredFields = providerConfig.allFields.filter((field: any) => field.required === true);
    
    // Check if we have any data
    const nonMetaKeys = Object.keys(formData).filter(
      (key) => key !== 'providerType' && key !== 'modelType' && key !== '_provider'
    );
    const hasData = nonMetaKeys.some((key) => {
      const value = formData[key];
      return value && value.toString().trim() !== '';
    });

    // Check if all required fields are filled
    const allRequiredFilled = requiredFields.every((field: any) => {
      const value = formData[field.name];
      return value && value.toString().trim() !== '';
    });

    return hasData && allRequiredFilled;
  }, [providerConfig, getValues]);

  const [isValid, setIsValid] = useState(false);

  useEffect(() => {
    if (isSwitchingProvider) return () => {};

    const subscription = watch(() => {
      const newIsValid = calculateIsValid();
      setIsValid(newIsValid);
    });

    // Initial calculation
    setIsValid(calculateIsValid());

    return () => subscription.unsubscribe();
  }, [watch, calculateIsValid, isSwitchingProvider]);

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
              const newProviderConfig = getProviderById(configType, newProvider);
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
    providers,
    getAllFormStates,
    formStates: configFormStateRef.current,
    initialDataLoaded,
    errors,
    getValues,
    watch,
  };
};