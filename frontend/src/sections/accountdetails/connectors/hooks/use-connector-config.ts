/**
 * useConnectorConfig Hook
 *
 * Custom hook for managing connector instance configuration.
 * Handles form state, validation, OAuth flows, and saving configuration.
 *
 * Features:
 * - Multi-step form management
 * - Field validation with conditional display
 * - Business OAuth support for Google Workspace
 * - Create and edit modes
 * - Optimized re-renders with useCallback
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAccountType } from 'src/hooks/use-account-type';
import { Connector, ConnectorConfig } from '../types/types';
import { ConnectorApiService } from '../services/api';
import { CrawlingManagerApi } from '../services/crawling-manager';
import { buildCronFromSchedule } from '../utils/cron';
import { evaluateConditionalDisplay } from '../utils/conditional-display';
import { isNoneAuthType } from '../utils/auth';

// Types
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

// Constants
const MIN_INSTANCE_NAME_LENGTH = 3;
const REQUIRED_SERVICE_ACCOUNT_FIELDS = ['client_id', 'project_id', 'type'];
const SERVICE_ACCOUNT_TYPE = 'service_account';

/**
 * Main hook for connector configuration
 */
export const useConnectorConfig = ({
  connector,
  onClose,
  onSuccess,
  initialInstanceName = '',
}: UseConnectorConfigProps): UseConnectorConfigReturn => {
  // Hooks
  const { isBusiness } = useAccountType();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Determine if we're in create mode (no _key means new instance)
  const isCreateMode = !connector?._key;

  // State - Loading
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // State - Form
  const [connectorConfig, setConnectorConfig] = useState<ConnectorConfig | null>(null);
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

  // State - Create mode
  const [instanceName, setInstanceName] = useState(initialInstanceName);
  const [instanceNameError, setInstanceNameError] = useState<string | null>(null);

  // State - Business OAuth
  const [adminEmail, setAdminEmail] = useState('');
  const [adminEmailError, setAdminEmailError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [jsonData, setJsonData] = useState<Record<string, any> | null>(null);

  /**
   * Check if business OAuth mode applies
   */
  const isBusinessGoogleOAuth = useMemo(
    () =>
      isBusiness && connector?.appGroup === 'Google Workspace' && connector?.authType === 'OAUTH',
    [isBusiness, connector?.appGroup, connector?.authType]
  );

  /**
   * Validate email format
   */
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

  /**
   * Validate JSON file format and required fields
   */
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

  /**
   * Parse and validate JSON file content
   */
  const parseJsonFile = useCallback(async (file: File): Promise<Record<string, any> | null> => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);

      // Check for required fields
      const missingFields = REQUIRED_SERVICE_ACCOUNT_FIELDS.filter((field) => !parsed[field]);

      if (missingFields.length > 0) {
        setFileError(`Missing required fields: ${missingFields.join(', ')}`);
        return null;
      }

      if (parsed.type !== SERVICE_ACCOUNT_TYPE) {
        setFileError('This is not a Google Cloud Service Account JSON file');
        return null;
      }

      return parsed;
    } catch (error) {
      setFileError('Invalid JSON file format');
      return null;
    }
  }, []);

  /**
   * Handle file selection
   */
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

  /**
   * Trigger file input click
   */
  const handleFileUpload = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  /**
   * Handle file input change
   */
  const handleFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] || null;
      handleFileSelect(file);
    },
    [handleFileSelect]
  );

  /**
   * Handle admin email change
   */
  const handleAdminEmailChange = useCallback(
    (email: string) => {
      setAdminEmail(email);
      validateAdminEmail(email);
    },
    [validateAdminEmail]
  );

  /**
   * Check if business OAuth is valid
   */
  const isBusinessGoogleOAuthValid = useCallback(
    () => validateAdminEmail(adminEmail) && !!jsonData && jsonData.type === SERVICE_ACCOUNT_TYPE,
    [adminEmail, jsonData, validateAdminEmail]
  );

  /**
   * Extract business OAuth data from config
   */
  const extractBusinessOAuthData = useCallback((config: any) => {
    const authValues = config?.config?.auth?.values || config?.config?.auth || {};

    return {
      adminEmail: authValues.adminEmail || '',
      jsonData:
        authValues.client_id && authValues.project_id && authValues.type === SERVICE_ACCOUNT_TYPE
          ? authValues
          : null,
      fileName:
        authValues.client_id && authValues.project_id && authValues.type === SERVICE_ACCOUNT_TYPE
          ? 'existing-credentials.json'
          : null,
    };
  }, []);

  /**
   * Merge config with schema
   */
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

  /**
   * Initialize form data from config
   */
  const initializeFormData = useCallback((config: any): FormData => {
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
  }, []);

  /**
   * Load connector configuration
   */
  useEffect(() => {
    const fetchConnectorConfig = async () => {
      try {
        setLoading(true);
        let mergedConfig: any = null;

        if (isCreateMode) {
          // Create mode: load schema only
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
          // Edit mode: load both config and schema
          const [configResponse, schemaResponse] = await Promise.all([
            ConnectorApiService.getConnectorInstanceConfig(connector._key),
            ConnectorApiService.getConnectorSchema(connector.type),
          ]);

          mergedConfig = mergeConfigWithSchema(configResponse, schemaResponse);
        }

        setConnectorConfig(mergedConfig);

        // Initialize form data
        const initialFormData = initializeFormData(mergedConfig);
        setFormData(initialFormData);

        // For business OAuth, load existing credentials
        if (isBusinessGoogleOAuth) {
          const businessData = extractBusinessOAuthData(mergedConfig);
          setAdminEmail(businessData.adminEmail);
          setJsonData(businessData.jsonData);
          setFileName(businessData.fileName);
        }

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
    isCreateMode,
    isBusinessGoogleOAuth,
    mergeConfigWithSchema,
    initializeFormData,
    extractBusinessOAuthData,
  ]);

  /**
   * Recalculate conditional display when auth form data changes
   */
  useEffect(() => {
    if (connectorConfig?.config?.auth?.conditionalDisplay) {
      const displayRules = evaluateConditionalDisplay(
        connectorConfig.config.auth.conditionalDisplay,
        formData.auth
      );
      setConditionalDisplay(displayRules);
    }
  }, [formData.auth, connectorConfig?.config?.auth?.conditionalDisplay]);

  /**
   * Validate a single field
   */
  const validateField = useCallback((field: any, value: any): string => {
    if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
      return `${field.displayName} is required`;
    }

    if (field.validation) {
      const { minLength, maxLength, format } = field.validation;

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
            const canParse =
              typeof URL !== 'undefined' && (URL as any).canParse
                ? (URL as any).canParse(value)
                : (() => {
                    // Fallback regex when URL.canParse is unavailable
                    const urlRegex = /^(https?:\/\/)[^\s/$.?#].[^\s]*$/i;
                    return urlRegex.test(value);
                  })();
            if (!canParse) {
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

  /**
   * Validate a section of the form
   */
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

  /**
   * Handle field value change
   */
  const handleFieldChange = useCallback((section: string, fieldName: string, value: any) => {
    setFormData((prev) => ({
      ...prev,
      [section]: {
        ...prev[section as keyof FormData],
        [fieldName]: value,
      },
    }));

    // Clear error for this field
    setFormErrors((prev) => ({
      ...prev,
      [section]: {
        ...prev[section as keyof FormErrors],
        [fieldName]: '',
      },
    }));
  }, []);

  /**
   * Handle next step
   */
  const handleNext = useCallback(() => {
    if (!connectorConfig) return;

    let errors: Record<string, string> = {};

    // Validate current step
    switch (activeStep) {
      case 0: // Auth step
        if (isBusinessGoogleOAuth) {
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
        break;
      case 1: // Sync step
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
    isBusinessGoogleOAuth,
    isBusinessGoogleOAuthValid,
    adminEmailError,
  ]);

  /**
   * Handle back step
   */
  const handleBack = useCallback(() => {
    if (activeStep > 0) {
      setActiveStep((prev) => prev - 1);
    }
  }, [activeStep]);

  /**
   * Schedule crawling job
   */
  const scheduleCrawlingJob = useCallback(
    async (connectorType: string, connectorId: string, scheduledConfig: any) => {
      const cron = buildCronFromSchedule({
        startTime: scheduledConfig.startTime,
        intervalMinutes: scheduledConfig.intervalMinutes,
        timezone: scheduledConfig.timezone?.toUpperCase() || 'UTC',
      });

      await CrawlingManagerApi.schedule(connectorType.toLowerCase(), connectorId, {
        scheduleConfig: {
          scheduleType: 'custom',
          isEnabled: true,
          timezone: scheduledConfig.timezone?.toUpperCase() || 'UTC',
          cronExpression: cron,
        },
        priority: 5,
        maxRetries: 3,
        timeout: 300000,
      });
    },
    []
  );

  /**
   * Remove crawling job
   */
  const removeCrawlingJob = useCallback(async (connectorType: string, connectorId: string) => {
    try {
      await CrawlingManagerApi.remove(connectorType.toLowerCase(), connectorId);
    } catch (error) {
      console.error('Error removing scheduled jobs:', error);
      // Don't throw - removal failure shouldn't block save
    }
  }, []);

  /**
   * Handle save configuration
   */
  const handleSave = useCallback(async () => {
    if (!connectorConfig) return;

    try {
      setSaving(true);
      setSaveError(null);

      // Validate instance name in create mode
      if (isCreateMode) {
        const trimmedName = instanceName.trim();
        if (!trimmedName) {
          setInstanceNameError('Instance name is required');
          return;
        }
        if (trimmedName.length < MIN_INSTANCE_NAME_LENGTH) {
          setInstanceNameError(
            `Instance name must be at least ${MIN_INSTANCE_NAME_LENGTH} characters`
          );
          return;
        }
        setInstanceNameError(null);
      }

      const skipAuthValidation = isNoneAuthType(connector.authType);

      // Validate sections
      let authErrors: Record<string, string> = {};
      if (!skipAuthValidation) {
        if (isBusinessGoogleOAuth) {
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

      // Prepare config for API - store values directly in etcd
      const normalizedStrategy = String(formData.sync.selectedStrategy || '').toUpperCase();
      const syncToSave: any = {
        ...formData.sync,
        selectedStrategy: formData.sync.selectedStrategy,
        ...(normalizedStrategy === 'SCHEDULED'
          ? { scheduledConfig: formData.sync.scheduledConfig || {} }
          : {}),
      };

      const configToSave: any = {
        auth: formData.auth,
        sync: syncToSave,
        filters: formData.filters || {},
      };

      // For business OAuth, merge JSON data and admin email
      if (isBusinessGoogleOAuth && jsonData) {
        configToSave.auth = {
          ...configToSave.auth,
          ...jsonData,
          adminEmail,
        };
      }

      if (isCreateMode) {
        // Create new instance
        const scope = connector.scope || 'personal';
        const created = await ConnectorApiService.createConnectorInstance(
          connector.type,
          instanceName.trim(),
          scope,
          configToSave
        );

        onSuccess?.();
        onClose();

        // Navigate to the new connector
        const basePath = isBusiness
          ? '/account/company-settings/settings/connector'
          : '/account/individual/settings/connector';
        navigate(`${basePath}/${created.connectorId}`);
        return;
      }

      // Update existing instance
      await ConnectorApiService.updateConnectorInstanceConfig(connector._key, configToSave);

      // Scheduling logic moved to enable/active changes.
      // Here: only trigger scheduling updates when the connector is already active.
      const wasActive = !!connector.isActive;
      if (wasActive) {
        const prevStrategy = String(
          connectorConfig?.config?.sync?.selectedStrategy || ''
        ).toUpperCase();
        const newStrategy = normalizedStrategy;

        const prevScheduled = (connectorConfig?.config?.sync?.scheduledConfig || {}) as any;
        const newScheduled = (formData.sync.scheduledConfig || {}) as any;

        const strategyChanged = prevStrategy !== newStrategy;
        const scheduleChanged =
          JSON.stringify({
            startTime: prevScheduled.startTime,
            intervalMinutes: prevScheduled.intervalMinutes,
            timezone: (prevScheduled.timezone || '').toUpperCase(),
          }) !==
          JSON.stringify({
            startTime: newScheduled.startTime,
            intervalMinutes: newScheduled.intervalMinutes,
            timezone: (newScheduled.timezone || '').toUpperCase(),
          });

        if (strategyChanged || (newStrategy === 'SCHEDULED' && scheduleChanged)) {
          if (prevStrategy === 'SCHEDULED' && newStrategy !== 'SCHEDULED') {
            await removeCrawlingJob(connector.type, connector._key);
          } else if (newStrategy === 'SCHEDULED') {
            // Apply (or re-apply) schedule with new settings
            await scheduleCrawlingJob(connector.type, connector._key, newScheduled);
          }
        }
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
    isCreateMode,
    instanceName,
    isBusinessGoogleOAuth,
    jsonData,
    adminEmail,
    adminEmailError,
    isBusiness,
    validateSection,
    isBusinessGoogleOAuthValid,
    scheduleCrawlingJob,
    removeCrawlingJob,
    onSuccess,
    onClose,
    navigate,
  ]);

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
