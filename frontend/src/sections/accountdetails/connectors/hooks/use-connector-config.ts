/**
 * useConnectorConfig Hook
 *
 * Hook for managing connector instance configuration.
 * Handles form state, validation, and saving configuration.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAccountType } from 'src/hooks/use-account-type';
import { Connector, ConnectorConfig } from '../types/types';
import { ConnectorApiService } from '../services/api';
import { CrawlingManagerApi } from '../services/crawling-manager';
import { buildCronFromSchedule } from '../utils/cron';
import { evaluateConditionalDisplay } from '../utils/conditional-display';

interface FormData {
  auth: Record<string, any>;
  sync: Record<string, any>;
  filters: Record<string, any>;
}

interface FormErrors {
  auth: Record<string, string>;
  sync: Record<string, string>;
  filters: Record<string, string>;
}

interface UseConnectorConfigProps {
  connector: Connector | any; // supports both Connector and ConnectorRegistry shape
  onClose: () => void;
  onSuccess?: () => void;
  initialInstanceName?: string;
}

interface UseConnectorConfigReturn {
  // State
  connectorConfig: ConnectorConfig | null;
  loading: boolean;
  saving: boolean;
  activeStep: number;
  formData: FormData;
  formErrors: FormErrors;
  saveError: string | null;
  conditionalDisplay: Record<string, boolean>;

  // Business OAuth state
  adminEmail: string;
  adminEmailError: string | null;
  selectedFile: File | null;
  fileName: string | null;
  fileError: string | null;
  jsonData: Record<string, any> | null;

  // Create mode state
  isCreateMode: boolean;
  instanceName: string;
  instanceNameError: string | null;

  // Actions
  handleFieldChange: (section: string, fieldName: string, value: any) => void;
  handleNext: () => void;
  handleBack: () => void;
  handleSave: () => Promise<void>;
  handleFileSelect: (file: File | null) => Promise<void>;
  handleFileUpload: () => void;
  handleFileChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  handleAdminEmailChange: (email: string) => void;
  validateAdminEmail: (email: string) => boolean;
  isBusinessGoogleOAuthValid: () => boolean;
  fileInputRef: React.RefObject<HTMLInputElement>;
  setInstanceName: (name: string) => void;
}

export const useConnectorConfig = ({
  connector,
  onClose,
  onSuccess,
  initialInstanceName,
}: UseConnectorConfigProps): UseConnectorConfigReturn => {
  const { isBusiness, isIndividual, loading: accountTypeLoading } = useAccountType();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  // State
  const [connectorConfig, setConnectorConfig] = useState<ConnectorConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [formData, setFormData] = useState<FormData>({
    auth: {},
    sync: {},
    filters: {},
  });
  const [formErrors, setFormErrors] = useState<FormErrors>({
    auth: {},
    sync: {},
    filters: {},
  });
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conditionalDisplay, setConditionalDisplay] = useState<Record<string, boolean>>({});

  // Create mode
  const isCreateMode = !connector?._key;
  const [instanceName, setInstanceName] = useState<string>(initialInstanceName || '');
  const [instanceNameError, setInstanceNameError] = useState<string | null>(null);

  // Business OAuth specific state
  const [adminEmail, setAdminEmail] = useState('');
  const [adminEmailError, setAdminEmailError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [jsonData, setJsonData] = useState<Record<string, any> | null>(null);

  // Helper to check if custom business OAuth logic applies
  const customGoogleBusinessOAuth = useCallback(
    (connectorParam: Connector, accountType: string): boolean =>
      accountType === 'business' &&
      connectorParam.appGroup === 'Google Workspace' &&
      connectorParam.authType === 'OAUTH',
    []
  );

  // Business OAuth validation
  const validateAdminEmail = useCallback((email: string): boolean => {
    if (!email.trim()) {
      setAdminEmailError('Admin email is required for business OAuth');
      return false;
    }

    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailPattern.test(email)) {
      setAdminEmailError('Please enter a valid email address');
      return false;
    }

    setAdminEmailError(null);
    return true;
  }, []);

  const validateBusinessGoogleOAuth = useCallback(
    (adminEmailParam: string, jsonDataParam: any): boolean =>
      validateAdminEmail(adminEmailParam) &&
      !!jsonDataParam &&
      jsonDataParam.type === 'service_account',
    [validateAdminEmail]
  );

  const getBusinessOAuthData = useCallback((config: any) => {
    const authValues = config?.config?.auth?.values || config?.config?.auth || {};
    return {
      adminEmail: authValues.adminEmail || '',
      jsonData:
        authValues.client_id && authValues.project_id && authValues.type === 'service_account'
          ? authValues
          : null,
      fileName:
        authValues.client_id && authValues.project_id && authValues.type === 'service_account'
          ? 'existing-credentials.json'
          : null,
    };
  }, []);

  // Config merger
  const mergeConfigWithSchema = useCallback(
    (configResponse: any, schemaResponse: any) => ({
      ...configResponse,
      config: {
        documentationLinks: schemaResponse.documentationLinks || [],
        auth: {
          ...schemaResponse.auth,
          values: configResponse.config.auth?.values || configResponse.config.auth || {},
          customValues: configResponse.config.auth?.customValues || {},
        },
        sync: {
          ...schemaResponse.sync,
          selectedStrategy:
            configResponse.config.sync?.selectedStrategy ||
            schemaResponse.sync.supportedStrategies?.[0] ||
            'MANUAL',
          scheduledConfig: configResponse.config.sync?.scheduledConfig || {},
          values: configResponse.config.sync?.values || configResponse.config.sync || {},
          customValues: configResponse.config.sync?.customValues || {},
        },
        filters: {
          ...schemaResponse.filters,
          values: configResponse.config.filters?.values || configResponse.config.filters || {},
          customValues: configResponse.config.filters?.customValues || {},
        },
      },
    }),
    []
  );

  const validateJsonFile = useCallback((file: File): boolean => {
    if (!file) {
      setFileError('JSON file is required for business OAuth');
      return false;
    }

    if (!file.name.endsWith('.json') && file.type !== 'application/json') {
      setFileError('Only JSON files are allowed');
      return false;
    }

    setFileError(null);
    return true;
  }, []);

  const parseJsonFile = useCallback(async (file: File): Promise<Record<string, any> | null> => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);

      const requiredFields = ['client_id', 'project_id', 'type'];
      const missingFields = requiredFields.filter((field) => !parsed[field]);

      if (missingFields.length > 0) {
        setFileError(`Missing required fields: ${missingFields.join(', ')}`);
        return null;
      }

      if (parsed.type !== 'service_account') {
        setFileError('This is not a Google Cloud Service Account JSON file');
        return null;
      }

      return parsed;
    } catch (error) {
      setFileError('Invalid JSON file format');
      return null;
    }
  }, []);

  const handleFileSelect = useCallback(
    async (file: File | null) => {
      setSelectedFile(file);
      setFileName(file?.name || null);
      setFileError(null);

      if (file && validateJsonFile(file)) {
        const parsed = await parseJsonFile(file);
        if (parsed) {
          setJsonData(parsed);
        }
      } else {
        setJsonData(null);
      }
    },
    [validateJsonFile, parseJsonFile]
  );

  const handleFileUpload = useCallback(() => {
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  }, []);

  const handleFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] || null;
      handleFileSelect(file);
    },
    [handleFileSelect]
  );

  const isBusinessGoogleOAuthValid = useCallback(
    () => validateBusinessGoogleOAuth(adminEmail, jsonData),
    [adminEmail, jsonData, validateBusinessGoogleOAuth]
  );

  // Load connector configuration (create mode: load schema only; edit mode: load instance config + schema)
  useEffect(() => {
    const fetchConnectorConfig = async () => {
      try {
        setLoading(true);
        let mergedConfig: any = null;

        if (isCreateMode) {
          // Create mode: only schema exists; synthesize empty config shape
          const schemaResponse = await ConnectorApiService.getConnectorSchema(connector.type);
          const emptyConfigResponse = {
            name: connector.name,
            type: connector.type,
            appGroup: connector.appGroup,
            appGroupId: connector.appGroupId || '',
            authType: connector.authType,
            isActive: false,
            isConfigured: false,
            supportsRealtime: !!connector.supportsRealtime,
            appDescription: connector.appDescription || '',
            appCategories: connector.appCategories || [],
            iconPath: connector.iconPath,
            config: {
              auth: {},
              sync: {},
              filters: {},
            },
          };
          mergedConfig = mergeConfigWithSchema(emptyConfigResponse, schemaResponse);
        } else {
          // Edit mode: fetch both config and schema using connectorId
          const [configResponse, schemaResponse] = await Promise.all([
            ConnectorApiService.getConnectorInstanceConfig(connector._key),
            ConnectorApiService.getConnectorSchema(connector.type),
          ]);
          mergedConfig = mergeConfigWithSchema(configResponse, schemaResponse);
        }
        setConnectorConfig(mergedConfig);

        // Initialize form data
        const initializeFormData = (config: any) => {
          const authData = { ...(config.config.auth?.values || config.config.auth || {}) };

          // Set default values from field definitions
          if (config.config.auth?.schema?.fields) {
            config.config.auth.schema.fields.forEach((field: any) => {
              if (field.defaultValue !== undefined && authData[field.name] === undefined) {
                authData[field.name] = field.defaultValue;
              }
            });
          }

          return {
            auth: authData,
            sync: {
              selectedStrategy:
                config.config.sync?.selectedStrategy ||
                config.config.sync?.supportedStrategies?.[0] ||
                'MANUAL',
              scheduledConfig: config.config.sync?.scheduledConfig || {},
              ...(config.config.sync?.values || config.config.sync || {}),
            },
            filters: config.config.filters?.values || config.config.filters || {},
          };
        };

        const initialFormData = initializeFormData(mergedConfig);

        // For business OAuth, load admin email and JSON data from existing config
        if (customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual')) {
          const businessData = getBusinessOAuthData(mergedConfig);
          setAdminEmail(businessData.adminEmail);
          setJsonData(businessData.jsonData);
          setFileName(businessData.fileName);
        }

        setFormData(initialFormData);

        // Evaluate conditional display rules
        if (mergedConfig.config.auth.conditionalDisplay) {
          const displayRules = evaluateConditionalDisplay(
            mergedConfig.config.auth.conditionalDisplay,
            initialFormData.auth
          );
          setConditionalDisplay(displayRules);
        } else {
          setConditionalDisplay({});
        }
      } catch (error) {
        console.error('Error fetching connector config:', error);
        setSaveError('Failed to load connector configuration');
      } finally {
        setLoading(false);
      }
    };

    fetchConnectorConfig();
  }, [
    connector,
    isBusiness,
    mergeConfigWithSchema,
    customGoogleBusinessOAuth,
    getBusinessOAuthData,
    isCreateMode,
  ]);
  // Recalculate conditional display when form data changes
  useEffect(() => {
    if (connectorConfig?.config?.auth?.conditionalDisplay) {
      const displayRules = evaluateConditionalDisplay(
        connectorConfig.config.auth.conditionalDisplay,
        formData.auth
      );
      setConditionalDisplay(displayRules);
    }
  }, [formData.auth, connectorConfig?.config?.auth?.conditionalDisplay]);
  const validateField = useCallback((field: any, value: any): string => {
    if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
      return `${field.displayName} is required`;
    }
    if (field.validation) {
      const { minLength, maxLength, pattern, format } = field.validation;

      if (minLength && value && value.length < minLength) {
        return `${field.displayName} must be at least ${minLength} characters`;
      }

      if (maxLength && value && value.length > maxLength) {
        return `${field.displayName} must be no more than ${maxLength} characters`;
      }

      if (format && value) {
        switch (format) {
          case 'email': {
            const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            if (!emailRegex.test(value)) {
              return `${field.displayName} must be a valid email address`;
            }
            break;
          }
          case 'url': {
            const isValidUrl = (() => {
              try {
                // Constructing URL returns an object; we only care if it throws
                // Using a variable avoids 'new' for side effects lint rule
                const parsed = new URL(value);
                return Boolean(parsed);
              } catch {
                return false;
              }
            })();
            if (!isValidUrl) {
              return `${field.displayName} must be a valid URL`;
            }
            break;
          }
          default:
            break;
        }
      }
    }

    return '';
  }, []);
  const validateSection = useCallback(
    (section: string, fields: any[], values: Record<string, any>): Record<string, string> => {
      const errors: Record<string, string> = {};
      fields.forEach((field) => {
        const error = validateField(field, values[field.name]);
        if (error) {
          errors[field.name] = error;
        }
      });

      return errors;
    },
    [validateField]
  );
  const handleFieldChange = useCallback(
    (section: string, fieldName: string, value: any) => {
      setFormData((prev) => {
        const newFormData = {
          ...prev,
          [section]: {
            ...prev[section as keyof FormData],
            [fieldName]: value,
          },
        };
        // Re-evaluate conditional display rules for auth section
        if (section === 'auth' && connectorConfig?.config.auth.conditionalDisplay) {
          const displayRules = evaluateConditionalDisplay(
            connectorConfig.config.auth.conditionalDisplay,
            newFormData.auth
          );
          setConditionalDisplay(displayRules);
        }

        return newFormData;
      });

      // Clear error for this field
      setFormErrors((prev) => ({
        ...prev,
        [section]: {
          ...prev[section as keyof FormErrors],
          [fieldName]: '',
        },
      }));
    },
    [connectorConfig]
  );
  const handleNext = useCallback(() => {
    if (!connectorConfig) return;
    let errors: Record<string, string> = {};

    // Validate current step
    switch (activeStep) {
      case 0: // Auth
        {
          const isBusinessMode = customGoogleBusinessOAuth(
            connector,
            isBusiness ? 'business' : 'individual'
          );

          if (isBusinessMode) {
            if (!isBusinessGoogleOAuthValid()) {
              errors = { adminEmail: adminEmailError || 'Invalid business credentials' };
            }
          } else {
            errors = validateSection(
              'auth',
              connectorConfig.config.auth.schema.fields,
              formData.auth
            );
          }
        }
        break;
      case 1: // Sync
        errors = validateSection('sync', connectorConfig.config.sync.customFields, formData.sync);
        break;
      default:
        break;
    }

    setFormErrors((prev) => ({
      ...prev,
      [activeStep === 0 ? 'auth' : 'sync']: errors,
    }));

    if (Object.keys(errors).length === 0 && activeStep < 1) {
      setActiveStep((prev) => prev + 1);
    }
  }, [
    activeStep,
    connectorConfig,
    formData,
    validateSection,
    customGoogleBusinessOAuth,
    connector,
    isBusiness,
    isBusinessGoogleOAuthValid,
    adminEmailError,
  ]);
  const handleBack = useCallback(() => {
    if (activeStep > 0) {
      setActiveStep((prev) => prev - 1);
    }
  }, [activeStep]);
  const handleSave = useCallback(async () => {
    if (!connectorConfig) return;
    try {
      setSaving(true);
      setSaveError(null);

      // In create mode, validate instance name
      if (isCreateMode) {
        const trimmed = (instanceName || '').trim();
        if (!trimmed) {
          setInstanceNameError('Instance name is required');
          return;
        }
        if (trimmed.length < 3) {
          setInstanceNameError('Instance name must be at least 3 characters');
          return;
        }
        setInstanceNameError(null);
      }

      // For business OAuth, validate admin email and JSON file
      if (customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual')) {
        if (!isBusinessGoogleOAuthValid()) {
          setSaveError('Please provide valid admin email and JSON credentials file');
          return;
        }
      }

      // Validate all sections
      let authErrors: Record<string, string> = {};
      const isBusinessMode = customGoogleBusinessOAuth(
        connector,
        isBusiness ? 'business' : 'individual'
      );

      if (isBusinessMode) {
        if (!isBusinessGoogleOAuthValid()) {
          authErrors = { adminEmail: adminEmailError || 'Invalid business credentials' };
        }
      } else {
        authErrors = validateSection(
          'auth',
          connectorConfig.config.auth.schema.fields,
          formData.auth
        );
      }
      const syncErrors = validateSection(
        'sync',
        connectorConfig.config.sync.customFields,
        formData.sync
      );

      const allErrors = { auth: authErrors, sync: syncErrors, filters: {} };
      setFormErrors(allErrors);

      if (Object.values(allErrors).some((section) => Object.keys(section).length > 0)) {
        return;
      }

      // Prepare config for API
      const configToSave: any = {
        auth: formData.auth,
        sync: {
          selectedStrategy: formData.sync.selectedStrategy,
          scheduledConfig: formData.sync.scheduledConfig || {},
          ...formData.sync,
        },
        filters: formData.filters || {},
      };

      // For business OAuth, merge JSON data and admin email
      if (
        customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') &&
        jsonData
      ) {
        configToSave.auth = {
          ...configToSave.auth,
          ...jsonData,
          adminEmail,
        };
      }

      if (isCreateMode) {
        // Create new instance with config and instance name
        const created = await ConnectorApiService.createConnectorInstance(
          connector.type,
          instanceName.trim(),
          configToSave
        );
        onSuccess?.();
        onClose();
        // After creation, if scheduled, schedule crawling with connectorId
        const syncStrategy = String(formData.sync.selectedStrategy || '').toUpperCase();
        if (syncStrategy === 'SCHEDULED') {
          const scheduled = (formData.sync.scheduledConfig || {}) as any;
          const cron = buildCronFromSchedule({
            startTime: scheduled.startTime,
            intervalMinutes: scheduled.intervalMinutes,
            timezone: scheduled.timezone?.toUpperCase() || 'UTC',
          });
          await CrawlingManagerApi.schedule(connector.type.toLowerCase(), created.connectorId, {
            scheduleConfig: {
              scheduleType: 'custom',
              isEnabled: true,
              timezone: scheduled.timezone?.toUpperCase() || 'UTC',
              cronExpression: cron,
            },
            priority: 5,
            maxRetries: 3,
            timeout: 300000,
          });
        }
        if (isBusiness) {
          navigate(`/account/company-settings/settings/connector/${created.connectorId}`);
        } else {
          navigate(`/account/individual/settings/connector/${created.connectorId}`);
        }
        return;
      }
      if (connector._key) {
        // Update existing connector configuration using connectorId
        await ConnectorApiService.updateConnectorInstanceConfig(connector._key, configToSave);
      }

      // After saving config, schedule crawling if strategy is SCHEDULED
      const syncStrategy = String(formData.sync.selectedStrategy || '').toUpperCase();
      if (syncStrategy === 'SCHEDULED') {
        const scheduled = (formData.sync.scheduledConfig || {}) as any;
        const cron = buildCronFromSchedule({
          startTime: scheduled.startTime,
          intervalMinutes: scheduled.intervalMinutes,
          timezone: scheduled.timezone?.toUpperCase() || 'UTC',
        });
        await CrawlingManagerApi.schedule(connector.type.toLowerCase(), connector._key, {
          scheduleConfig: {
            scheduleType: 'custom',
            isEnabled: true,
            timezone: scheduled.timezone?.toUpperCase() || 'UTC',
            cronExpression: cron,
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        });
      }

      onSuccess?.();
      onClose();
    } catch (error) {
      console.error('Error saving connector config:', error);
      setSaveError('Failed to save configuration. Please try again.');
    } finally {
      setSaving(false);
    }
  }, [
    connectorConfig,
    connector,
    formData,
    validateSection,
    onClose,
    onSuccess,
    isBusiness,
    adminEmail,
    jsonData,
    isBusinessGoogleOAuthValid,
    customGoogleBusinessOAuth,
    adminEmailError,
    isCreateMode,
    instanceName,
    navigate
  ]);
  // Admin email change handler
  const handleAdminEmailChange = useCallback(
    (email: string) => {
      setAdminEmail(email);
      validateAdminEmail(email);
    },
    [validateAdminEmail]
  );
  return {
    // State
    connectorConfig,
    loading,
    saving,
    activeStep,
    formData,
    formErrors,
    saveError,
    conditionalDisplay,
    // Create mode
    isCreateMode,
    instanceName,
    instanceNameError,
    // Business OAuth state
    adminEmail,
    adminEmailError,
    selectedFile,
    fileName,
    fileError,
    jsonData,

    // Actions
    handleFieldChange,
    handleNext,
    handleBack,
    handleSave,
    handleFileSelect,
    handleFileUpload,
    handleFileChange,
    handleAdminEmailChange,
    validateAdminEmail,
    isBusinessGoogleOAuthValid,
    fileInputRef,
    setInstanceName,
  };
};
