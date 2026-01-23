import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {useNavigate} from 'react-router-dom';
import { useAccountType } from 'src/hooks/use-account-type';
import { useAdmin } from 'src/context/AdminContext';
import { 
  Connector, 
  ConnectorConfig, 
  FilterSchemaField,
  FilterValueData,
  FilterValue
} from '../types/types';
import { ConnectorApiService } from '../services/api';
import { CrawlingManagerApi } from '../services/crawling-manager';
import { buildCronFromSchedule } from '../utils/cron';
import { evaluateConditionalDisplay } from '../utils/conditional-display';
import { isNoneAuthType } from '../utils/auth';
import {
  normalizeDatetimeValueForDisplay,
  normalizeDatetimeValueForSave,
  convertRelativeDateToAbsolute,
  convertDurationToMilliseconds,
  isDurationField,
  EpochDatetimeRange
} from '../utils/time-utils';

interface FormData {
  auth: Record<string, any>;
  sync: Record<string, any>;
  filters: Record<string, any>;
  [key: string]: Record<string, any>; // Index signature for dynamic access
}

interface FormErrors {
  auth: Record<string, string>;
  sync: Record<string, string>;
  filters: Record<string, string>;
  [key: string]: Record<string, string>; // Index signature for dynamic access
}

interface UseConnectorConfigProps {
  connector: Connector;
  onClose: () => void;
  onSuccess?: () => void;
  initialInstanceName?: string;
  enableMode?: boolean; // If true, opened from toggle - save filters and sync then toggle connector
  authOnly?: boolean; // If true, show only auth section
  syncOnly?: boolean; // If true, show only filters and sync (when connector is active) - DEPRECATED
  syncSettingsMode?: boolean; // If true, opened from Sync Settings button - only save filters, never toggle
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

  // Business OAuth state (Google Workspace)
  adminEmail: string;
  adminEmailError: string | null;
  selectedFile: File | null;
  fileName: string | null;
  fileError: string | null;
  jsonData: Record<string, any> | null;

  // SharePoint Certificate OAuth state
  certificateFile: File | null;
  certificateFileName: string | null;
  certificateError: string | null;
  certificateData: Record<string, any> | null;
  privateKeyFile: File | null;
  privateKeyFileName: string | null;
  privateKeyError: string | null;
  privateKeyData: string | null;

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

  // SharePoint Certificate actions
  handleCertificateUpload: () => void;
  handleCertificateChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  handlePrivateKeyUpload: () => void;
  handlePrivateKeyChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  certificateInputRef: React.RefObject<HTMLInputElement>;
  privateKeyInputRef: React.RefObject<HTMLInputElement>;
  isSharePointCertificateAuthValid: () => boolean;
  
  // Auth type selection (create mode only)
  selectedAuthType: string | null;
  handleAuthTypeChange: (authType: string) => void;
}

// Constants
const MIN_INSTANCE_NAME_LENGTH = 3;
const REQUIRED_SERVICE_ACCOUNT_FIELDS = ['client_id', 'project_id', 'type'];
const SERVICE_ACCOUNT_TYPE = 'service_account';

export const useConnectorConfig = ({
  connector,
  onClose,
  onSuccess,
  initialInstanceName = '',
  enableMode = false,
  authOnly = false,
  syncOnly = false,
  syncSettingsMode = false,
}: UseConnectorConfigProps): UseConnectorConfigReturn => {
  const { isBusiness, isIndividual, loading: accountTypeLoading } = useAccountType();
  const { isAdmin } = useAdmin();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Determine if we're in create mode (no _key means new instance)
  const isCreateMode = connector?._key === 'new' || connector?._key === null || connector?._key === undefined;

  // SharePoint certificate refs
  const certificateInputRef = useRef<HTMLInputElement>(null);
  const privateKeyInputRef = useRef<HTMLInputElement>(null);

  // State
  const [connectorConfig, setConnectorConfig] = useState<ConnectorConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [formData, setFormData] = useState<FormData>({
    auth: {} as Record<string, unknown>,
    sync: {} as Record<string, unknown>,
    filters: {} as Record<string, FilterValueData>,
  });
  const [formErrors, setFormErrors] = useState<FormErrors>({
    auth: {},
    sync: {},
    filters: {},
  });
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conditionalDisplay, setConditionalDisplay] = useState<Record<string, boolean>>({});

  // Create mode state
  const [instanceName, setInstanceName] = useState(initialInstanceName);
  const [instanceNameError, setInstanceNameError] = useState<string | null>(null);
  
  // Selected auth type (only in create mode, cannot be changed after creation)
  const [selectedAuthType, setSelectedAuthType] = useState<string | null>(null);

  // Business OAuth specific state (Google Workspace)
  const [adminEmail, setAdminEmail] = useState('');
  const [adminEmailError, setAdminEmailError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [jsonData, setJsonData] = useState<Record<string, any> | null>(null);

  // SharePoint Certificate OAuth state
  const [certificateFile, setCertificateFile] = useState<File | null>(null);
  const [certificateFileName, setCertificateFileName] = useState<string | null>(null);
  const [certificateError, setCertificateError] = useState<string | null>(null);
  const [certificateData, setCertificateData] = useState<Record<string, any> | null>(null);
  const [certificateContent, setCertificateContent] = useState<string | null>(null);

  const [privateKeyFile, setPrivateKeyFile] = useState<File | null>(null);
  const [privateKeyFileName, setPrivateKeyFileName] = useState<string | null>(null);
  const [privateKeyError, setPrivateKeyError] = useState<string | null>(null);
  const [privateKeyData, setPrivateKeyData] = useState<string | null>(null);

  // Memoized helper to check if this is custom Google Business OAuth
  // Checks both scope and account type to determine if this is Google Workspace business account
  const isCustomGoogleBusinessOAuth = useMemo(
    () =>
      isBusiness &&
      connector.appGroup === 'Google Workspace' &&
      connector.authType === 'OAUTH' &&
      connector.scope === 'team',
    [isBusiness, connector.appGroup, connector.authType, connector.scope]
  );

  // Memoized helper to check if this is SharePoint certificate auth
  const isSharePointCertificateAuth = useMemo(
    () =>
      connector.type === 'SharePoint Online' &&
      (connector.authType === 'OAUTH_CERTIFICATE' || connector.authType === 'OAUTH_ADMIN_CONSENT') &&
      connector.scope === 'team',
    [connector.type, connector.authType, connector.scope]
  );

  // Memoized helper functions
  const getBusinessOAuthData = useCallback((config: any) => {
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

  // Helper to get SharePoint certificate data from existing config
  const getSharePointCertificateData = useCallback((config: any) => {
    const authValues = config?.config?.auth?.values || config?.config?.auth || {};
    return {
      certificate: authValues.certificate || null,
      privateKey: authValues.privateKey || null,
      certificateFileName: authValues.certificate ? 'existing-certificate.pem' : null,
      privateKeyFileName: authValues.privateKey ? 'existing-privatekey.pem' : null,
    };
  }, []);

  const mergeConfigWithSchema = useCallback(
    (configResponse: any, schemaResponse: any) => {
      // Extract stored auth type from config (can be in auth.authType or auth.type)
      const storedAuthType = configResponse.config?.auth?.authType || 
                             configResponse.config?.auth?.type || 
                             configResponse.authType || 
                             schemaResponse.auth?.type || 
                             '';
      
      // Get stored auth values - handle both formats: { values: {...} } and direct {...}
      const storedAuthValues = configResponse.config?.auth?.values || 
                               configResponse.config?.auth || {};
      
      // Remove authType from values if it exists (it's metadata, not a field value)
      const { authType: _, type: __, connectorScope: ___, ...cleanAuthValues } = storedAuthValues;
      
      return {
        ...configResponse,
        config: {
          documentationLinks: schemaResponse.documentationLinks || [],
          auth: {
            ...schemaResponse.auth,
            // Preserve the stored auth type - this is critical for loading the correct schema
            type: storedAuthType,
            // Store the clean auth values (without metadata fields)
            values: cleanAuthValues,
            customValues: configResponse.config?.auth?.customValues || {},
          },
          sync: {
            ...schemaResponse.sync,
            selectedStrategy:
              configResponse.config?.sync?.selectedStrategy ||
              schemaResponse.sync.supportedStrategies?.[0] ||
              'MANUAL',
            scheduledConfig: configResponse.config?.sync?.scheduledConfig || {},
            values: configResponse.config?.sync?.values || configResponse.config?.sync || {},
            customValues: configResponse.config?.sync?.customValues || {},
          },
          filters: {
            ...schemaResponse.filters,
            sync: {
              ...schemaResponse.filters?.sync,
              values: configResponse.config?.filters?.sync?.values || {},
            },
            indexing: {
              ...schemaResponse.filters?.indexing,
              values: configResponse.config?.filters?.indexing?.values || {},
            },
          },
        },
      };
    },
    []
  );

  const initializeFormData = useCallback((config: any, authType?: string) => {
    // Use provided auth type or existing auth type
    const currentAuthType = authType || config.config.auth?.type || '';
    
    // Get schema for the current auth type
    const authSchemas = config.config.auth?.schemas || {};
    const currentAuthSchema = currentAuthType && authSchemas[currentAuthType] 
      ? { fields: authSchemas[currentAuthType].fields || [] }
      : { fields: [] };
    
    // Initialize authData with existing values or empty object
    const existingAuthValues = config.config.auth?.values || {};
    const authData: Record<string, any> = { ...existingAuthValues };

    // Set default values from field definitions for the selected auth type
    // Also ensure all fields exist in authData (even if empty) so they're bound properly
    if (currentAuthSchema?.fields) {
      currentAuthSchema.fields.forEach((field: any) => {
        // If field has a default value and no existing value, use default
        if (field.defaultValue !== undefined && authData[field.name] === undefined) {
          authData[field.name] = field.defaultValue;
        }
        // If field has no value at all, initialize with empty string (for text fields)
        // This ensures the field is bound to formData and can be updated
        else if (authData[field.name] === undefined) {
          // Only initialize with empty string for text-like fields
          // Other field types (boolean, number, etc.) should remain undefined
          if (field.fieldType === 'TEXT' || field.fieldType === 'PASSWORD' || 
              field.fieldType === 'EMAIL' || field.fieldType === 'URL' || 
              field.fieldType === 'TEXTAREA') {
            authData[field.name] = '';
          }
        }
      });
    }

    // Initialize filters (both sync and indexing)
    const filtersData: Record<string, FilterValueData> = {};

    /**
     * Get default value for a filter field based on its type
     */
    const getDefaultFilterValue = (field: FilterSchemaField): FilterValue => {
      if (field.defaultValue !== undefined) {
        return field.defaultValue;
      }
      
      switch (field.filterType) {
        case 'list':
          return [];
        case 'datetime':
          return { start: '', end: '' };
        case 'boolean':
          return true;
        default:
          return '';
      }
    };

    /**
     * Initialize filter fields - only initialize filters that have existing values
     */
    const initializeFilterFields = (
      filterSchema: { fields: FilterSchemaField[] }, 
      filterValues: Record<string, FilterValueData> | undefined
    ) => {
      if (!filterSchema?.fields) return;
      
      filterSchema.fields.forEach((field) => {
        const existingValue = filterValues?.[field.name];
        
        // Only initialize if there's an existing value from saved config
        if (existingValue === undefined || existingValue === null) {
          return; // Don't initialize filters without existing values - start blank
        }
        
        // If value exists, use it (could be { operator, value } or just value)
        if (
          typeof existingValue === 'object' && 
          !Array.isArray(existingValue) && 
          'operator' in existingValue
        ) {
          // Already in the correct format: { operator: '...', value: '...' }
          const operator = existingValue.operator || field.defaultOperator || '';
          const needsValue = !operator.startsWith('last_');
          
          let value: FilterValue = needsValue 
            ? (existingValue.value !== undefined 
                ? existingValue.value 
                : getDefaultFilterValue(field))
            : null;
          
          // Normalize datetime values to {start, end} format for display
          if (field.filterType === 'datetime' && value !== null && !operator.startsWith('last_')) {
            value = normalizeDatetimeValueForDisplay(value, operator);
          }
          
          filtersData[field.name] = {
            operator,
            value,
            type: field.filterType,
          };
        } else {
          // Value exists but not in the right format, wrap it
          let value: FilterValue = existingValue as FilterValue;
          
          // Normalize datetime values to {start, end} format
          if (field.filterType === 'datetime') {
            const operator = field.defaultOperator || '';
            value = normalizeDatetimeValueForDisplay(value, operator);
          }
          
          filtersData[field.name] = {
            operator: field.defaultOperator || '',
            value,
            type: field.filterType,
          };
        }
      });
    };
    
    // Initialize sync filters
    const syncFilters = config.config.filters?.sync;
    if (syncFilters?.schema) {
      initializeFilterFields(syncFilters.schema, config.config.filters?.sync?.values);
    }
    
    // Initialize indexing filters
    const indexingFilters = config.config.filters?.indexing;
    if (indexingFilters?.schema) {
      initializeFilterFields(indexingFilters.schema, config.config.filters?.indexing?.values);
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
      filters: filtersData,
    };
  }, []);

  // Business OAuth validation (Google Workspace)
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

      // Validate required fields for Google Cloud Service Account JSON
      const requiredFields = ['client_id', 'project_id', 'type'];
      const missingFields = requiredFields.filter((field) => !parsed[field]);

      if (missingFields.length > 0) {
        setFileError(`Missing required fields: ${missingFields.join(', ')}`);
        return null;
      }

      // Validate that it's a service account JSON
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

  // SharePoint Certificate validation functions
  const validateCertificateFile = useCallback((content: string): boolean => {
    const certificateRegex = /-----BEGIN CERTIFICATE-----[\s\S]+-----END CERTIFICATE-----/;
    if (!certificateRegex.test(content)) {
      setCertificateError(
        'Invalid certificate format. Must contain BEGIN CERTIFICATE and END CERTIFICATE markers.'
      );
      return false;
    }
    setCertificateError(null);
    return true;
  }, []);

  const validatePrivateKeyFile = useCallback((content: string): boolean => {
    // Check for PKCS#8 format (no RSA in headers)
    const pkcs8Regex = /-----BEGIN PRIVATE KEY-----[\s\S]+-----END PRIVATE KEY-----/;
    const rsaRegex = /-----BEGIN RSA PRIVATE KEY-----/;

    if (rsaRegex.test(content)) {
      setPrivateKeyError(
        'Private key must be in PKCS#8 format. Use: openssl pkcs8 -topk8 -inform PEM -outform PEM -in privatekey.key -out privatekey.key -nocrypt'
      );
      return false;
    }

    if (!pkcs8Regex.test(content)) {
      setPrivateKeyError(
        'Invalid private key format. Must contain BEGIN PRIVATE KEY and END PRIVATE KEY markers.'
      );
      return false;
    }

    // Check if encrypted (should not contain ENCRYPTED in headers)
    if (content.includes('ENCRYPTED')) {
      setPrivateKeyError(
        'Private key must not be encrypted. Use -nocrypt flag during generation.'
      );
      return false;
    }

    setPrivateKeyError(null);
    return true;
  }, []);

  const parseCertificateInfo = useCallback((certContent: string): Record<string, any> => ({
    status: 'Valid',
    format: 'X.509',
    loaded: new Date().toISOString(),
  }), []);

  // SharePoint Certificate handlers
  const handleCertificateUpload = useCallback(() => {
    certificateInputRef.current?.click();
  }, []);

  const handleCertificateChange = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;

      // Validate file type
      const validExtensions = ['.crt', '.cer', '.pem'];
      const fileExtension = file.name.toLowerCase().substring(file.name.lastIndexOf('.'));

      if (!validExtensions.includes(fileExtension)) {
        setCertificateError('Invalid file type. Please upload a .crt, .cer, or .pem file.');
        return;
      }

      try {
        // Read file content
        const content = await file.text();

        // Validate certificate format
        if (!validateCertificateFile(content)) {
          return;
        }

        // Store certificate data
        setCertificateFile(file);
        setCertificateFileName(file.name);
        setCertificateContent(content);
        setCertificateError(null);

        // Clear save error when user uploads a valid file
        setSaveError(null);

        // Parse certificate information (basic parsing for display)
        try {
          const certInfo = parseCertificateInfo(content);
          setCertificateData(certInfo);
        } catch (parseError) {
          console.warn('Could not parse certificate info:', parseError);
          setCertificateData({ status: 'Valid certificate loaded' });
        }

        // Update form data with certificate content
        setFormData((prev) => ({
          ...prev,
          auth: {
            ...prev.auth,
            certificate: content,
          },
        }));
      } catch (error) {
        setCertificateError('Failed to read certificate file');
        console.error('Certificate read error:', error);
      }
    },
    [validateCertificateFile, parseCertificateInfo]
  );

  const handlePrivateKeyUpload = useCallback(() => {
    privateKeyInputRef.current?.click();
  }, []);

  const handlePrivateKeyChange = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;

      // Validate file type
      const validExtensions = ['.key', '.pem'];
      const fileExtension = file.name.toLowerCase().substring(file.name.lastIndexOf('.'));

      if (!validExtensions.includes(fileExtension)) {
        setPrivateKeyError('Invalid file type. Please upload a .key or .pem file.');
        return;
      }

      try {
        // Read file content
        const content = await file.text();

        // Validate private key format
        if (!validatePrivateKeyFile(content)) {
          return;
        }

        // Store private key data
        setPrivateKeyFile(file);
        setPrivateKeyFileName(file.name);
        setPrivateKeyData(content);
        setPrivateKeyError(null);

        // Clear save error when user uploads a valid file
        setSaveError(null);

        // Update form data with private key content
        setFormData((prev) => ({
          ...prev,
          auth: {
            ...prev.auth,
            privateKey: content,
          },
        }));
      } catch (error) {
        setPrivateKeyError('Failed to read private key file');
        console.error('Private key read error:', error);
      }
    },
    [validatePrivateKeyFile]
  );

  const isSharePointCertificateAuthValid = useCallback((): boolean => {
    if (!isSharePointCertificateAuth) {
      return true; // Not SharePoint, so this validation doesn't apply
    }

    // Check required fields
    const hasClientId = formData.auth.clientId && String(formData.auth.clientId).trim() !== '';
    const hasTenantId = formData.auth.tenantId && String(formData.auth.tenantId).trim() !== '';
    const hasSharePointDomain = formData.auth.sharepointDomain && String(formData.auth.sharepointDomain).trim() !== '';
    const hasAdminConsent = formData.auth.hasAdminConsent === true;
    
    // Check for certificate content - either from file upload or from existing config in formData
    const hasCertificate = !!(certificateContent || formData.auth.certificate);
    const hasPrivateKey = !!(privateKeyData || formData.auth.privateKey);

    // Check for validation errors (only if they have actual error messages)
    const hasErrors =
      (certificateError !== null && certificateError !== '') ||
      (privateKeyError !== null && privateKeyError !== '');

    const isValid = hasClientId && hasTenantId && hasSharePointDomain && hasAdminConsent && hasCertificate && hasPrivateKey && !hasErrors;
    
    // Update form errors for SharePoint fields
    if (!isValid) {
      const errors: Record<string, string> = {};
      if (!hasClientId) errors.clientId = 'Application (Client) ID is required';
      if (!hasTenantId) errors.tenantId = 'Directory (Tenant) ID is required';
      if (!hasSharePointDomain) errors.sharepointDomain = 'SharePoint Domain is required';
      if (!hasCertificate) errors.certificate = 'Certificate file is required';
      if (!hasPrivateKey) errors.privateKey = 'Private key file is required';
      
      setFormErrors((prev) => ({
        ...prev,
        auth: {
          ...prev.auth,
          ...errors,
        },
      }));
    }

    return isValid;
  }, [
    isSharePointCertificateAuth,
    formData.auth,
    certificateContent,
    privateKeyData,
    certificateError,
    privateKeyError,
  ]);

  // Load connector configuration - simplified with proper dependency management
  useEffect(() => {
    // Skip if account type is still loading
    if (accountTypeLoading) {
      return undefined;
    }

    let isMounted = true;

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

        if (!isMounted) return;

        setConnectorConfig(mergedConfig);

        // In create mode, initialize selected auth type to first available supported type
        // Only set if not already set (to prevent resetting user's selection)
        if (isCreateMode) {
          const supportedAuthTypes = mergedConfig.config.auth?.supportedAuthTypes || [];
          if (supportedAuthTypes.length > 0 && selectedAuthType === null) {
            setSelectedAuthType(supportedAuthTypes[0]);
          }
        }

        // Initialize form data with selected auth type
        // In create mode: use selectedAuthType or first supported type
        // In edit mode: use the stored auth type from the config (this is the auth type that was saved when connector was created)
        const currentAuthType = isCreateMode 
          ? (selectedAuthType || mergedConfig.config.auth?.supportedAuthTypes?.[0] || mergedConfig.config.auth?.type || '')
          : (mergedConfig.config.auth?.type || ''); // Use stored auth type from config
        
        // Ensure the merged config has the correct auth type set
        if (currentAuthType && mergedConfig.config.auth) {
          mergedConfig.config.auth.type = currentAuthType;
        }
        
        const initialFormData = initializeFormData(mergedConfig, currentAuthType);

        // For business OAuth, load admin email and JSON data from existing config
        if (isCustomGoogleBusinessOAuth) {
          const businessData = getBusinessOAuthData(mergedConfig);
          setAdminEmail(businessData.adminEmail);
          setJsonData(businessData.jsonData);
          setFileName(businessData.fileName);
        }

        // For SharePoint certificate auth, load existing certificate data
        if (isSharePointCertificateAuth) {
          const sharePointData = getSharePointCertificateData(mergedConfig);
          if (sharePointData.certificate) {
            setCertificateContent(sharePointData.certificate);
            setCertificateFileName(sharePointData.certificateFileName);
            setCertificateData({ status: 'Valid certificate loaded' });
          }
          if (sharePointData.privateKey) {
            setPrivateKeyData(sharePointData.privateKey);
            setPrivateKeyFileName(sharePointData.privateKeyFileName);
          }
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
      } catch (error: any) {
        if (!isMounted) return;
        console.error('Error fetching connector config:', error);

        // Check if it's a beta connector access denied error (403)
        if (error?.response?.status === 403) {
          const errorMessage = error?.response?.data?.detail || error?.message || 'Beta connectors are not enabled. This connector is a beta connector and cannot be accessed. Please enable beta connectors in platform settings to use this connector.';
          setSaveError(errorMessage);
        } else {
          setSaveError(error?.response?.data?.detail || error?.message || 'Failed to load connector configuration');
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    fetchConnectorConfig();

    return () => {
      isMounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    connector.name,
    connector.type,
    connector.appGroup,
    connector.appGroupId,
    connector.authType,
    connector.supportsRealtime,
    connector.appDescription,
    connector.appCategories,
    connector.iconPath,
    connector._key,
    isCreateMode,
    accountTypeLoading,
    isCustomGoogleBusinessOAuth,
    isSharePointCertificateAuth,
    mergeConfigWithSchema,
    initializeFormData,
    getBusinessOAuthData,
    getSharePointCertificateData,
    // Intentionally excluded selectedAuthType from dependencies to prevent re-fetch on auth type change.
    // The auth type change is handled by handleAuthTypeChange which updates formData directly,
    // avoiding unnecessary API calls and preventing UI flickering.
  ]);

  // Reset activeStep to 0 when mode changes (create/enable/edit)
  useEffect(() => {
    setActiveStep(0);
  }, [enableMode, syncOnly, isCreateMode]);

  // Recalculate conditional display when auth form data changes
  // Use a ref to track previous auth data to avoid unnecessary recalculations
  const prevAuthDataRef = useRef<Record<string, any>>({});
  useEffect(() => {
    if (connectorConfig?.config?.auth?.conditionalDisplay) {
      // Only recalculate if auth data actually changed
      const authDataString = JSON.stringify(formData.auth);
      const prevAuthDataString = JSON.stringify(prevAuthDataRef.current);
      
      if (authDataString !== prevAuthDataString) {
        const displayRules = evaluateConditionalDisplay(
          connectorConfig.config.auth.conditionalDisplay,
          formData.auth
        );
        setConditionalDisplay(displayRules);
        prevAuthDataRef.current = { ...formData.auth };
      }
    }
  }, [formData.auth, connectorConfig?.config?.auth?.conditionalDisplay]);

  // Field validation
  const validateField = useCallback((field: any, value: any): string => {
    if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
      return `${field.displayName} is required`;
    }

    if (field.validation) {
      const { minLength, maxLength, format } = field.validation;

      if (minLength && value && value.length < minLength) {
        return `${field.displayName} must be at least ${minLength} characters`;
      }

   // Skip maxLength validation for serviceAccountJson to allow larger JSON files
    if (maxLength && value && value.length > maxLength && field.name !== 'serviceAccountJson') {
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
            try {
              // eslint-disable-next-line no-new
              new URL(value);
            } catch {
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
        // Ensure section exists and is an object
        const currentSectionData = (prev[section as keyof FormData] || {}) as Record<string, any>;
        
        // Handle filter removal (when value is undefined)
        if (section === 'filters' && value === undefined) {
          const { [fieldName]: removed, ...rest } = currentSectionData;
          return {
            ...prev,
            [section]: rest,
          };
        }
        
        // Normal field update - create new object to ensure React detects the change
        const updatedSectionData = {
          ...currentSectionData,
          [fieldName]: value,
        };

        const newFormData = {
          ...prev,
          [section]: updatedSectionData,
        };

        // Re-evaluate conditional display rules for auth section
        if (section === 'auth' && connectorConfig?.config.auth.conditionalDisplay) {
          const displayRules = evaluateConditionalDisplay(
            connectorConfig.config.auth.conditionalDisplay,
            newFormData.auth as Record<string, any>
          );
          setConditionalDisplay(displayRules);
        }

        return newFormData;
      });

      // Clear error for this field
      setFormErrors((prev) => {
        const currentSectionErrors = (prev[section as keyof FormErrors] || {}) as Record<string, string>;
        return {
          ...prev,
          [section]: {
            ...currentSectionErrors,
            [fieldName]: '',
          },
        };
      });

      // Clear save error when user makes changes
      setSaveError(null);
    },
    [connectorConfig]
  );

  const handleNext = useCallback(() => {
    if (!connectorConfig) return;

    // Clear any previous save error when user tries again
    setSaveError(null);

    const isNoAuthType = isNoneAuthType(connector.authType);
    const hasFilters = (connectorConfig.config.filters?.sync?.schema?.fields?.length ?? 0) > 0;
    
    // Determine which step we're on based on mode
    let currentSection = '';
    let maxStep = 0;
    
    // Sync Settings mode: filters (if available) -> sync (skip auth)
    if (syncSettingsMode || enableMode) {
      maxStep = hasFilters ? 1 : 0; // Filters (0) -> Sync (1) or just Sync (0)
      if (hasFilters) {
        currentSection = activeStep === 0 ? 'filters' : 'sync';
      } else {
        currentSection = 'sync';
      }
    }
    // Create mode: only auth (no next button, should not reach here)
    else if (isCreateMode) {
      maxStep = 0; // Only one step (auth)
      currentSection = 'auth';
    }
    // Edit mode: show all steps based on auth type
    else if (isNoAuthType) {
      maxStep = hasFilters ? 1 : 0; // Filters (0) -> Sync (1) or just Sync (0)
      if (hasFilters) {
        currentSection = activeStep === 0 ? 'filters' : 'sync';
      } else {
        currentSection = 'sync';
      }
    } else {
      maxStep = hasFilters ? 2 : 1; // Auth (0) -> Filters (1) -> Sync (2) or Auth (0) -> Sync (1)
      if (hasFilters) {
        currentSection = activeStep === 0 ? 'auth' : activeStep === 1 ? 'filters' : 'sync';
      } else {
        currentSection = activeStep === 0 ? 'auth' : 'sync';
      }
    }

    let errors: Record<string, string> = {};

    // Validate current step
    if (currentSection === 'auth') {
      if (isCustomGoogleBusinessOAuth) {
        // Validate Google Business OAuth
        const businessErrors: Record<string, string> = {};
        
        // Validate admin email (adminEmailError is already set by validateAdminEmail)
        if (!adminEmail || !adminEmail.trim()) {
          setAdminEmailError('Admin email is required');
          businessErrors.adminEmail = 'Admin email is required';
        } else if (adminEmailError) {
          businessErrors.adminEmail = adminEmailError;
        }
        
        // Validate JSON file
        if (!jsonData) {
          setFileError('Service account credentials file is required');
          businessErrors.jsonFile = 'Service account credentials file is required';
        } else if (fileError) {
          businessErrors.jsonFile = fileError;
        }
        
        errors = businessErrors;
      } else if (isSharePointCertificateAuth) {
        // Validate SharePoint certificate authentication
        const sharePointErrors: Record<string, string> = {};
        
        // Validate required fields
        const hasClientId = formData.auth.clientId && String(formData.auth.clientId).trim() !== '';
        const hasTenantId = formData.auth.tenantId && String(formData.auth.tenantId).trim() !== '';
        const hasSharePointDomain = formData.auth.sharepointDomain && String(formData.auth.sharepointDomain).trim() !== '';
        const hasAdminConsent = formData.auth.hasAdminConsent === true;
        
        if (!hasClientId) sharePointErrors.clientId = 'Application (Client) ID is required';
        if (!hasTenantId) sharePointErrors.tenantId = 'Directory (Tenant) ID is required';
        if (!hasSharePointDomain) sharePointErrors.sharepointDomain = 'SharePoint Domain is required';
        if (!hasAdminConsent) sharePointErrors.hasAdminConsent = 'Admin consent is required';
        
        // Validate certificate and private key
        const hasCertificate = !!(certificateContent || formData.auth.certificate);
        const hasPrivateKey = !!(privateKeyData || formData.auth.privateKey);
        
        if (!hasCertificate) sharePointErrors.certificate = 'Certificate file is required';
        if (!hasPrivateKey) sharePointErrors.privateKey = 'Private key file is required';
        
        // Check for file validation errors
        if (certificateError) sharePointErrors.certificate = certificateError;
        if (privateKeyError) sharePointErrors.privateKey = privateKeyError;
        
        errors = sharePointErrors;
      } else {
        // Get schema for current auth type
        const validationAuthType = isCreateMode
          ? (selectedAuthType || connectorConfig.config.auth?.supportedAuthTypes?.[0] || '')
          : (connectorConfig.config.auth?.type || '');
        const validationAuthSchemas = connectorConfig.config.auth?.schemas || {};
        const validationSchema = validationAuthType && validationAuthSchemas[validationAuthType]
          ? validationAuthSchemas[validationAuthType]
          : { fields: [] };
        
        // Validate schema fields
        errors = validateSection(
          'auth',
          validationSchema.fields || [],
          formData.auth
        );
        
        // For OAuth type: Additional validation for creating new OAuth apps
        if (validationAuthType === 'OAUTH' && !formData.auth.oauthConfigId && isAdmin) {
          // If no OAuth config is selected, validate that credential fields are filled
          const oauthFieldNames = (validationSchema.fields || []).map((f: any) => f.name);
          oauthFieldNames.forEach((fieldName: string) => {
            const field = validationSchema.fields?.find((f: any) => f.name === fieldName);
            if (field && field.required) {
              const value = formData.auth[fieldName];
              if (!value || (typeof value === 'string' && !value.trim())) {
                errors[fieldName] = `${field.displayName || fieldName} is required`;
              }
            }
          });
          
          // Validate oauthInstanceName is required when creating new OAuth app
          const instanceNameValue = formData.auth.oauthInstanceName;
          if (!instanceNameValue || (typeof instanceNameValue === 'string' && !instanceNameValue.trim())) {
            errors.oauthInstanceName = 'OAuth App Instance Name is required when creating a new OAuth app';
          }
        }
      }
    } else if (currentSection === 'filters') {
      // Filters are optional, so no validation needed
      errors = {};
    } else if (currentSection === 'sync') {
      errors = validateSection('sync', connectorConfig.config.sync.customFields, formData.sync);
    }

    setFormErrors((prev) => ({
      ...prev,
      [currentSection]: errors,
    }));

    if (Object.keys(errors).length === 0 && activeStep < maxStep) {
      setActiveStep((prev) => prev + 1);
    }
  }, [
    activeStep,
    connectorConfig,
    connector,
    formData,
    validateSection,
    isCustomGoogleBusinessOAuth,
    adminEmail,
    adminEmailError,
    jsonData,
    fileError,
    isSharePointCertificateAuth,
    certificateContent,
    certificateError,
    privateKeyData,
    privateKeyError,
    enableMode,
    syncSettingsMode,
    isCreateMode,
    selectedAuthType,
    isAdmin,
  ]);

  const handleBack = useCallback(() => {
    if (activeStep > 0) {
      setActiveStep((prev) => prev - 1);
    }
  }, [activeStep]);

  // Helper function to process filter fields for saving
  const processFilterFields = useCallback((
    filterSchema: { fields: FilterSchemaField[] }
  ): Record<string, FilterValueData> => {
    const filtersToSave: Record<string, FilterValueData> = {};
    if (!filterSchema?.fields) return filtersToSave;
    
    filterSchema.fields.forEach((field) => {
      const filterValue = formData.filters[field.name];
      if (!filterValue?.operator) {
        return; // Skip filters without operator
      }
      
      // Convert relative date operators to absolute dates
      if (filterValue.operator.startsWith('last_')) {
        const converted = convertRelativeDateToAbsolute(filterValue.operator);
        if (converted) {
          try {
            const normalizedValue = normalizeDatetimeValueForSave(
              converted.value, 
              converted.operator
            );
            if (normalizedValue.start !== null || normalizedValue.end !== null) {
              filtersToSave[field.name] = { 
                operator: converted.operator, 
                value: normalizedValue, 
                type: field.filterType 
              };
            }
          } catch (error) {
            console.warn(`Failed to convert relative date operator for ${field.name}:`, error);
          }
        }
        return;
      }
      
      // Handle filters with values
      if (filterValue.value === undefined || filterValue.value === null || filterValue.value === '') {
        if (field.filterType === 'boolean') {
          filtersToSave[field.name] = { 
            operator: filterValue.operator, 
            value: filterValue.value, 
            type: field.filterType 
          };
        }
        return;
      }
      
      // Process datetime fields
      if (field.filterType === 'datetime') {
        try {
          const normalizedValue = normalizeDatetimeValueForSave(
            filterValue.value, 
            filterValue.operator
          );
          if (normalizedValue.start !== null || normalizedValue.end !== null) {
            filtersToSave[field.name] = { 
              operator: filterValue.operator, 
              value: normalizedValue, 
              type: field.filterType 
            };
          }
        } catch (error) {
          console.warn(`Failed to normalize datetime value for ${field.name}:`, error);
        }
        return;
      }
      
      // Process list values
      if (Array.isArray(filterValue.value) && filterValue.value.length > 0) {
        let processedValue: FilterValue = filterValue.value;
        if (isDurationField(field)) {
          processedValue = filterValue.value.map((item: unknown) => 
            typeof item === 'string' ? convertDurationToMilliseconds(item) : item
          );
        }
        filtersToSave[field.name] = { 
          operator: filterValue.operator, 
          value: processedValue, 
          type: field.filterType 
        };
        return;
      }
      
      // Process non-array values
      if (!Array.isArray(filterValue.value) && filterValue.value !== '') {
        let processedValue: FilterValue = filterValue.value;
        if (isDurationField(field) && typeof filterValue.value === 'string') {
          processedValue = convertDurationToMilliseconds(filterValue.value);
        }
        filtersToSave[field.name] = { 
          operator: filterValue.operator, 
          value: processedValue, 
          type: field.filterType 
        };
      }
    });
    
    return filtersToSave;
  }, [formData.filters]);

  // Helper function to prepare sync config for saving
  const prepareSyncConfig = useCallback((): any => {
    const syncToSave: any = {
      selectedStrategy: formData.sync.selectedStrategy,
      ...formData.sync,
    };
    
    // Process sync custom fields to convert duration values
    if (connectorConfig?.config?.sync?.customFields) {
      connectorConfig.config.sync.customFields.forEach((field: any) => {
        if (syncToSave[field.name] !== undefined && isDurationField(field)) {
          const value = syncToSave[field.name];
          if (typeof value === 'string') {
            syncToSave[field.name] = convertDurationToMilliseconds(value);
          }
        }
      });
    }
    
    const normalizedStrategy = String(formData.sync.selectedStrategy || '').toUpperCase();
    if (normalizedStrategy === 'SCHEDULED') {
      syncToSave.scheduledConfig = formData.sync.scheduledConfig || {};
    } else if (syncToSave.scheduledConfig !== undefined) {
      delete syncToSave.scheduledConfig;
    }

    return syncToSave;
  }, [formData.sync, connectorConfig]);

  // Helper function to prepare filters payload
  const prepareFiltersPayload = useCallback((): any => {
    const syncFiltersToSave = connectorConfig?.config?.filters?.sync?.schema
      ? processFilterFields(connectorConfig.config.filters.sync.schema)
      : {};
    
    const indexingFiltersToSave = connectorConfig?.config?.filters?.indexing?.schema
      ? processFilterFields(connectorConfig.config.filters.indexing.schema)
      : {};

    const filtersPayload: any = {
      sync: {
        values: syncFiltersToSave,
      },
    };
    if (Object.keys(indexingFiltersToSave).length > 0) {
      filtersPayload.indexing = {
        values: indexingFiltersToSave,
      };
    }

    return filtersPayload;
  }, [connectorConfig, processFilterFields]);

  // Helper function to update scheduling based on sync strategy changes
  const updateScheduling = useCallback(async (isConnectorActiveAfterSave: boolean = false) => {
    if (!connectorConfig || !connector) return;

    // Determine if connector is/will be active
    const isActive = isConnectorActiveAfterSave || !!connector.isActive;
    
    // Only update scheduling if connector is or will be active
    if (!isActive) {
      return; // Connector is not active, no need to update scheduling
    }

    const prevStrategy = String(
      connectorConfig?.config?.sync?.selectedStrategy || ''
    ).toUpperCase();
    const newStrategy = String(formData.sync.selectedStrategy || '').toUpperCase();

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

    try {
      if (newStrategy === 'SCHEDULED') {
        // Strategy is SCHEDULED - ensure schedule is applied
        const cron = buildCronFromSchedule({
          startTime: newScheduled.startTime,
          intervalMinutes: newScheduled.intervalMinutes,
          timezone: (newScheduled.timezone || 'UTC').toUpperCase(),
        });
        await CrawlingManagerApi.schedule(connector.type, connector._key, {
          scheduleConfig: {
            scheduleType: 'custom',
            isEnabled: true,
            timezone: (newScheduled.timezone || 'UTC').toUpperCase(),
            cronExpression: cron,
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        });
      } else if (strategyChanged && prevStrategy === 'SCHEDULED' && newStrategy !== 'SCHEDULED') {
        // Strategy changed from SCHEDULED to something else - remove schedule
        await CrawlingManagerApi.remove(connector.type, connector._key);
      }
    } catch (scheduleError) {
      console.error('Scheduling update failed:', scheduleError);
      // Do not block saving on scheduling issues
    }
  }, [connectorConfig, connector, formData.sync]);

  const handleSave = useCallback(async () => {
    if (!connectorConfig) return;

    try {
      setSaving(true);
      setSaveError(null);

      const isNoAuthType = isNoneAuthType(connector.authType);

      // For auth-only mode, check if connector is active (auth changes require disabled connector)
      if (authOnly && connector.isActive) {
        setSaveError('Cannot update authentication while connector is active. Please disable the connector first.');
        setSaving(false);
        return;
      }

      // Auth only mode: save auth config only
      if (authOnly) {
        // Validate auth section
        let authErrors: Record<string, string> = {};
        if (!isNoAuthType) {
          if (isCustomGoogleBusinessOAuth) {
            // Validate Google Business OAuth
            if (!adminEmail || !adminEmail.trim()) {
              setAdminEmailError('Admin email is required');
              authErrors.adminEmail = 'Admin email is required';
            } else if (adminEmailError) {
              authErrors.adminEmail = adminEmailError;
            }
            
            if (!jsonData) {
              setFileError('Service account credentials file is required');
              authErrors.jsonFile = 'Service account credentials file is required';
            } else if (fileError) {
              authErrors.jsonFile = fileError;
            }
          } else if (isSharePointCertificateAuth) {
            // Validate SharePoint certificate authentication
            const hasClientId = formData.auth.clientId && String(formData.auth.clientId).trim() !== '';
            const hasTenantId = formData.auth.tenantId && String(formData.auth.tenantId).trim() !== '';
            const hasSharePointDomain = formData.auth.sharepointDomain && String(formData.auth.sharepointDomain).trim() !== '';
            const hasAdminConsent = formData.auth.hasAdminConsent === true;
            
            if (!hasClientId) authErrors.clientId = 'Application (Client) ID is required';
            if (!hasTenantId) authErrors.tenantId = 'Directory (Tenant) ID is required';
            if (!hasSharePointDomain) authErrors.sharepointDomain = 'SharePoint Domain is required';
            if (!hasAdminConsent) authErrors.hasAdminConsent = 'Admin consent is required';
            
            const hasCertificate = !!(certificateContent || formData.auth.certificate);
            const hasPrivateKey = !!(privateKeyData || formData.auth.privateKey);
            
            if (!hasCertificate) authErrors.certificate = 'Certificate file is required';
            if (!hasPrivateKey) authErrors.privateKey = 'Private key file is required';
            
            if (certificateError) authErrors.certificate = certificateError;
            if (privateKeyError) authErrors.privateKey = privateKeyError;
          } else {
            // Use schema from existing auth type in edit mode (authOnly mode)
            const validationAuthType = connectorConfig.config.auth?.type || '';
            const validationAuthSchemas = connectorConfig.config.auth?.schemas || {};
            const validationSchema = validationAuthType && validationAuthSchemas[validationAuthType]
              ? validationAuthSchemas[validationAuthType]
              : connectorConfig.config.auth?.schema || { fields: [] };
            
            authErrors = validateSection(
              'auth',
              validationSchema.fields || [],
              formData.auth
            );
            
            // For OAuth type: Additional validation for creating new OAuth apps
            if (validationAuthType === 'OAUTH' && !formData.auth.oauthConfigId && isAdmin) {
              // If no OAuth config is selected, validate that credential fields are filled
              const oauthFieldNames = (validationSchema.fields || []).map((f: any) => f.name);
              oauthFieldNames.forEach((fieldName: string) => {
                const field = validationSchema.fields?.find((f: any) => f.name === fieldName);
                if (field && field.required) {
                  const value = formData.auth[fieldName];
                  if (!value || (typeof value === 'string' && !value.trim())) {
                    authErrors[fieldName] = `${field.displayName || fieldName} is required`;
                  }
                }
              });
              
              // Validate oauthInstanceName if creating new OAuth app
              if (!formData.auth.oauthConfigId) {
                const instanceNameValue = formData.auth.oauthInstanceName;
                if (!instanceNameValue || (typeof instanceNameValue === 'string' && !instanceNameValue.trim())) {
                  authErrors.oauthInstanceName = 'OAuth App Instance Name is required when creating a new OAuth app';
                }
              }
            }
          }
        }

        if (Object.keys(authErrors).length > 0) {
          setFormErrors((prev) => ({ ...prev, auth: authErrors }));
          setSaving(false);
          return;
        }

        // Prepare auth config
        let authToSave = { ...formData.auth };

        // For OAuth: Ensure oauthInstanceName is included if creating new OAuth app
        // The backend uses this to create a new OAuth config when oauthConfigId is not provided
        // If not provided, backend will use connector instance name as fallback
        if (formData.auth.oauthInstanceName) {
          authToSave.oauthInstanceName = formData.auth.oauthInstanceName;
        }

        // For OAuth: Remove oauthConfigId if it's null/empty to ensure backend creates new OAuth config
        // Only keep it if an existing OAuth config is selected
        if (!authToSave.oauthConfigId) {
          // Explicitly remove this field to ensure backend creates new OAuth config
          delete authToSave.oauthConfigId;
        }

        // For business OAuth, merge JSON data and admin email
        if (isCustomGoogleBusinessOAuth && jsonData) {
          authToSave = {
            ...authToSave,
            ...jsonData,
            adminEmail,
          };
        }

        // For SharePoint certificate auth, ensure certificate and private key are included
        if (isSharePointCertificateAuth) {
          const certContent = certificateContent || formData.auth.certificate;
          const keyContent = privateKeyData || formData.auth.privateKey;
          
          if (!certContent || !keyContent) {
            setSaveError('Certificate and private key are required for SharePoint authentication');
            setSaving(false);
            return;
          }

          authToSave = {
            ...authToSave,
            certificate: certContent,
            privateKey: keyContent,
          };
        }

        // Save auth config using new endpoint
        await ConnectorApiService.updateConnectorInstanceAuthConfig(connector._key, authToSave);

        onSuccess?.();
        onClose();
        return;
      }

      // Sync Settings mode, enable mode, or sync only mode: save filters and sync config
      if (syncSettingsMode || enableMode || syncOnly) {
        // Validate sync section only (filters are optional)
        const syncErrors = validateSection(
          'sync',
          connectorConfig.config.sync.customFields,
          formData.sync
        );

        if (Object.keys(syncErrors).length > 0) {
          setFormErrors((prev) => ({ ...prev, sync: syncErrors }));
          setSaving(false);
          return;
        }

        // Prepare filters and sync config using helper functions
        const filtersPayload = prepareFiltersPayload();
        const syncToSave = prepareSyncConfig();

        // Save filters and sync using new endpoint
        await ConnectorApiService.updateConnectorInstanceFiltersSyncConfig(connector._key, {
          filters: filtersPayload,
          sync: syncToSave,
        });

        // If enableMode, toggle connector to enable it
        let connectorWillBeActive = connector.isActive;
        if (enableMode) {
          await ConnectorApiService.toggleConnectorInstance(connector._key, 'sync');
          connectorWillBeActive = true; // After toggling, connector will be active
        }

        // Update scheduling after saving sync config
        // Pass true if connector is/will be active (for enableMode or already active connectors)
        await updateScheduling(connectorWillBeActive);

        // Call onSuccess to refresh connector data and update UI
        onSuccess?.();
        onClose();
        return;
      }

      // Create mode: only validate and save auth (skip filters and sync)
      if (isCreateMode) {
        // Validate instance name
        const trimmedName = instanceName.trim();
        if (!trimmedName) {
          setInstanceNameError('Instance name is required');
          setSaving(false);
          return;
        }
        if (trimmedName.length < MIN_INSTANCE_NAME_LENGTH) {
          setInstanceNameError(
            `Instance name must be at least ${MIN_INSTANCE_NAME_LENGTH} characters`
          );
          setSaving(false);
          return;
        }
        setInstanceNameError(null);

        // Validate auth only
        let authErrors: Record<string, string> = {};
        if (!isNoAuthType) {
          if (isCustomGoogleBusinessOAuth) {
            // Validate Google Business OAuth
            if (!adminEmail || !adminEmail.trim()) {
              setAdminEmailError('Admin email is required');
              authErrors.adminEmail = 'Admin email is required';
            } else if (adminEmailError) {
              authErrors.adminEmail = adminEmailError;
            }
            
            if (!jsonData) {
              setFileError('Service account credentials file is required');
              authErrors.jsonFile = 'Service account credentials file is required';
            } else if (fileError) {
              authErrors.jsonFile = fileError;
            }
          } else if (isSharePointCertificateAuth) {
            // Validate SharePoint certificate authentication
            const hasClientId = formData.auth.clientId && String(formData.auth.clientId).trim() !== '';
            const hasTenantId = formData.auth.tenantId && String(formData.auth.tenantId).trim() !== '';
            const hasSharePointDomain = formData.auth.sharepointDomain && String(formData.auth.sharepointDomain).trim() !== '';
            const hasAdminConsent = formData.auth.hasAdminConsent === true;
            
            if (!hasClientId) authErrors.clientId = 'Application (Client) ID is required';
            if (!hasTenantId) authErrors.tenantId = 'Directory (Tenant) ID is required';
            if (!hasSharePointDomain) authErrors.sharepointDomain = 'SharePoint Domain is required';
            if (!hasAdminConsent) authErrors.hasAdminConsent = 'Admin consent is required';
            
            const hasCertificate = !!(certificateContent || formData.auth.certificate);
            const hasPrivateKey = !!(privateKeyData || formData.auth.privateKey);
            
            if (!hasCertificate) authErrors.certificate = 'Certificate file is required';
            if (!hasPrivateKey) authErrors.privateKey = 'Private key file is required';
            
            if (certificateError) authErrors.certificate = certificateError;
            if (privateKeyError) authErrors.privateKey = privateKeyError;
          } else {
            // Use schema from selected auth type in create mode
            const validationAuthType = selectedAuthType || connectorConfig.config.auth?.type || '';
            const validationAuthSchemas = connectorConfig.config.auth?.schemas || {};
            const validationSchema = validationAuthType && validationAuthSchemas[validationAuthType]
              ? validationAuthSchemas[validationAuthType]
              : connectorConfig.config.auth?.schema || { fields: [] };
            
            authErrors = validateSection(
              'auth',
              validationSchema.fields || [],
              formData.auth
            );
            
            // For OAuth type: Additional validation for creating new OAuth apps
            if (validationAuthType === 'OAUTH' && !formData.auth.oauthConfigId && isAdmin) {
              // If no OAuth config is selected, validate that credential fields are filled
              const oauthFieldNames = (validationSchema.fields || []).map((f: any) => f.name);
              oauthFieldNames.forEach((fieldName: string) => {
                const field = validationSchema.fields?.find((f: any) => f.name === fieldName);
                if (field && field.required) {
                  const value = formData.auth[fieldName];
                  if (!value || (typeof value === 'string' && !value.trim())) {
                    authErrors[fieldName] = `${field.displayName || fieldName} is required`;
                  }
                }
              });
              
              // Validate oauthInstanceName if creating new OAuth app
              if (!formData.auth.oauthConfigId) {
                const instanceNameValue = formData.auth.oauthInstanceName;
                if (!instanceNameValue || (typeof instanceNameValue === 'string' && !instanceNameValue.trim())) {
                  authErrors.oauthInstanceName = 'OAuth App Instance Name is required when creating a new OAuth app';
                }
              }
            }
          }
        }

        if (Object.keys(authErrors).length > 0) {
          setFormErrors((prev) => ({ ...prev, auth: authErrors }));
          setSaving(false);
          return;
        }

        // Prepare auth config
        let authToSave = { ...formData.auth };

        // For OAuth: Ensure oauthInstanceName is included if creating new OAuth app
        // The backend uses this to create a new OAuth config when oauthConfigId is not provided
        // If not provided, backend will use connector instance name as fallback
        if (formData.auth.oauthInstanceName) {
          authToSave.oauthInstanceName = formData.auth.oauthInstanceName;
        }

        // For OAuth: Remove oauthConfigId if it's null/empty to ensure backend creates new OAuth config
        // Only keep it if an existing OAuth config is selected
        if (!authToSave.oauthConfigId) {
          // Explicitly remove this field to ensure backend creates new OAuth config
          delete authToSave.oauthConfigId;
        }

        // For business OAuth, merge JSON data and admin email
        if (isCustomGoogleBusinessOAuth && jsonData) {
          authToSave = {
            ...authToSave,
            ...jsonData,
            adminEmail,
          };
        }

        // For SharePoint certificate auth, ensure certificate and private key are included
        if (isSharePointCertificateAuth) {
          const certContent = certificateContent || formData.auth.certificate;
          const keyContent = privateKeyData || formData.auth.privateKey;
          
          if (!certContent || !keyContent) {
            setSaveError('Certificate and private key are required for SharePoint authentication');
            setSaving(false);
            return;
          }

          authToSave = {
            ...authToSave,
            certificate: certContent,
            privateKey: keyContent,
          };
        }


        const scope = connector.scope || 'personal';
        
        // Compute auth type with fallback (same logic as currentAuthType)
        // Ensures auth type is always sent, even when there's only one supported type
        const authTypeToSend = selectedAuthType || 
          connectorConfig.config.auth?.supportedAuthTypes?.[0] || 
          connectorConfig.config.auth?.type || 
          undefined;

        authToSave.authType = authTypeToSend;
        
        
        const created = await ConnectorApiService.createConnectorInstance(
          connector.type,
          trimmedName,
          scope,
          { auth: authToSave },
          authTypeToSend
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

      // Edit mode: validate all sections and save separately
      let authErrors: Record<string, string> = {};
      if (!isNoAuthType) {
        if (isCustomGoogleBusinessOAuth) {
          // Validate Google Business OAuth
          if (!adminEmail || !adminEmail.trim()) {
            setAdminEmailError('Admin email is required');
            authErrors.adminEmail = 'Admin email is required';
          } else if (adminEmailError) {
            authErrors.adminEmail = adminEmailError;
          }
          
          if (!jsonData) {
            setFileError('Service account credentials file is required');
            authErrors.jsonFile = 'Service account credentials file is required';
          } else if (fileError) {
            authErrors.jsonFile = fileError;
          }
        } else if (isSharePointCertificateAuth) {
          // Validate SharePoint certificate authentication
          const hasClientId = formData.auth.clientId && String(formData.auth.clientId).trim() !== '';
          const hasTenantId = formData.auth.tenantId && String(formData.auth.tenantId).trim() !== '';
          const hasSharePointDomain = formData.auth.sharepointDomain && String(formData.auth.sharepointDomain).trim() !== '';
          const hasAdminConsent = formData.auth.hasAdminConsent === true;
          
          if (!hasClientId) authErrors.clientId = 'Application (Client) ID is required';
          if (!hasTenantId) authErrors.tenantId = 'Directory (Tenant) ID is required';
          if (!hasSharePointDomain) authErrors.sharepointDomain = 'SharePoint Domain is required';
          if (!hasAdminConsent) authErrors.hasAdminConsent = 'Admin consent is required';
          
          const hasCertificate = !!(certificateContent || formData.auth.certificate);
          const hasPrivateKey = !!(privateKeyData || formData.auth.privateKey);
          
          if (!hasCertificate) authErrors.certificate = 'Certificate file is required';
          if (!hasPrivateKey) authErrors.privateKey = 'Private key file is required';
          
          if (certificateError) authErrors.certificate = certificateError;
          if (privateKeyError) authErrors.privateKey = privateKeyError;
        } else {
          // Use schema from existing auth type in edit mode (cannot be changed)
          const editAuthType = connectorConfig.config.auth?.type || '';
          const editAuthSchemas = connectorConfig.config.auth?.schemas || {};
          const editSchema = editAuthType && editAuthSchemas[editAuthType]
            ? editAuthSchemas[editAuthType]
            : connectorConfig.config.auth?.schema || { fields: [] };
          
          authErrors = validateSection(
            'auth',
            editSchema.fields || [],
            formData.auth
          );
          
          // For OAuth type: Additional validation for creating new OAuth apps
          if (editAuthType === 'OAUTH' && !formData.auth.oauthConfigId && isAdmin) {
            // If no OAuth config is selected, validate that credential fields are filled
            const oauthFieldNames = (editSchema.fields || []).map((f: any) => f.name);
            oauthFieldNames.forEach((fieldName: string) => {
              const field = editSchema.fields?.find((f: any) => f.name === fieldName);
              if (field && field.required) {
                const value = formData.auth[fieldName];
                if (!value || (typeof value === 'string' && !value.trim())) {
                  authErrors[fieldName] = `${field.displayName || fieldName} is required`;
                }
              }
            });
            
            // Validate oauthInstanceName if creating new OAuth app
            if (!formData.auth.oauthConfigId) {
              const instanceNameValue = formData.auth.oauthInstanceName;
              if (!instanceNameValue || (typeof instanceNameValue === 'string' && !instanceNameValue.trim())) {
                authErrors.oauthInstanceName = 'OAuth App Instance Name is required when creating a new OAuth app';
              }
            }
          }
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
        setSaving(false);
        return;
      }

      // Save auth config if not NONE auth type
      if (!isNoAuthType) {
        let authToSave = { ...formData.auth };

        // For OAuth: Ensure oauthInstanceName is included if creating new OAuth app
        // The backend uses this to create a new OAuth config when oauthConfigId is not provided
        // If not provided, backend will use connector instance name as fallback
        if (formData.auth.oauthInstanceName) {
          authToSave.oauthInstanceName = formData.auth.oauthInstanceName;
        }

        // For OAuth: Remove oauthConfigId if it's null/empty to ensure backend creates new OAuth config
        // Only keep it if an existing OAuth config is selected
        if (!authToSave.oauthConfigId) {
          // Explicitly remove this field to ensure backend creates new OAuth config
          delete authToSave.oauthConfigId;
        }

        // For business OAuth, merge JSON data and admin email
        if (isCustomGoogleBusinessOAuth && jsonData) {
          authToSave = {
            ...authToSave,
            ...jsonData,
            adminEmail,
          };
        }

        // For SharePoint certificate auth, ensure certificate and private key are included
        if (isSharePointCertificateAuth) {
          const certContent = certificateContent || formData.auth.certificate;
          const keyContent = privateKeyData || formData.auth.privateKey;
          
          if (!certContent || !keyContent) {
            setSaveError('Certificate and private key are required for SharePoint authentication');
            setSaving(false);
            return;
          }

          authToSave = {
            ...authToSave,
            certificate: certContent,
            privateKey: keyContent,
          };
        }

        await ConnectorApiService.updateConnectorInstanceAuthConfig(connector._key, authToSave);
      }

      // Save filters and sync config using helper functions
      const filtersPayload = prepareFiltersPayload();
      const syncToSave = prepareSyncConfig();

      await ConnectorApiService.updateConnectorInstanceFiltersSyncConfig(connector._key, {
        filters: filtersPayload,
        sync: syncToSave,
      });

      // Update scheduling after saving sync config
      // Pass connector.isActive to indicate if connector is currently active
      await updateScheduling(connector.isActive);

      onSuccess?.();
      onClose();
    } catch (error: any) {
      console.error('Error saving connector config:', error);
      
      // Extract more specific error message
      let errorMessage = 'Failed to save configuration. Please try again.';
      if (error?.response?.data?.detail) {
        errorMessage = error.response.data.detail;
      } else if (error?.response?.data?.message) {
        errorMessage = error.response.data.message;
      } else if (error?.message) {
        errorMessage = error.message;
      }
      
      setSaveError(errorMessage);
    } finally {
      setSaving(false);
    }
  }, [
    connectorConfig,
    formData,
    validateSection,
    onClose,
    onSuccess,
    connector,
    adminEmail,
    adminEmailError,
    jsonData,
    fileError,
    isCustomGoogleBusinessOAuth,
    isSharePointCertificateAuth,
    certificateContent,
    certificateError,
    privateKeyData,
    privateKeyError,
    isCreateMode,
    instanceName,
    isBusiness,
    navigate,
    enableMode,
    syncOnly,
    syncSettingsMode,
    authOnly,
    prepareFiltersPayload,
    prepareSyncConfig,
    updateScheduling,
    selectedAuthType,
    isAdmin,
  ]);

  // Admin email change handler
  const handleAdminEmailChange = useCallback(
    (email: string) => {
      setAdminEmail(email);
      validateAdminEmail(email);
    },
    [validateAdminEmail]
  );

  // Auth type change handler (create mode only)
  const handleAuthTypeChange = useCallback(
    (authType: string) => {
      if (!isCreateMode) {
        console.warn('Auth type cannot be changed after connector creation');
        return;
      }
      
      // Update selected auth type immediately (no state batching needed)
      setSelectedAuthType(authType);
      
      // Update form data to use the new auth type's schema
      // Preserve existing values where possible, only set defaults for new fields
      if (connectorConfig) {
        const authSchemas = connectorConfig.config.auth?.schemas || {};
        const newAuthSchema = authSchemas[authType] || { fields: [] };
        const previousAuthData = formData.auth || {};
        
        // Preserve existing values for fields that exist in both schemas
        // Only set defaults for fields that don't have existing values
        const newAuthData: Record<string, any> = { ...previousAuthData };
        newAuthSchema.fields?.forEach((field: any) => {
          // Only set default if field doesn't exist or is undefined
          if (field.defaultValue !== undefined && newAuthData[field.name] === undefined) {
            newAuthData[field.name] = field.defaultValue;
          }
        });
        
        // Remove fields that don't exist in the new schema
        const newFieldNames = new Set(newAuthSchema.fields?.map((f: any) => f.name) || []);
        Object.keys(newAuthData).forEach((key) => {
          if (!newFieldNames.has(key)) {
            delete newAuthData[key];
          }
        });
        
        // Update form data smoothly without full re-initialization
        // Use functional update to ensure we're working with latest state
        setFormData((prev) => ({
          ...prev,
          auth: newAuthData,
        }));
        
        // Clear auth errors for fields that no longer exist
        setFormErrors((prev) => {
          const newAuthErrors = { ...prev.auth };
          Object.keys(newAuthErrors).forEach((key) => {
            if (!newFieldNames.has(key)) {
              delete newAuthErrors[key];
            }
          });
          return {
            ...prev,
            auth: newAuthErrors,
          };
        });
        
        // Re-evaluate conditional display rules for the new auth type
        if (connectorConfig.config.auth?.conditionalDisplay) {
          const displayRules = evaluateConditionalDisplay(
            connectorConfig.config.auth.conditionalDisplay,
            newAuthData
          );
          setConditionalDisplay(displayRules);
        }
      }
    },
    [isCreateMode, connectorConfig, formData.auth]
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

    // Business OAuth state (Google Workspace)
    adminEmail,
    adminEmailError,
    selectedFile,
    fileName,
    fileError,
    jsonData,

    // Create mode state
    isCreateMode,
    instanceName,
    instanceNameError,
    setInstanceName,

    // SharePoint Certificate OAuth state
    certificateFile,
    certificateFileName,
    certificateError,
    certificateData,
    privateKeyFile,
    privateKeyFileName,
    privateKeyError,
    privateKeyData,

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

    // SharePoint Certificate actions
    handleCertificateUpload,
    handleCertificateChange,
    handlePrivateKeyUpload,
    handlePrivateKeyChange,
    certificateInputRef,
    privateKeyInputRef,
    isSharePointCertificateAuthValid,
    
    // Auth type selection
    selectedAuthType,
    handleAuthTypeChange,
  };
};