import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useAccountType } from 'src/hooks/use-account-type';
import { Connector, ConnectorConfig } from '../types/types';
import { ConnectorApiService } from '../services/api';
import { CrawlingManagerApi } from '../services/crawling-manager';
import { buildCronFromSchedule } from '../utils/cron';
import { evaluateConditionalDisplay } from '../utils/conditional-display';
import { isNoneAuthType } from '../utils/auth';

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

  // SharePoint Certificate actions
  handleCertificateUpload: () => void;
  handleCertificateChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  handlePrivateKeyUpload: () => void;
  handlePrivateKeyChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  certificateInputRef: React.RefObject<HTMLInputElement>;
  privateKeyInputRef: React.RefObject<HTMLInputElement>;
  isSharePointCertificateAuthValid: () => boolean;
}

export const useConnectorConfig = ({
  connector,
  onClose,
  onSuccess,
}: UseConnectorConfigProps): UseConnectorConfigReturn => {
  const { isBusiness, isIndividual, loading: accountTypeLoading } = useAccountType();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // SharePoint certificate refs
  const certificateInputRef = useRef<HTMLInputElement>(null);
  const privateKeyInputRef = useRef<HTMLInputElement>(null);

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
  const isCustomGoogleBusinessOAuth = useMemo(
    () =>
      isBusiness &&
      connector.appGroup === 'Google Workspace' &&
      connector.authType === 'OAUTH',
    [isBusiness, connector.appGroup, connector.authType]
  );

  // Memoized helper to check if this is SharePoint certificate auth
  const isSharePointCertificateAuth = useMemo(
    () =>
      connector.name === 'SharePoint Online' &&
      (connector.authType === 'OAUTH_CERTIFICATE' || connector.authType === 'OAUTH_ADMIN_CONSENT'),
    [connector.name, connector.authType]
  );

  // Memoized helper functions
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

  const initializeFormData = useCallback((config: any) => {
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
    
    // Debug logging (can be removed in production)
    if (!isValid) {
      console.log('SharePoint validation failed:', {
        hasClientId,
        hasTenantId,
        hasSharePointDomain,
        hasAdminConsent,
        hasCertificate,
        hasPrivateKey,
        hasErrors,
        certificateError,
        privateKeyError,
      });
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

        // Fetch both config and schema in a single API call
        const { config: configResponse, schema: schemaResponse } =
          await ConnectorApiService.getConnectorConfigAndSchema(connector.name);

        if (!isMounted) return;

        // Merge config with schema
        const mergedConfig = mergeConfigWithSchema(configResponse, schemaResponse);
        setConnectorConfig(mergedConfig);

        // Initialize form data
        const initialFormData = initializeFormData(mergedConfig);

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
          setSaveError('Failed to load connector configuration');
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
  }, [
    connector.name,
    accountTypeLoading,
    isCustomGoogleBusinessOAuth,
    isSharePointCertificateAuth,
    mergeConfigWithSchema,
    initializeFormData,
    getBusinessOAuthData,
    getSharePointCertificateData,
  ]);

  // Recalculate conditional display when auth form data changes
  useEffect(() => {
    if (connectorConfig?.config?.auth?.conditionalDisplay) {
      const displayRules = evaluateConditionalDisplay(
        connectorConfig.config.auth.conditionalDisplay,
        formData.auth
      );
      setConditionalDisplay(displayRules);
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

      // Clear save error when user makes changes
      setSaveError(null);
    },
    [connectorConfig]
  );

  const handleNext = useCallback(() => {
    if (!connectorConfig) return;

    // Clear any previous save error when user tries again
    setSaveError(null);

    let errors: Record<string, string> = {};

    // Validate current step
    switch (activeStep) {
      case 0: // Auth
        if (isCustomGoogleBusinessOAuth) {
          if (!isBusinessGoogleOAuthValid()) {
            errors = { adminEmail: adminEmailError || 'Invalid business credentials' };
          }
        } else if (isSharePointCertificateAuth) {
          // Validate SharePoint certificate authentication
          if (!isSharePointCertificateAuthValid()) {
            setSaveError('Please complete all required SharePoint authentication fields and upload valid certificate and private key files.');
            return;
          }
        } else {
          errors = validateSection(
            'auth',
            connectorConfig.config.auth.schema.fields,
            formData.auth
          );
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
    isCustomGoogleBusinessOAuth,
    isBusinessGoogleOAuthValid,
    adminEmailError,
    isSharePointCertificateAuth,
    isSharePointCertificateAuthValid,
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

      const isNoAuthType = isNoneAuthType(connector.authType);

      // For business OAuth, validate admin email and JSON file
      if (isCustomGoogleBusinessOAuth) {
        if (!isBusinessGoogleOAuthValid()) {
          setSaveError('Please provide valid admin email and JSON credentials file');
          return;
        }
      }

      // For SharePoint certificate auth, validate certificate and key
      if (isSharePointCertificateAuth) {
        if (!isSharePointCertificateAuthValid()) {
          setSaveError('Please provide valid certificate and private key files for SharePoint authentication');
          return;
        }
      }

      // Validate all sections (skip auth validation for 'NONE' authType)
      let authErrors: Record<string, string> = {};
      if (!isNoAuthType) {
        if (isCustomGoogleBusinessOAuth) {
          if (!isBusinessGoogleOAuthValid()) {
            authErrors = { adminEmail: adminEmailError || 'Invalid business credentials' };
          }
        } else if (isSharePointCertificateAuth) {
          if (!isSharePointCertificateAuthValid()) {
            authErrors = { certificate: 'Invalid SharePoint certificate authentication' };
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

      // Prepare config for API
      const syncToSave: any = {
        selectedStrategy: formData.sync.selectedStrategy,
        ...formData.sync,
      };
      const normalizedStrategy = String(formData.sync.selectedStrategy || '').toUpperCase();
      if (normalizedStrategy === 'SCHEDULED') {
        syncToSave.scheduledConfig = formData.sync.scheduledConfig || {};
      } else if (syncToSave.scheduledConfig !== undefined) {
        delete syncToSave.scheduledConfig;
      }

      const configToSave: any = {
        auth: formData.auth,
        sync: syncToSave,
        filters: formData.filters || {},
      };

      // For business OAuth, merge JSON data and admin email into auth config
      if (isCustomGoogleBusinessOAuth && jsonData) {
        configToSave.auth = {
          ...configToSave.auth,
          ...jsonData,
          adminEmail,
        };
      }

      // For SharePoint certificate auth, ensure certificate and private key are included
      if (isSharePointCertificateAuth) {
        const certContent = certificateContent || formData.auth.certificate;
        const keyContent = privateKeyData || formData.auth.privateKey;
        
        if (!certContent || !keyContent) {
          throw new Error('Certificate and private key are required for SharePoint authentication');
        }

        configToSave.auth = {
          ...configToSave.auth,
          certificate: certContent,
          privateKey: keyContent,
        };
      }

      await ConnectorApiService.updateConnectorConfig(connector.name, configToSave as any);

      // Update scheduling if connector is already active
      const wasActive = !!connector.isActive;
      if (wasActive) {
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
          if (strategyChanged) {
            if (prevStrategy === 'SCHEDULED' && newStrategy !== 'SCHEDULED') {
              // Turn off existing schedule
              await CrawlingManagerApi.remove(connector.name.toLowerCase());
            } else if (newStrategy === 'SCHEDULED') {
              // Apply schedule with new settings
              const cron = buildCronFromSchedule({
                startTime: newScheduled.startTime,
                intervalMinutes: newScheduled.intervalMinutes,
                timezone: (newScheduled.timezone || 'UTC').toUpperCase(),
              });
              await CrawlingManagerApi.schedule(connector.name.toLowerCase(), {
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
            }
          } else if (newStrategy === 'SCHEDULED' && scheduleChanged) {
            // Re-apply schedule when only the schedule parameters changed
            const cron = buildCronFromSchedule({
              startTime: newScheduled.startTime,
              intervalMinutes: newScheduled.intervalMinutes,
              timezone: (newScheduled.timezone || 'UTC').toUpperCase(),
            });
            await CrawlingManagerApi.schedule(connector.name.toLowerCase(), {
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
          }
        } catch (scheduleError) {
          console.error('Scheduling update failed:', scheduleError);
          // Do not block saving on scheduling issues
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
    formData,
    validateSection,
    onClose,
    onSuccess,
    connector,
    adminEmail,
    jsonData,
    isBusinessGoogleOAuthValid,
    isCustomGoogleBusinessOAuth,
    adminEmailError,
    isSharePointCertificateAuth,
    isSharePointCertificateAuthValid,
    certificateContent,
    privateKeyData,
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

    // Business OAuth state (Google Workspace)
    adminEmail,
    adminEmailError,
    selectedFile,
    fileName,
    fileError,
    jsonData,

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
  };
};