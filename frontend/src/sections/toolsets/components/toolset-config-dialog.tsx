/**
 * Toolset Configuration Dialog
 *
 * Handles two modes:
 * 1. CREATE mode (no instanceId / toolsetId): Admin creates a new org-wide toolset instance.
 *    - Shown when clicking "Configure Toolset" from the Available tab.
 *    - Shows instanceName field + auth credentials (clientId/secret for OAuth, apiToken etc.)
 *    - Calls POST /instances on save.
 *
 * 2. MANAGE mode (instanceId / toolsetId provided): User authenticates against an existing instance.
 *    - Shown when clicking "Manage" on a My Toolsets card.
 *    - Shows auth-type-specific credential fields (no admin OAuth credentials).
 *    - Calls POST /instances/:id/authenticate or OAuth flow on save.
 *    - ADMIN ONLY: Also shows OAuth config section (clientId, etc.) and allows updating it.
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Alert,
  Snackbar,
  Stack,
  Typography,
  Box,
  Chip,
  CircularProgress,
  Grid,
  alpha,
  useTheme,
  IconButton,
  Paper,
  Skeleton,
  Collapse,
  Tooltip,
  TextField,
  Divider,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import ToolsetApiService, { MyToolset, OAuthConfigSummary } from 'src/services/toolset-api';
import { RegistryToolset } from 'src/types/agent';
import { FieldRenderer } from 'src/sections/accountdetails/connectors/components/field-renderers';

// Icons
import keyIcon from '@iconify-icons/mdi/key';
import lockIcon from '@iconify-icons/mdi/lock';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import closeIcon from '@iconify-icons/mdi/close';
import saveIcon from '@iconify-icons/eva/save-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import infoIcon from '@iconify-icons/eva/info-outline';
import copyIcon from '@iconify-icons/mdi/content-copy';
import checkIcon from '@iconify-icons/mdi/check';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import refreshIcon from '@iconify-icons/mdi/refresh';
import shieldIcon from '@iconify-icons/mdi/shield-account';
import warningIcon from '@iconify-icons/mdi/alert';
import editIcon from '@iconify-icons/mdi/pencil';

interface ToolsetConfigDialogProps {
  /** Registry toolset (CREATE mode) or MyToolset (MANAGE mode) */
  toolset: RegistryToolset | MyToolset | Partial<RegistryToolset>;
  /** Instance ID – if provided, dialog is in MANAGE mode */
  toolsetId?: string;
  /** Whether the current user has admin privileges */
  isAdmin?: boolean;
  onClose: () => void;
  onSuccess: () => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

interface ToolsetSchema {
  toolset?: {
    name?: string;
    displayName?: string;
    description?: string;
    category?: string;
    supportedAuthTypes?: string[];
    config?: {
      auth?: {
        schemas?: Record<string, { fields: any[]; redirectUri?: string; displayRedirectUri?: boolean }>;
        [key: string]: any;
      };
      [key: string]: any;
    };
    tools?: any[];
    oauthConfig?: any;
    [key: string]: any;
  };
  auth?: {
    type?: string;
    supportedAuthTypes?: string[];
    schemas?: Record<string, { fields: any[] }>;
    [key: string]: any;
  };
  [key: string]: any;
}

// ============================================================================
// Note: Auth fields are now dynamically loaded from schema (no hardcoded fields)
// ============================================================================

// ============================================================================
// Admin confirmation dialog (deauthentication warning)
// ============================================================================
interface ConfirmDeauthDialogProps {
  open: boolean;
  userCount: number;
  actionLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const ConfirmDeauthDialog: React.FC<ConfirmDeauthDialogProps> = ({
  open,
  userCount,
  actionLabel,
  onConfirm,
  onCancel,
}) => {
  const theme = useTheme();
  return (
    <Dialog open={open} onClose={onCancel} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1.5, pb: 1.5 }}>
        <Box
          sx={{
            p: 0.75,
            borderRadius: 1,
            bgcolor: alpha(theme.palette.warning.main, 0.12),
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <Iconify icon={warningIcon} width={20} color={theme.palette.warning.main} />
        </Box>
        <Typography variant="h6" sx={{ fontWeight: 600, fontSize: '1rem' }}>
          Confirm OAuth Config Change
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Alert severity="warning" sx={{ borderRadius: 1.5, mb: 2 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            This will deauthenticate {userCount > 0 ? `${userCount} user(s)` : 'all users'} of this instance.
          </Typography>
          <Typography variant="body2">
            All users who have authenticated against this toolset instance will need to re-authenticate.
            This action cannot be undone.
          </Typography>
        </Alert>
        <Typography variant="body2" color="text.secondary">
          Are you sure you want to <strong>{actionLabel}</strong>?
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onCancel} variant="text" sx={{ textTransform: 'none' }}>
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          color="warning"
          sx={{ textTransform: 'none', boxShadow: 'none' }}
        >
          Confirm & Proceed
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ============================================================================
// Main Dialog
// ============================================================================

const ToolsetConfigDialog: React.FC<ToolsetConfigDialogProps> = ({
  toolset,
  toolsetId,
  isAdmin = false,
  onClose,
  onSuccess,
  onShowToast,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Is this an edit/manage operation (instanceId provided)?
  const isManageMode = !!toolsetId;
  const instanceId = toolsetId ?? null;

  // Derive auth type and instance info from props when in MANAGE mode
  const manageToolset = isManageMode ? (toolset as MyToolset) : null;
  const manageAuthType = manageToolset?.authType ?? 'NONE';

  // Schema and configuration state (used in CREATE mode)
  const [toolsetSchema, setToolsetSchema] = useState<ToolsetSchema | null>(null);
  const [selectedAuthType, setSelectedAuthType] = useState<string>(
    isManageMode
      ? manageAuthType
      : (toolset.supportedAuthTypes && toolset.supportedAuthTypes.length > 0)
        ? toolset.supportedAuthTypes[0]
        : 'API_TOKEN'
  );
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [instanceName, setInstanceName] = useState(''); // CREATE mode only

  // UI state
  const [loading, setLoading] = useState(!isManageMode); // MANAGE mode doesn't need initial load
  const [saving, setSaving] = useState(false);
  const [authenticating, setAuthenticating] = useState(false);
  const [reauthenticating, setReauthenticating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(
    isManageMode ? (manageToolset?.isAuthenticated ?? false) : false
  );
  const [configSaved, setConfigSaved] = useState(isManageMode); // In manage mode, instance is always "configured"
  const [saveAttempted, setSaveAttempted] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [createdInstanceId, setCreatedInstanceId] = useState<string | null>(null); // After instance creation

  // ── Admin OAuth config state ──
  const [instanceDetails, setInstanceDetails] = useState<any>(null); // Full instance with oauthConfigDetails
  const [availableOAuthConfigs, setAvailableOAuthConfigs] = useState<OAuthConfigSummary[]>([]);
  const [oauthFormData, setOauthFormData] = useState<Record<string, any>>({}); // admin oauth credential fields
  const [oauthFormErrors, setOauthFormErrors] = useState<Record<string, string>>({});
  const [selectedOAuthConfigId, setSelectedOAuthConfigId] = useState<string | null>(
    manageToolset?.oauthConfigId ?? null
  );
  const [savingOAuth, setSavingOAuth] = useState(false);
  const [oauthSaveAttempted, setOauthSaveAttempted] = useState(false);
  const [authenticatedUserCount, setAuthenticatedUserCount] = useState(0);
  const [confirmDeauth, setConfirmDeauth] = useState<{ open: boolean; action: () => Promise<void>; label: string }>({
    open: false,
    action: async () => {},
    label: '',
  });
  
  // CREATE mode: new OAuth app name for admin
  const [newOAuthAppName, setNewOAuthAppName] = useState('');
  const [selectedCreateOAuthConfigId, setSelectedCreateOAuthConfigId] = useState<string | null>(null);
  
  // Loading states
  const [loadingOAuthConfigs, setLoadingOAuthConfigs] = useState(false);
  
  // Form dirty tracking for OAuth config
  const [oauthFormDirty, setOauthFormDirty] = useState(false);
  const [initialOauthFormData, setInitialOauthFormData] = useState<Record<string, any>>({});
  
  // Tools display state
  const [showAllTools, setShowAllTools] = useState(false);

  // Local toast
  const [localToast, setLocalToast] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info' | 'warning';
  }>({ open: false, message: '', severity: 'success' });

  const showLocalToast = useCallback(
    (message: string, severity: 'success' | 'error' | 'info' | 'warning' = 'success') => {
      setLocalToast({ open: true, message, severity });
    },
    []
  );

  const hideLocalToast = useCallback(() => {
    setLocalToast((prev) => ({ ...prev, open: false }));
  }, []);

  // ============================================================================
  // DATA LOADING
  // ============================================================================

  // Load schema for both CREATE and MANAGE modes
  useEffect(() => {
    const toolsetType = isManageMode 
      ? (manageToolset?.toolsetType ?? '')
      : ((toolset as RegistryToolset).name || toolset.displayName || '');
    
    if (toolsetType) {
      const loadSchema = async () => {
        try {
          if (!isManageMode) setLoading(true);
          const schema = await ToolsetApiService.getToolsetSchema(toolsetType);
          setToolsetSchema(schema);
          
          // Determine initial auth type for CREATE mode
          let initialAuthType = 'API_TOKEN';
          if (!isManageMode) {
            if (toolset.supportedAuthTypes && toolset.supportedAuthTypes.length > 0) {
              initialAuthType = toolset.supportedAuthTypes[0];
            }
            setSelectedAuthType(initialAuthType);
            
            // If OAuth and admin, immediately load OAuth configs
            if (isAdmin && initialAuthType === 'OAUTH') {
              try {
                setLoadingOAuthConfigs(true);
                const { oauthConfigs } = await ToolsetApiService.listToolsetOAuthConfigs(toolsetType);
                setAvailableOAuthConfigs(oauthConfigs);
              } catch (err) {
                console.error('Failed to load OAuth configs during schema load:', err);
              } finally {
                setLoadingOAuthConfigs(false);
              }
            }
          }
        } catch (err: any) {
          console.error('Failed to load toolset schema:', err);
          if (!isManageMode) {
            setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to load toolset configuration');
          }
        } finally {
          if (!isManageMode) setLoading(false);
        }
      };
      loadSchema();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isManageMode]);

  // MANAGE mode: load admin details and OAuth configs
  useEffect(() => {
    if (isManageMode && isAdmin && manageAuthType === 'OAUTH' && instanceId) {
      const loadAdminDetails = async () => {
        try {
          setLoadingOAuthConfigs(true);
          const data = await ToolsetApiService.getToolsetInstance(instanceId);
          setInstanceDetails(data);
          setAuthenticatedUserCount((data as any).authenticatedUserCount ?? 0);
          
          const toolsetType = manageToolset?.toolsetType ?? '';
          if (toolsetType) {
            const { oauthConfigs } = await ToolsetApiService.listToolsetOAuthConfigs(toolsetType);
            setAvailableOAuthConfigs(oauthConfigs);
          }
        } catch (err) {
          console.error('Failed to load admin instance details:', err);
        } finally {
          setLoadingOAuthConfigs(false);
        }
      };
      loadAdminDetails();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isManageMode, isAdmin, manageAuthType, instanceId]);

  // CREATE mode: load OAuth configs when admin selects OAuth auth type
  useEffect(() => {
    if (!isManageMode && isAdmin && selectedAuthType === 'OAUTH' && toolsetSchema) {
      const loadOAuthConfigs = async () => {
        try {
          setLoadingOAuthConfigs(true);
          const toolsetType = (toolset as RegistryToolset).name || '';
          if (toolsetType) {
            const { oauthConfigs } = await ToolsetApiService.listToolsetOAuthConfigs(toolsetType);
            setAvailableOAuthConfigs(oauthConfigs);
          }
        } catch (err) {
          console.error('Failed to load OAuth configs:', err);
        } finally {
          setLoadingOAuthConfigs(false);
        }
      };
      loadOAuthConfigs();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isManageMode, isAdmin, selectedAuthType, toolsetSchema]);

  // ============================================================================
  // AUTH STATUS CHECK
  // ============================================================================

  const checkAuthStatus = useCallback(async () => {
    const idToCheck = instanceId ?? createdInstanceId;
    if (!idToCheck) return;
    try {
      const status = await ToolsetApiService.getInstanceStatus(idToCheck);
      setIsAuthenticated(status.isAuthenticated);
      setConfigSaved(status.isConfigured);
    } catch (err) {
      console.error('Failed to check instance status:', err);
    }
  }, [instanceId, createdInstanceId]);

  // ============================================================================
  // SCHEMA HELPERS (CREATE mode only)
  // ============================================================================

  const currentAuthSchema = useMemo(() => {
    if (!toolsetSchema || isManageMode) {
      return { fields: [], redirectUri: '', displayRedirectUri: false };
    }
    const toolsetData = (toolsetSchema as any).toolset || toolsetSchema;
    const authConfig = toolsetData.config?.auth || toolsetData.auth || {};
    const schemas = authConfig.schemas || {};
    if (!schemas || Object.keys(schemas).length === 0) {
      return { fields: [], redirectUri: '', displayRedirectUri: false };
    }
    let schema = null;
    if (selectedAuthType && schemas[selectedAuthType]) {
      schema = schemas[selectedAuthType];
    } else {
      const firstSchemaKey = Object.keys(schemas)[0];
      schema = firstSchemaKey ? schemas[firstSchemaKey] : { fields: [] };
    }
    return {
      fields: schema.fields || [],
      redirectUri: schema.redirectUri || authConfig.redirectUri || '',
      displayRedirectUri: schema.displayRedirectUri !== undefined
        ? schema.displayRedirectUri
        : authConfig.displayRedirectUri || false,
    };
  }, [toolsetSchema, selectedAuthType, isManageMode]);

  const redirectUriValue = useMemo(() => {
    if (currentAuthSchema.redirectUri) {
      const uri = currentAuthSchema.redirectUri;
      if (uri && !uri.startsWith('http')) {
        return `${window.location.origin}/${uri.replace(/^\//, '')}`;
      }
      return uri;
    }
    return '';
  }, [currentAuthSchema.redirectUri]);

  // Extract OAuth field names and schema dynamically from toolsetSchema (for MANAGE mode)
  const oauthFieldNames = useMemo(() => {
    if (!toolsetSchema || manageAuthType !== 'OAUTH') return [];
    
    const toolsetData = (toolsetSchema as any).toolset || toolsetSchema;
    const authConfig = toolsetData.config?.auth || toolsetData.auth || {};
    const authSchemas = authConfig.schemas || {};
    const oauthSchema = authSchemas.OAUTH || authSchemas.oauth;
    
    if (!oauthSchema || !oauthSchema.fields) return [];
    
    return oauthSchema.fields.map((field: any) => field.name);
  }, [toolsetSchema, manageAuthType]);

  const oauthSchema = useMemo(() => {
    if (!toolsetSchema || manageAuthType !== 'OAUTH') return { fields: [] };
    
    const toolsetData = (toolsetSchema as any).toolset || toolsetSchema;
    const authConfig = toolsetData.config?.auth || toolsetData.auth || {};
    const authSchemas = authConfig.schemas || {};
    
    return authSchemas.OAUTH || authSchemas.oauth || { fields: [] };
  }, [toolsetSchema, manageAuthType]);

  // Extract auth schema for non-OAuth auth types (MANAGE mode)
  const manageAuthSchema = useMemo(() => {
    if (!toolsetSchema || !isManageMode || manageAuthType === 'OAUTH' || manageAuthType === 'NONE') {
      return { fields: [] };
    }
    
    const toolsetData = (toolsetSchema as any).toolset || toolsetSchema;
    const authConfig = toolsetData.config?.auth || toolsetData.auth || {};
    const authSchemas = authConfig.schemas || {};
    
    return authSchemas[manageAuthType] || { fields: [] };
  }, [toolsetSchema, manageAuthType, isManageMode]);

  const [showRedirectUri, setShowRedirectUri] = useState(true);
  const [copied, setCopied] = useState(false);

  const handleCopyRedirectUri = useCallback(() => {
    if (redirectUriValue) {
      navigator.clipboard.writeText(redirectUriValue);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [redirectUriValue]);

  // Dynamically populate OAuth form data when instance details and schema are loaded
  useEffect(() => {
    if (instanceDetails && oauthFieldNames.length > 0 && isAdmin) {
      const oauthConfig = (instanceDetails as any).oauthConfig || (instanceDetails as any).oauthConfigDetails;
      if (oauthConfig) {
        const newFormData: Record<string, any> = {};
        
        oauthFieldNames.forEach((fieldName: string) => {
          const value = oauthConfig[fieldName] || oauthConfig.config?.[fieldName];
          if (value !== null && value !== undefined) {
            // Handle arrays (like scopes) by converting to comma-separated string
            newFormData[fieldName] = Array.isArray(value) ? value.join(',') : value;
          }
        });
        
        setOauthFormData(newFormData);
        setInitialOauthFormData(newFormData); // Track initial state
        setOauthFormDirty(false);
      }
    }
  }, [instanceDetails, oauthFieldNames, isAdmin]);

  // ============================================================================
  // FORM HANDLERS
  // ============================================================================

  const handleFieldChange = useCallback((fieldName: string, value: any) => {
    setFormData((prev) => ({ ...prev, [fieldName]: value }));
    setFormErrors((prev) => {
      const newErrors = { ...prev };
      delete newErrors[fieldName];
      return newErrors;
    });
  }, []);

  const validateForm = useCallback(() => {
    const errors: Record<string, string> = {};
    if (isManageMode) {
      const fields = manageAuthSchema.fields || [];
      fields.forEach((field: any) => {
        const value = formData[field.name];
        if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
          errors[field.name] = `${field.displayName} is required`;
        }
        if (value && field.validation) {
          if (field.validation.minLength && value.length < field.validation.minLength) {
            errors[field.name] = `Minimum ${field.validation.minLength} characters required`;
          }
          if (field.validation.maxLength && value.length > field.validation.maxLength) {
            errors[field.name] = `Maximum ${field.validation.maxLength} characters allowed`;
          }
          if (field.validation.pattern) {
            const regex = new RegExp(field.validation.pattern);
            if (!regex.test(value)) {
              errors[field.name] = field.validation.message || 'Invalid format';
            }
          }
        }
      });
    } else {
      if (!instanceName.trim()) {
        errors.instanceName = 'Instance name is required';
      }
      const fields = currentAuthSchema.fields || [];
      fields.forEach((field: any) => {
        const value = formData[field.name];
        if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
          errors[field.name] = `${field.displayName} is required`;
        }
        if (value && field.validation) {
          if (field.validation.minLength && value.length < field.validation.minLength) {
            errors[field.name] = `Minimum ${field.validation.minLength} characters required`;
          }
          if (field.validation.maxLength && value.length > field.validation.maxLength) {
            errors[field.name] = `Maximum ${field.validation.maxLength} characters allowed`;
          }
          if (field.validation.pattern) {
            const regex = new RegExp(field.validation.pattern);
            if (!regex.test(value)) {
              errors[field.name] = field.validation.message || 'Invalid format';
            }
          }
        }
      });
    }
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  }, [isManageMode, instanceName, currentAuthSchema, manageAuthSchema, formData]);

  // ============================================================================
  // ADMIN: Save OAuth Config
  // ============================================================================

  const executeOAuthUpdate = useCallback(async () => {
    if (!instanceId) return;
    const toolsetType = manageToolset?.toolsetType ?? '';
    const oauthConfigId = selectedOAuthConfigId ?? instanceDetails?.oauthConfigId ?? (instanceDetails as any)?.oauthConfigDetails?._id;

    try {
      setSavingOAuth(true);
      setError(null);
      setSuccess(null);

      // Case 1: Switching to a different oauth config
      if (selectedOAuthConfigId && selectedOAuthConfigId !== (manageToolset?.oauthConfigId ?? instanceDetails?.oauthConfigId)) {
        await ToolsetApiService.updateToolsetInstance(instanceId, { oauthConfigId: selectedOAuthConfigId });
        setSuccess('OAuth configuration switched. All users have been deauthenticated.');
        showLocalToast('OAuth config switched. Users must re-authenticate.', 'info');
        setAuthenticatedUserCount(0);
        onSuccess();
        return;
      }

      // Case 2: Updating credentials of existing oauth config - send all OAuth fields dynamically
      if (!oauthConfigId) {
        setError('OAuth configuration ID not found. Please refresh and try again.');
        return;
      }

      const authConfig: Record<string, any> = {};
      
      // Populate all fields from oauthFormData dynamically
      oauthFieldNames.forEach((fieldName: string) => {
        const value = oauthFormData[fieldName];
        
        // Skip clientSecret if empty (keep existing)
        if (fieldName === 'clientSecret' && (!value || !value.trim())) {
          return;
        }
        
        // Skip redirectUri (computed by backend)
        if (fieldName === 'redirectUri') {
          return;
        }
        
        // Convert comma-separated strings to arrays for scopes
        if (fieldName === 'scopes' && typeof value === 'string') {
          authConfig[fieldName] = value.split(',').map((s: string) => s.trim()).filter(Boolean);
        } else if (value !== null && value !== undefined) {
          authConfig[fieldName] = value;
        }
      });

      const { deauthenticatedUserCount, message } = await ToolsetApiService.updateToolsetOAuthConfig(
        toolsetType,
        oauthConfigId,
        {
          authConfig,
          baseUrl: window.location.origin,
        }
      );

      setSuccess(message || 'OAuth configuration updated successfully.');
      showLocalToast(message || 'OAuth config updated.', deauthenticatedUserCount > 0 ? 'info' : 'success');
      setAuthenticatedUserCount(0);
      if (deauthenticatedUserCount > 0) {
        setIsAuthenticated(false); // Current admin may also be deauthenticated
      }
      // Reset dirty flag and update initial data
      setInitialOauthFormData(oauthFormData);
      setOauthFormDirty(false);
      onSuccess(); // Refresh state
    } catch (err: any) {
      console.error('Failed to save admin OAuth config:', err);
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to update OAuth configuration.');
    } finally {
      setSavingOAuth(false);
      setConfirmDeauth({ open: false, action: async () => {}, label: '' });
    }
  }, [instanceId, instanceDetails, manageToolset, selectedOAuthConfigId, oauthFormData, oauthFieldNames, showLocalToast, onSuccess]);

  const handleSaveOAuthConfig = useCallback(() => {
    setOauthSaveAttempted(true);
    const errors: Record<string, string> = {};
    if (!oauthFormData.clientId?.trim()) errors.clientId = 'Client ID is required';
    setOauthFormErrors(errors);
    if (Object.keys(errors).length > 0) return;

    // Prompt for confirmation if there are authenticated users
    if (authenticatedUserCount > 0) {
      setConfirmDeauth({
        open: true,
        action: executeOAuthUpdate,
        label: 'update the OAuth configuration',
      });
    } else {
      executeOAuthUpdate();
    }
  }, [oauthFormData, authenticatedUserCount, executeOAuthUpdate]);

  const handleSwitchOAuthConfig = useCallback((newConfigId: string) => {
    setSelectedOAuthConfigId(newConfigId);
  }, []);

  const handleConfirmSwitchOAuthConfig = useCallback(() => {
    if (authenticatedUserCount > 0) {
      setConfirmDeauth({
        open: true,
        action: executeOAuthUpdate,
        label: 'switch the OAuth configuration',
      });
    } else {
      executeOAuthUpdate();
    }
  }, [authenticatedUserCount, executeOAuthUpdate]);

  // ============================================================================
  // CREATE MODE: Save (creates instance)
  // ============================================================================

  const handleCreateInstance = async () => {
    try {
      setSaving(true);
      setSaveAttempted(true);
      setError(null);
      setSuccess(null);

      // Validate OAuth app name for admin creating new OAuth app
      if (isAdmin && selectedAuthType === 'OAUTH' && !selectedOAuthConfigId && !newOAuthAppName.trim()) {
        setError('Please enter a name for the new OAuth App');
        return;
      }

      if (!validateForm()) {
        setError('Please fill in all required fields correctly');
        return;
      }

      const toolsetType = (toolset as RegistryToolset).name || '';
      if (!toolsetType) {
        setError('Toolset type is required');
        return;
      }

      const authConfig: Record<string, any> = { ...formData };
      if (selectedAuthType === 'OAUTH' && currentAuthSchema.redirectUri) {
        authConfig.redirectUri = currentAuthSchema.redirectUri;
      }

      const payload: any = {
        instanceName: instanceName.trim(),
        toolsetType,
        authType: selectedAuthType,
        baseUrl: window.location.origin,
        authConfig,
      };

      // OAuth config handling for admin
      if (isAdmin && selectedAuthType === 'OAUTH') {
        if (selectedOAuthConfigId) {
          // Using existing OAuth app
          payload.oauthConfigId = selectedOAuthConfigId;
        } else if (newOAuthAppName.trim()) {
          // Creating new OAuth app with separate name
          payload.oauthInstanceName = newOAuthAppName.trim();
        }
      }

      const newInstance = await ToolsetApiService.createToolsetInstance(payload);

      const newId = newInstance._id;
      setCreatedInstanceId(newId);
      (toolset as any)._id = newId;
      setConfigSaved(true);

      if (selectedAuthType === 'OAUTH') {
        const message = 'Instance created successfully! Go to "My Toolsets" to authenticate.';
        setSuccess(message);
        setIsAuthenticated(false);
        showLocalToast(message, 'success');
        onSuccess(); // Refresh state without closing dialog
      } else {
        const message = 'Toolset instance created successfully!';
        setSuccess(message);
        showLocalToast(message, 'success');
        onSuccess(); // Refresh state without closing dialog
      }
    } catch (err: any) {
      console.error('Failed to create toolset instance:', err);
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to create toolset instance. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  // ============================================================================
  // MANAGE MODE: Authenticate (user provides credentials for non-OAuth)
  // ============================================================================

  const handleAuthenticateCredentials = async () => {
    if (!instanceId) {
      setError('Instance ID is required');
      return;
    }
    try {
      setSaving(true);
      setSaveAttempted(true);
      setError(null);
      setSuccess(null);

      if (!validateForm()) {
        setError('Please fill in all required fields correctly');
        return;
      }

      await ToolsetApiService.authenticateToolsetInstance(instanceId, formData);
      setIsAuthenticated(true);
      setConfigSaved(true);

      const message = 'Authenticated successfully!';
      setSuccess(message);
      showLocalToast(message, 'success');
      onSuccess(); // Refresh state without closing dialog
    } catch (err: any) {
      console.error('Failed to authenticate toolset instance:', err);
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to save credentials. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  // ============================================================================
  // OAUTH FLOW (both modes)
  // ============================================================================

  const handleAuthenticate = async () => {
    const idToUse = instanceId ?? createdInstanceId;
    if (!idToUse) {
      setError('Instance ID is required. Please save configuration first.');
      return;
    }
    if (!isManageMode && !configSaved) {
      setError('Please save configuration first');
      return;
    }
    try {
      setAuthenticating(true);
      setError(null);
      setSuccess(null);

      const response = await ToolsetApiService.getInstanceOAuthAuthorizationUrl(idToUse, window.location.origin);
      if (!response.success || !response.authorizationUrl) throw new Error('Failed to get authorization URL');

      const width = 600;
      const height = 700;
      const left = window.screen.width / 2 - width / 2;
      const top = window.screen.height / 2 - height / 2;
      const popup = window.open(response.authorizationUrl, 'oauth_popup',
        `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`);

      if (!popup) throw new Error('Popup blocked. Please allow popups for this site and try again.');
      popup.focus();

      let pollCount = 0;
      const maxPolls = 300;
      const pollInterval = setInterval(async () => {
        pollCount += 1;
        if (popup.closed) {
          clearInterval(pollInterval);
          setAuthenticating(false);
          await new Promise(resolve => setTimeout(resolve, 500));
          try {
            const status = await ToolsetApiService.getInstanceStatus(idToUse);
            if (status.isAuthenticated) {
              setIsAuthenticated(true);
              setSuccess('Authentication successful!');
              showLocalToast('Authentication successful!', 'success');
              onSuccess(); // Refresh state without closing dialog
            } else {
              setError('Authentication failed or was cancelled');
              showLocalToast('Authentication failed or was cancelled', 'error');
            }
          } catch (err) {
            console.error('Failed to verify auth status:', err);
            setError('Failed to verify authentication status');
          }
          return;
        }
        if (pollCount >= maxPolls) {
          clearInterval(pollInterval);
          if (!popup.closed) popup.close();
          setAuthenticating(false);
          setError('Authentication timeout. Please try again.');
        }
      }, 1000);
    } catch (err: any) {
      console.error('Failed to start OAuth flow:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to start authentication');
      setAuthenticating(false);
    }
  };

  // ============================================================================
  // REAUTHENTICATE
  // ============================================================================

  const handleReauthenticate = async () => {
    const idToUse = instanceId ?? createdInstanceId;
    if (!idToUse) { setError('Instance ID is required'); return; }
    try {
      setReauthenticating(true);
      setError(null);
      setSuccess(null);
      await ToolsetApiService.reauthenticateToolsetInstance(idToUse);
      setIsAuthenticated(false);
      setSuccess('Authentication cleared. Click "Authenticate" to complete the OAuth flow again.');
      showLocalToast('Authentication cleared. Please re-authenticate.', 'info');
    } catch (err: any) {
      console.error('Failed to reauthenticate toolset instance:', err);
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to clear authentication');
    } finally {
      setReauthenticating(false);
    }
  };

  // ============================================================================
  // DELETE / REMOVE CREDENTIALS
  // ============================================================================

  const handleDelete = async () => {
    const idToUse = instanceId ?? createdInstanceId;
    if (!idToUse) { setError('Instance ID is required'); return; }

    const displayNameLabel = isManageMode
      ? (manageToolset?.instanceName || manageToolset?.displayName)
      : (toolset.displayName || (toolset as any).name);

    // Different messages for admin delete vs user credential removal
    const confirmMessage = isManageMode && isAdmin
      ? `Delete toolset instance "${displayNameLabel}"? This will remove it for all users. This action cannot be undone.`
      : isManageMode
        ? `Remove your credentials for "${displayNameLabel}"? You can re-authenticate later.`
        : `Delete instance "${displayNameLabel}"? This action cannot be undone.`;

    if (!window.confirm(confirmMessage)) return;

    try {
      setDeleting(true);
      setError(null);
      setSuccess(null);

      if (isManageMode && !isAdmin) {
        // Non-admin: just remove own credentials
        await ToolsetApiService.removeToolsetCredentials(idToUse);
        setSuccess('Credentials removed successfully');
        showLocalToast('Credentials removed successfully', 'success');
      } else {
        // Admin: delete entire instance
        await ToolsetApiService.deleteToolsetInstance(idToUse);
        setSuccess('Toolset instance deleted successfully');
        showLocalToast('Toolset instance deleted successfully', 'success');
        // For delete, actually close the dialog
        setTimeout(() => { onClose(); }, 1000);
        return;
      }
      onSuccess(); // Refresh state without closing dialog
    } catch (err: any) {
      console.error('Failed to delete/remove toolset:', err);
      const errorMsg = err.response?.data?.detail || err.response?.data?.message || 'Failed to delete';
      setError(errorMsg);
      showLocalToast(errorMsg, 'error');
    } finally {
      setDeleting(false);
    }
  };

  // ============================================================================
  // COMPUTED VALUES
  // ============================================================================

  const isOAuth = selectedAuthType === 'OAUTH';
  const isAnyActionInProgress = saving || authenticating || deleting || reauthenticating || savingOAuth;

  const displayName = isManageMode
    ? (manageToolset?.displayName || manageToolset?.instanceName || '')
    : (toolset.displayName || (toolset as any).name || 'Toolset');

  const instanceNameDisplay = isManageMode ? (manageToolset?.instanceName || '') : '';
  const iconPath = isManageMode
    ? (manageToolset?.iconPath || '/assets/icons/toolsets/default.svg')
    : (toolset.iconPath || '/assets/icons/toolsets/default.svg');
  const category = isManageMode ? (manageToolset?.category || 'app') : (toolset.category || 'app');
  const tools = toolset.tools || [];
  const toolCount = toolset.toolCount || tools.length || 0;

  // Admin: is the selected oauth config the same as the current one?
  const currentOAuthConfigId = manageToolset?.oauthConfigId ?? (instanceDetails as any)?.oauthConfigId;
  const isSwitchingOAuthConfig = selectedOAuthConfigId !== null && selectedOAuthConfigId !== currentOAuthConfigId;

  // ============================================================================
  // LOADING STATE
  // ============================================================================

  if (loading) {
    return (
      <Dialog open onClose={onClose} maxWidth="md" fullWidth
        PaperProps={{ sx: { borderRadius: 2.5, boxShadow: isDark ? '0 24px 48px rgba(0,0,0,0.4)' : '0 20px 60px rgba(0,0,0,0.12)' } }}
      >
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', px: 3, py: 2.5, borderBottom: `1px solid ${theme.palette.divider}` }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, width: '100%' }}>
            <Skeleton variant="rectangular" width={48} height={48} sx={{ borderRadius: 1.5 }} />
            <Box sx={{ flex: 1 }}>
              <Skeleton variant="text" width="60%" height={32} sx={{ mb: 1 }} />
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Skeleton variant="rectangular" width={60} height={20} sx={{ borderRadius: 0.5 }} />
                <Skeleton variant="rectangular" width={80} height={20} sx={{ borderRadius: 0.5 }} />
              </Box>
            </Box>
          </Box>
        </DialogTitle>
        <DialogContent sx={{ px: 3, py: 3 }}>
          <Stack spacing={3}>
            <Skeleton variant="rectangular" height={60} sx={{ borderRadius: 1.5 }} />
            <Skeleton variant="rectangular" height={200} sx={{ borderRadius: 1.25 }} />
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 2.5, borderTop: `1px solid ${theme.palette.divider}` }}>
          <Skeleton variant="rectangular" width={80} height={36} sx={{ borderRadius: 1 }} />
          <Skeleton variant="rectangular" width={150} height={36} sx={{ borderRadius: 1 }} />
        </DialogActions>
      </Dialog>
    );
  }

  return (
    <>
      <Dialog
        open
        onClose={onClose}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2.5,
            boxShadow: isDark
              ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
              : '0 20px 60px rgba(0, 0, 0, 0.12)',
          },
        }}
      >
        {/* ================================================================ */}
        {/* DIALOG TITLE                                                      */}
        {/* ================================================================ */}
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 3,
            py: 2.5,
            borderBottom: isDark
              ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
              : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Box
              sx={{
                p: 1.25,
                borderRadius: 1.5,
                backgroundColor: isDark
                  ? alpha(theme.palette.common.white, 0.9)
                  : alpha(theme.palette.grey[100], 0.8),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: isDark ? `1px solid ${alpha(theme.palette.common.white, 0.1)}` : 'none',
              }}
            >
              <img
                src={iconPath}
                alt={displayName}
                width={32}
                height={32}
                style={{ objectFit: 'contain' }}
                onError={(e: React.SyntheticEvent<HTMLImageElement>) => {
                  (e.target as HTMLImageElement).src = '/assets/icons/toolsets/default.svg';
                }}
              />
            </Box>
            <Box>
              <Typography
                variant="h6"
                sx={{ fontWeight: 600, mb: 0.5, color: theme.palette.text.primary, fontSize: '1.125rem', letterSpacing: '-0.01em' }}
              >
                {isManageMode
                  ? `${displayName}${instanceNameDisplay ? ` — ${instanceNameDisplay}` : ''}`
                  : `Configure ${displayName}`}
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Chip
                  label={category}
                  size="small"
                  variant="outlined"
                  sx={{ fontSize: '0.6875rem', height: 20, fontWeight: 500 }}
                />
                {isManageMode ? (
                  <Chip
                    label={manageAuthType.split('_').join(' ')}
                    size="small"
                    variant="outlined"
                    sx={{ fontSize: '0.6875rem', height: 20, fontWeight: 500 }}
                  />
                ) : (
                  selectedAuthType && (
                    <Chip
                      label={selectedAuthType.split('_').join(' ')}
                      size="small"
                      variant="outlined"
                      sx={{ fontSize: '0.6875rem', height: 20, fontWeight: 500 }}
                    />
                  )
                )}
                {isAdmin && isManageMode && (
                  <Chip
                    icon={<Iconify icon={shieldIcon} width={12} />}
                    label="Admin View"
                    size="small"
                    color="primary"
                    sx={{ fontSize: '0.6875rem', height: 20, fontWeight: 500 }}
                  />
                )}
              </Box>
            </Box>
          </Box>

          <IconButton
            onClick={onClose}
            size="small"
            sx={{
              color: theme.palette.text.secondary,
              p: 1,
              '&:hover': { backgroundColor: alpha(theme.palette.text.secondary, 0.08) },
            }}
          >
            <Iconify icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        {/* ================================================================ */}
        {/* DIALOG CONTENT                                                    */}
        {/* ================================================================ */}
        <DialogContent sx={{ px: 3, py: 3 }}>
          <Stack spacing={3}>
            {/* Alerts */}
            {error && (
              <Alert severity="error" onClose={() => setError(null)} sx={{ borderRadius: 1.5 }}>
                {error}
              </Alert>
            )}
            {success && (
              <Alert severity="success" onClose={() => setSuccess(null)} sx={{ borderRadius: 1.5 }}>
                {success}
              </Alert>
            )}
            {isAuthenticated && !success && (
              <Alert severity="success" icon={<Iconify icon={checkCircleIcon} />} sx={{ borderRadius: 1.5 }}>
                {isManageMode
                  ? 'You are authenticated and ready to use this toolset.'
                  : 'This toolset instance is authenticated and ready to use.'}
              </Alert>
            )}

            {/* Description */}
            {toolset.description && (
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem', lineHeight: 1.6 }}>
                {toolset.description}
              </Typography>
            )}

            {/* ============================================================ */}
            {/* CREATE MODE CONTENT                                           */}
            {/* ============================================================ */}
            {!isManageMode && (
              <>
                {/* Auth Type Selector */}
                {toolset.supportedAuthTypes && toolset.supportedAuthTypes.length > 1 && (
                  <FormControl fullWidth>
                    <InputLabel>Authentication Type</InputLabel>
                    <Select
                      value={selectedAuthType}
                      onChange={(e) => {
                        setSelectedAuthType(e.target.value);
                        setFormData({});
                        setFormErrors({});
                        setSaveAttempted(false);
                      }}
                      label="Authentication Type"
                      sx={{ borderRadius: 1.25 }}
                    >
                      {(toolset.supportedAuthTypes || []).map((type) => (
                        <MenuItem key={type} value={type}>
                          {type.split('_').map((w: string) =>
                            w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()
                          ).join(' ')}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                )}

                {/* Redirect URI for OAuth in CREATE mode */}
                {isOAuth && redirectUriValue && currentAuthSchema.displayRedirectUri && (
                  <Paper
                    variant="outlined"
                    sx={{
                      borderRadius: 1.25,
                      overflow: 'hidden',
                      bgcolor: isDark ? alpha(theme.palette.primary.main, 0.08) : alpha(theme.palette.primary.main, 0.03),
                      borderColor: isDark ? alpha(theme.palette.primary.main, 0.25) : alpha(theme.palette.primary.main, 0.15),
                    }}
                  >
                    <Box
                      onClick={() => setShowRedirectUri(!showRedirectUri)}
                      sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 1.5, cursor: 'pointer' }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                        <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.primary.main, 0.12), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Iconify icon={infoIcon} width={16} color={theme.palette.primary.main} />
                        </Box>
                        <Typography variant="subtitle2" sx={{ fontSize: '0.875rem', fontWeight: 600, color: theme.palette.primary.main }}>
                          Redirect URI
                        </Typography>
                      </Box>
                      <Iconify icon={chevronDownIcon} width={20} color={theme.palette.text.secondary}
                        sx={{ transform: showRedirectUri ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                      />
                    </Box>
                    <Collapse in={showRedirectUri}>
                      <Box sx={{ px: 1.5, pb: 1.5 }}>
                        <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem', mb: 1.25 }}>
                          Use this URL when configuring your {toolset.displayName || (toolset as any).name} OAuth2 App.
                        </Typography>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, p: 1.25, borderRadius: 1, bgcolor: isDark ? alpha(theme.palette.grey[900], 0.4) : alpha(theme.palette.grey[100], 0.8), border: `1.5px solid ${alpha(theme.palette.primary.main, isDark ? 0.25 : 0.15)}` }}>
                          <Typography variant="body2" sx={{ flex: 1, fontFamily: 'monospace', fontSize: '0.8125rem', wordBreak: 'break-all', color: isDark ? theme.palette.primary.light : theme.palette.primary.dark, userSelect: 'all' }}>
                            {redirectUriValue}
                          </Typography>
                          <Tooltip title={copied ? 'Copied!' : 'Copy to clipboard'} arrow>
                            <IconButton size="small" onClick={handleCopyRedirectUri} sx={{ p: 0.75, bgcolor: alpha(theme.palette.primary.main, 0.1) }}>
                              <Iconify icon={copied ? checkIcon : copyIcon} width={16} color={theme.palette.primary.main} />
                            </IconButton>
                          </Tooltip>
                        </Box>
                      </Box>
                    </Collapse>
                  </Paper>
                )}

                {/* Unified Configuration Form */}
                <Paper
                  variant="outlined"
                  sx={{ p: 2.5, borderRadius: 1.5, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
                >
                  <Stack spacing={2}>
                    {/* Header */}
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 0.5 }}>
                      <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <Iconify icon="mdi:cog" width={16} sx={{ color: theme.palette.primary.main }} />
                      </Box>
                      <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                        Configuration
                      </Typography>
                    </Box>

                    {/* Instance Name - Always required */}
                    <TextField
                      label="Toolset Instance Name"
                      value={instanceName}
                      onChange={(e) => {
                        setInstanceName(e.target.value);
                        if (formErrors.instanceName) {
                          setFormErrors((prev) => { const n = { ...prev }; delete n.instanceName; return n; });
                        }
                      }}
                      required
                      fullWidth
                      size="small"
                      error={saveAttempted && !!formErrors.instanceName}
                      helperText={saveAttempted && formErrors.instanceName}
                      placeholder={`e.g., ${toolset.displayName || 'My Toolset'} - Production`}
                      sx={{ '& .MuiOutlinedInput-root': { borderRadius: 1.25 } }}
                    />

                    {/* OAuth App Selector (Admin in CREATE mode for OAuth) */}
                    {isOAuth && isAdmin && (
                      <>
                        {loadingOAuthConfigs ? (
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, p: 2, bgcolor: alpha(theme.palette.primary.main, 0.04), borderRadius: 1.25 }}>
                            <CircularProgress size={16} />
                            <Typography variant="body2" color="text.secondary">Loading OAuth apps...</Typography>
                          </Box>
                        ) : (
                          <>
                            <FormControl fullWidth size="small">
                              <InputLabel>OAuth App</InputLabel>
                              <Select
                                value={selectedOAuthConfigId || '__new__'}
                                onChange={(e) => {
                                  const val = e.target.value;
                                  if (val === '__new__') {
                                    setSelectedOAuthConfigId(null);
                                    setNewOAuthAppName('');
                                    setFormData({});
                                  } else {
                                    setSelectedOAuthConfigId(val);
                                    // Load selected OAuth config data into formData
                                    const selectedConfig = availableOAuthConfigs.find(cfg => cfg._id === val);
                                    if (selectedConfig) {
                                      const newFormData: Record<string, any> = {};
                                      // Populate all fields from the selected config
                                      Object.keys(selectedConfig).forEach(key => {
                                        if (key !== '_id' && key !== 'oauthInstanceName' && key !== 'orgId' && key !== 'userId' && key !== 'toolsetType' && key !== 'createdAtTimestamp' && key !== 'updatedAtTimestamp' && key !== 'clientSecretSet') {
                                          const value = (selectedConfig as any)[key];
                                          if (Array.isArray(value)) {
                                            newFormData[key] = value.join(',');
                                          } else if (value !== null && value !== undefined) {
                                            newFormData[key] = value;
                                          }
                                        }
                                      });
                                      setFormData(newFormData);
                                      setNewOAuthAppName('');
                                    }
                                  }
                                }}
                                label="OAuth App"
                                sx={{ borderRadius: 1.25 }}
                              >
                                <MenuItem value="__new__">
                                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                    <Iconify icon="mdi:plus-circle" width={16} />
                                    <span>+ Create New OAuth App</span>
                                  </Box>
                                </MenuItem>
                                {availableOAuthConfigs.map((cfg) => (
                                  <MenuItem key={cfg._id} value={cfg._id}>
                                    {cfg.oauthInstanceName}
                                  </MenuItem>
                                ))}
                              </Select>
                            </FormControl>
                          </>
                        )}

                        {/* OAuth Instance Name (Admin creating new OAuth app) */}
                        {!selectedOAuthConfigId && !loadingOAuthConfigs && (
                          <TextField
                            label="OAuth App Name"
                            value={newOAuthAppName}
                            onChange={(e) => setNewOAuthAppName(e.target.value)}
                            placeholder="e.g., Company OAuth App"
                            size="small"
                            fullWidth
                            required
                            sx={{ '& .MuiOutlinedInput-root': { borderRadius: 1.25 } }}
                          />
                        )}
                      </>
                    )}

                    {/* Credential Fields (dynamic from schema) - No divider, seamless flow */}
                    {currentAuthSchema.fields && currentAuthSchema.fields.length > 0 && (
                      <Grid container spacing={2}>
                        {currentAuthSchema.fields.map((field: any) => {
                          // Skip redirectUri - handled separately
                          if (field.name === 'redirectUri') return null;

                          return (
                            <Grid item xs={12} key={field.name}>
                              <FieldRenderer
                                field={{
                                  ...field,
                                  // For admin with selected OAuth app, make credentials optional (for updates)
                                  required: (isAdmin && selectedOAuthConfigId && (field.name === 'clientSecret' || field.name === 'clientId')) 
                                    ? false 
                                    : field.required,
                                  placeholder: (isAdmin && selectedOAuthConfigId && field.name === 'clientSecret') 
                                    ? 'Leave empty to keep existing' 
                                    : field.placeholder,
                                }}
                                value={formData[field.name] ?? field.defaultValue ?? ''}
                                onChange={(value) => handleFieldChange(field.name, value)}
                                error={saveAttempted ? formErrors[field.name] : undefined}
                              />
                            </Grid>
                          );
                        })}
                      </Grid>
                    )}

                    {/* No credentials message */}
                    {currentAuthSchema.fields && currentAuthSchema.fields.length === 0 && (
                      <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                        No credentials required for this authentication type.
                      </Alert>
                    )}

                    {/* Consolidated Helper Text at Bottom */}
                    <Box sx={{ mt: 2, pt: 2, borderTop: `1px dashed ${alpha(theme.palette.divider, 0.3)}` }}>
                      <Stack spacing={1}>
                        {/* Instance name help */}
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, fontSize: '0.75rem' }}>
                          <Iconify icon="mdi:information-outline" width={14} sx={{ mt: 0.125, flexShrink: 0 }} />
                          <span><strong>Toolset Instance Name:</strong> A unique name to identify this toolset configuration in your organization.</span>
                        </Typography>

                        {/* OAuth app help for admin */}
                        {isOAuth && isAdmin && (
                          <>
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, fontSize: '0.75rem' }}>
                              <Iconify icon="mdi:information-outline" width={14} sx={{ mt: 0.125, flexShrink: 0 }} />
                              <span>
                                <strong>OAuth App:</strong> {selectedOAuthConfigId 
                                  ? 'Using an existing OAuth app. Credentials are pre-filled and can be updated if needed.'
                                  : !loadingOAuthConfigs && availableOAuthConfigs.length > 0
                                    ? 'Select an existing OAuth app to reuse credentials, or create a new one.'
                                    : 'Create a new OAuth app that can be shared across multiple toolset instances.'}
                              </span>
                            </Typography>
                            {!selectedOAuthConfigId && !loadingOAuthConfigs && (
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, fontSize: '0.75rem' }}>
                                <Iconify icon="mdi:lightbulb-on-outline" width={14} sx={{ mt: 0.125, flexShrink: 0, color: theme.palette.warning.main }} />
                                <span><strong>Tip:</strong> The OAuth App Name is separate from the Instance Name and can be reused across multiple instances.</span>
                              </Typography>
                            )}
                          </>
                        )}

                        {/* Client Secret help for existing OAuth app */}
                        {isOAuth && isAdmin && selectedOAuthConfigId && !loadingOAuthConfigs && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, fontSize: '0.75rem' }}>
                            <Iconify icon="mdi:shield-lock-outline" width={14} sx={{ mt: 0.125, flexShrink: 0, color: theme.palette.success.main }} />
                            <span><strong>Client Secret:</strong> Leave empty to keep the existing secret, or enter a new one to update it.</span>
                          </Typography>
                        )}

                        {/* Post-creation flow for OAuth */}
                        {isOAuth && !isAuthenticated && !configSaved && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5, fontSize: '0.75rem' }}>
                            <Iconify icon="mdi:arrow-right-circle-outline" width={14} sx={{ mt: 0.125, flexShrink: 0, color: theme.palette.info.main }} />
                            <span>
                              {isAdmin 
                                ? 'After creating the instance, you can authenticate from the "My Toolsets" page or test the OAuth connection using the "Connect" button below.'
                                : 'After creating the instance, go to "My Toolsets" page to authenticate and connect your account.'}
                            </span>
                          </Typography>
                        )}
                      </Stack>
                    </Box>
                  </Stack>
                </Paper>
              </>
            )}

            {/* ============================================================ */}
            {/* MANAGE MODE CONTENT                                           */}
            {/* ============================================================ */}
            {isManageMode && (
              <>
                {/* ── ADMIN: Unified OAuth Management Section ── */}
                {isAdmin && manageAuthType === 'OAUTH' && (
                  <Paper
                    variant="outlined"
                    sx={{
                      borderRadius: 1.5,
                      overflow: 'hidden',
                      bgcolor: isDark ? alpha(theme.palette.primary.main, 0.05) : alpha(theme.palette.primary.main, 0.02),
                      borderColor: alpha(theme.palette.primary.main, isDark ? 0.2 : 0.15),
                    }}
                  >
                    {/* Header */}
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        px: 2,
                        py: 1.5,
                        borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                        <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.primary.main, 0.12), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Iconify icon={shieldIcon} width={16} color={theme.palette.primary.main} />
                        </Box>
                        <Box>
                          <Typography variant="subtitle2" sx={{ fontWeight: 600, color: theme.palette.primary.main, fontSize: '0.875rem' }}>
                            {loadingOAuthConfigs ? (
                              <Skeleton width={150} />
                            ) : (
                              `OAuth App: ${instanceDetails?.oauthConfig?.oauthInstanceName || 'Not configured'}`
                            )}
                          </Typography>
                          <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                            {loadingOAuthConfigs ? (
                              <Skeleton width={200} />
                            ) : (
                              `Admin Configuration • ${authenticatedUserCount} user(s) authenticated`
                            )}
                          </Typography>
                        </Box>
                      </Box>
                      <Chip
                        label="Admin"
                        size="small"
                        color="primary"
                        sx={{ fontSize: '0.6875rem', height: 18, fontWeight: 600 }}
                      />
                    </Box>

                    {/* Content - Always visible */}
                    <Box sx={{ p: 2 }}>
                      {loadingOAuthConfigs ? (
                        <Stack spacing={2}>
                          <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1.25 }} />
                          <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1.25 }} />
                          <Skeleton variant="rectangular" height={40} sx={{ borderRadius: 1.25 }} />
                        </Stack>
                      ) : (
                        <Stack spacing={2.5}>
                          {/* Warning about impact */}
                          {authenticatedUserCount > 0 && (
                            <Alert severity="warning" icon={<Iconify icon={warningIcon} />} sx={{ borderRadius: 1.25 }}>
                              <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.25 }}>
                                Impact: {authenticatedUserCount} user(s) will be deauthenticated
                              </Typography>
                              <Typography variant="body2">
                                Changing the OAuth configuration will require all users to re-authenticate.
                              </Typography>
                            </Alert>
                          )}

                          {/* OAuth Config Picker (switch to existing config) */}
                          {availableOAuthConfigs.length > 1 && (
                            <Box>
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1, fontWeight: 600 }}>
                                Switch OAuth App
                              </Typography>
                              <FormControl fullWidth size="small">
                                <InputLabel>Select OAuth App</InputLabel>
                                <Select
                                  value={selectedOAuthConfigId ?? currentOAuthConfigId ?? ''}
                                  onChange={(e) => handleSwitchOAuthConfig(e.target.value)}
                                  label="Select OAuth App"
                                  sx={{ borderRadius: 1.25 }}
                                >
                                  {availableOAuthConfigs.map((cfg) => (
                                    <MenuItem key={cfg._id} value={cfg._id}>
                                      {cfg.oauthInstanceName}
                                      {cfg._id === currentOAuthConfigId && (
                                        <Chip label="Current" size="small" sx={{ ml: 1, fontSize: '0.6875rem', height: 16 }} />
                                      )}
                                    </MenuItem>
                                  ))}
                                </Select>
                              </FormControl>
                            </Box>
                          )}

                          {/* Update OAuth Credentials Form - Dynamic Fields from Schema */}
                          <Box>
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1, fontWeight: 600 }}>
                              Update OAuth Credentials
                            </Typography>
                            {oauthSchema.fields && oauthSchema.fields.length > 0 ? (
                              <Stack spacing={1.5}>
                                {oauthSchema.fields.map((field: any) => {
                                  // Skip redirectUri - it's computed by backend
                                  if (field.name === 'redirectUri') return null;
                                  
                                  // For scopes array field, convert to comma-separated string
                                  const fieldValue = field.name === 'scopes' 
                                    ? (Array.isArray(oauthFormData[field.name]) 
                                        ? oauthFormData[field.name].join(',') 
                                        : oauthFormData[field.name] || '')
                                    : (oauthFormData[field.name] ?? '');
                                  
                                  return (
                                    <FieldRenderer
                                      key={field.name}
                                      field={{
                                        ...field,
                                        // For clientSecret, make it optional with helper text
                                        required: field.name === 'clientSecret' ? false : field.required,
                                        placeholder: field.name === 'clientSecret' 
                                          ? 'Leave empty to keep existing secret' 
                                          : field.placeholder,
                                        description: field.name === 'clientSecret'
                                          ? 'Only enter if updating the secret'
                                          : field.description,
                                      }}
                                      value={fieldValue}
                                      onChange={(value) => {
                                        setOauthFormData(prev => {
                                          const newData: Record<string, any> = { ...prev, [field.name]: value };
                                          // Check if form is dirty
                                          const isDirty = Object.keys(newData).some(key => 
                                            newData[key] !== (initialOauthFormData as Record<string, any>)[key]
                                          );
                                          setOauthFormDirty(isDirty);
                                          return newData;
                                        });
                                        // Clear error for this field
                                        if (oauthFormErrors[field.name]) {
                                          setOauthFormErrors(prev => {
                                            const newErrors = { ...prev };
                                            delete newErrors[field.name];
                                            return newErrors;
                                          });
                                        }
                                      }}
                                      error={oauthSaveAttempted ? oauthFormErrors[field.name] : undefined}
                                    />
                                  );
                                })}
                              </Stack>
                            ) : (
                              <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                                Loading OAuth configuration fields...
                              </Alert>
                            )}
                          </Box>
                        </Stack>
                      )}
                    </Box>
                  </Paper>
                )}

                {/* OAuth: Non-admin authentication section */}
                {!isAdmin && manageAuthType === 'OAUTH' && (
                  <Paper
                    variant="outlined"
                    sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
                  >
                    <Stack spacing={2}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                        <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Iconify icon={lockIcon} width={16} color={theme.palette.primary.main} />
                        </Box>
                        <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                          OAuth Authentication
                        </Typography>
                      </Box>
                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                        OAuth App: {instanceDetails?.oauthConfig?.oauthInstanceName || manageToolset?.oauthConfigId || 'Not configured'}
                      </Typography>
                      {isAuthenticated ? (
                        <Alert severity="success" sx={{ borderRadius: 1.25 }}>
                          You are authenticated. Use &quot;Reauthenticate&quot; to start a new OAuth flow, or &quot;Remove Credentials&quot; to disconnect.
                        </Alert>
                      ) : (
                        <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                          Click &quot;Authenticate&quot; to connect your account via OAuth. You will be redirected to the provider to grant access.
                        </Alert>
                      )}
                    </Stack>
                  </Paper>
                )}

                {/* Non-OAuth: show credential fields */}
                {manageAuthType !== 'OAUTH' && manageAuthType !== 'NONE' && (
                  <Paper
                    variant="outlined"
                    sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
                  >
                    <Stack spacing={2}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                        <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.text.primary, 0.05), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Iconify icon={keyIcon} width={16} sx={{ color: theme.palette.text.primary }} />
                        </Box>
                        <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                          Your Credentials
                        </Typography>
                      </Box>

                      {manageAuthSchema.fields && manageAuthSchema.fields.length > 0 ? (
                        <Grid container spacing={2}>
                          {manageAuthSchema.fields.map((field: any) => (
                            <Grid item xs={12} key={field.name}>
                              <FieldRenderer
                                field={field}
                                value={formData[field.name] ?? ''}
                                onChange={(value) => handleFieldChange(field.name, value)}
                                error={saveAttempted ? formErrors[field.name] : undefined}
                              />
                            </Grid>
                          ))}
                        </Grid>
                      ) : (
                        <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                          No credentials required for this authentication type.
                        </Alert>
                      )}

                      {isAuthenticated && (
                        <Alert severity="success" sx={{ borderRadius: 1.25 }}>
                          You are authenticated. Enter new credentials and click &quot;Save Credentials&quot; to update.
                        </Alert>
                      )}
                    </Stack>
                  </Paper>
                )}

                {manageAuthType === 'NONE' && (
                  <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                    This toolset does not require authentication.
                  </Alert>
                )}
              </>
            )}

            {/* Tool Preview */}
            {toolCount > 0 && (
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                    Available Tools ({toolCount})
                  </Typography>
                  {tools.length > 5 && (
                    <Button
                      size="small"
                      onClick={() => setShowAllTools(!showAllTools)}
                      sx={{
                        textTransform: 'none',
                        fontSize: '0.75rem',
                        minWidth: 'auto',
                        px: 1,
                        py: 0.5,
                        color: theme.palette.primary.main,
                      }}
                    >
                      {showAllTools ? 'Show less' : `Show all (${tools.length})`}
                    </Button>
                  )}
                </Box>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {tools.length > 0 ? (
                    <>
                      {(showAllTools ? tools : tools.slice(0, 5)).map((tool: any) => (
                        <Chip
                          key={tool.fullName || tool.name}
                          label={tool.name}
                          size="small"
                          variant="outlined"
                          sx={{ borderRadius: 1, fontSize: '0.8125rem', height: 26 }}
                        />
                      ))}
                    </>
                  ) : (
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                      {toolCount} tools available
                    </Typography>
                  )}
                </Stack>
              </Box>
            )}
          </Stack>
        </DialogContent>

        {/* ================================================================ */}
        {/* DIALOG ACTIONS                                                    */}
        {/* ================================================================ */}
        <DialogActions
          sx={{
            px: 3,
            py: 2.5,
            borderTop: isDark
              ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
              : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
            flexDirection: 'row',
            justifyContent: 'space-between',
          }}
        >
          {/* Left: Destructive actions */}
          <Box>
            {(instanceId || createdInstanceId) && (
              <Button
                onClick={handleDelete}
                disabled={isAnyActionInProgress}
                variant="text"
                color="error"
                startIcon={
                  deleting ? (
                    <CircularProgress size={14} color="error" />
                  ) : (
                    <Iconify icon={deleteIcon} width={16} height={16} />
                  )
                }
                sx={{ textTransform: 'none', borderRadius: 1, px: 2, '&:hover': { backgroundColor: alpha(theme.palette.error.main, 0.08) } }}
              >
                {deleting
                  ? 'Deleting...'
                  : isManageMode && isAdmin
                    ? 'Delete Instance'
                    : isManageMode
                      ? 'Remove Credentials'
                      : 'Delete Instance'}
              </Button>
            )}
          </Box>

          {/* Right: Primary actions */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Button
              onClick={onClose}
              disabled={isAnyActionInProgress}
              variant="text"
              sx={{ textTransform: 'none', borderRadius: 1, px: 2, color: theme.palette.text.secondary }}
            >
              {isAuthenticated ? 'Close' : 'Cancel'}
            </Button>

            <Box sx={{ width: '1px', height: 20, bgcolor: alpha(theme.palette.divider, 0.4), mx: 0.5 }} />

            {isManageMode ? (
              /* ── MANAGE MODE ACTIONS ── */
              <>
                {/* ADMIN OAUTH: Show Connect, Disconnect, Update/Save buttons */}
                {isAdmin && manageAuthType === 'OAUTH' && (
                  <>
                    {/* Connect button (admin testing OAuth) */}
                    <Button
                      onClick={handleAuthenticate}
                      variant="outlined"
                      disabled={isAnyActionInProgress}
                      startIcon={
                        authenticating ? (
                          <CircularProgress size={14} color="inherit" />
                        ) : (
                          <Iconify icon={lockIcon} width={15} height={15} />
                        )
                      }
                      sx={{
                        textTransform: 'none',
                        borderRadius: 1,
                        px: 2,
                        fontSize: '0.8125rem',
                        borderColor: alpha(theme.palette.primary.main, 0.5),
                        '&:hover': { borderColor: theme.palette.primary.main },
                      }}
                    >
                      {authenticating ? 'Connecting...' : isAuthenticated ? 'Reconnect' : 'Connect'}
                    </Button>

                    {/* Disconnect button (clear OAuth credentials) */}
                    {isAuthenticated && (
                      <Button
                        onClick={handleReauthenticate}
                        disabled={isAnyActionInProgress}
                        variant="outlined"
                        startIcon={
                          reauthenticating ? (
                            <CircularProgress size={14} color="inherit" />
                          ) : (
                            <Iconify icon={refreshIcon} width={15} height={15} />
                          )
                        }
                        sx={{
                          textTransform: 'none',
                          borderRadius: 1,
                          px: 2,
                          fontSize: '0.8125rem',
                          borderColor: alpha(theme.palette.warning.main, 0.5),
                          color: theme.palette.warning.main,
                          '&:hover': {
                            borderColor: theme.palette.warning.main,
                            backgroundColor: alpha(theme.palette.warning.main, 0.07),
                          },
                        }}
                      >
                        {reauthenticating ? 'Disconnecting...' : 'Disconnect'}
                      </Button>
                    )}

                    {/* Update Toolset (when switching OAuth config) */}
                    {isSwitchingOAuthConfig && (
                      <Button
                        onClick={handleConfirmSwitchOAuthConfig}
                        variant="contained"
                        disabled={savingOAuth || isAnyActionInProgress}
                        startIcon={
                          savingOAuth ? (
                            <CircularProgress size={14} sx={{ color: 'inherit' }} />
                          ) : (
                            <Iconify icon={saveIcon} width={16} height={16} />
                          )
                        }
                        sx={{
                          textTransform: 'none',
                          borderRadius: 1,
                          px: 2.5,
                          boxShadow: 'none',
                          '&:hover': { boxShadow: 'none' },
                        }}
                      >
                        {savingOAuth ? 'Updating...' : 'Update Toolset'}
                      </Button>
                    )}
                    
                    {/* Save OAuth Config (only when form is dirty and not switching) */}
                    {!isSwitchingOAuthConfig && oauthFormDirty && (
                      <Button
                        onClick={handleSaveOAuthConfig}
                        variant="contained"
                        disabled={savingOAuth || isAnyActionInProgress}
                        startIcon={
                          savingOAuth ? (
                            <CircularProgress size={14} sx={{ color: 'inherit' }} />
                          ) : (
                            <Iconify icon={saveIcon} width={16} height={16} />
                          )
                        }
                        sx={{
                          textTransform: 'none',
                          borderRadius: 1,
                          px: 2.5,
                          boxShadow: 'none',
                          '&:hover': { boxShadow: 'none' },
                        }}
                      >
                        {savingOAuth ? 'Saving...' : 'Save OAuth Config'}
                      </Button>
                    )}
                  </>
                )}

                {/* NON-ADMIN OAUTH: Show Authenticate button only */}
                {!isAdmin && manageAuthType === 'OAUTH' && (
                  <Button
                    onClick={handleAuthenticate}
                    variant="contained"
                    disabled={isAnyActionInProgress}
                    startIcon={
                      authenticating ? (
                        <CircularProgress size={14} sx={{ color: 'inherit' }} />
                      ) : (
                        <Iconify icon={lockIcon} width={16} height={16} />
                      )
                    }
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1,
                      px: 2.5,
                      boxShadow: 'none',
                      '&:hover': { boxShadow: 'none' },
                    }}
                  >
                    {authenticating ? 'Authenticating...' : isAuthenticated ? 'Re-authenticate' : 'Authenticate'}
                  </Button>
                )}

                {/* Save credentials (non-OAuth) */}
                {manageAuthType !== 'OAUTH' && manageAuthType !== 'NONE' && (
                  <Button
                    onClick={handleAuthenticateCredentials}
                    variant="contained"
                    disabled={isAnyActionInProgress}
                    startIcon={
                      saving ? (
                        <CircularProgress size={14} sx={{ color: 'inherit' }} />
                      ) : (
                        <Iconify icon={saveIcon} width={16} height={16} />
                      )
                    }
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1,
                      px: 2.5,
                      boxShadow: 'none',
                      '&:hover': { boxShadow: 'none' },
                    }}
                  >
                    {saving ? 'Saving...' : 'Save Credentials'}
                  </Button>
                )}
              </>
            ) : (
              /* ── CREATE MODE ACTIONS ── */
              <>
                {/* Create instance button */}
                {!configSaved && (
                  <Button
                    onClick={handleCreateInstance}
                    variant="contained"
                    disabled={isAnyActionInProgress}
                    startIcon={
                      saving ? (
                        <CircularProgress size={14} sx={{ color: 'inherit' }} />
                      ) : (
                        <Iconify icon={saveIcon} width={16} height={16} />
                      )
                    }
                    sx={{ textTransform: 'none', borderRadius: 1, px: 2.5, boxShadow: 'none', '&:hover': { boxShadow: 'none' } }}
                  >
                    {saving ? 'Creating...' : 'Create Instance'}
                  </Button>
                )}
              </>
            )}
          </Box>
        </DialogActions>

        {/* Local toast */}
        <Snackbar
          open={localToast.open}
          autoHideDuration={4000}
          onClose={hideLocalToast}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          sx={{ zIndex: (t) => t.zIndex.snackbar }}
        >
          <Alert onClose={hideLocalToast} severity={localToast.severity} variant="filled" sx={{ borderRadius: 1.5, fontWeight: 600, minWidth: 320 }}>
            {localToast.message}
          </Alert>
        </Snackbar>
      </Dialog>

      {/* Deauthentication confirmation dialog */}
      <ConfirmDeauthDialog
        open={confirmDeauth.open}
        userCount={authenticatedUserCount}
        actionLabel={confirmDeauth.label}
        onConfirm={confirmDeauth.action}
        onCancel={() => setConfirmDeauth({ open: false, action: async () => {}, label: '' })}
      />
    </>
  );
};

export default ToolsetConfigDialog;
