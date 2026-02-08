/**
 * Toolset Configuration Dialog
 * 
 * Dynamic, schema-based toolset configuration dialog following connector patterns:
 * - Fetches schema from backend
 * - Dynamic field rendering based on auth type
 * - OAuth authentication (popup window flow)
 * - API Token, Username/Password, and other auth types
 * - Validation and error handling
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
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import ToolsetApiService from 'src/services/toolset-api';
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

interface ToolsetConfigDialogProps {
  toolset: RegistryToolset | Partial<RegistryToolset>; // Can be RegistryToolset or Toolset (configured instance)
  toolsetId?: string; // If editing existing toolset (optional - will be created if not provided)
  onClose: () => void;
  onSuccess: () => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void; // Optional callback to show toast
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
        schemas?: Record<string, { fields: any[] }>;
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

const ToolsetConfigDialog: React.FC<ToolsetConfigDialogProps> = ({
  toolset,
  toolsetId,
  onClose,
  onSuccess,
  onShowToast,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Schema and configuration state
  const [toolsetSchema, setToolsetSchema] = useState<ToolsetSchema | null>(null);
  const [loadedToolsetConfig, setLoadedToolsetConfig] = useState<any>(null); // Store loaded config for auth type switching
  const [selectedAuthType, setSelectedAuthType] = useState<string>(
    (toolset.supportedAuthTypes && toolset.supportedAuthTypes.length > 0) 
      ? toolset.supportedAuthTypes[0] 
      : 'API_TOKEN'
  );
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  
  // UI state
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [authenticating, setAuthenticating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [configSaved, setConfigSaved] = useState(false);
  const [saveAttempted, setSaveAttempted] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Load toolset schema and configuration (only once, cached)
  useEffect(() => {
    const loadToolsetData = async () => {
      try {
        setLoading(true);
        setError(null);
        
        if (toolsetId) {
          // Editing existing toolset - get everything in one call (instance + config + schema)
          try {
            const toolsetData = await ToolsetApiService.getToolsetConfig(toolsetId);
            
            if (toolsetData.toolset) {
              const fullToolset = toolsetData.toolset;
              
              // Store loaded config for later use (e.g., when switching auth types)
              setLoadedToolsetConfig(fullToolset);
              
              // Set schema from response - structure matches registry schema format
              if (fullToolset.schema) {
                // Schema comes as { toolset: { config: { auth: {...} }, tools: [...] } }
                setToolsetSchema(fullToolset.schema as ToolsetSchema);
              }
              
              // Set status
              setIsAuthenticated(fullToolset.isAuthenticated || false);
              setConfigSaved(fullToolset.isConfigured || false);
              
              // Load existing config values if available
              if (fullToolset.config?.auth) {
                const authConfig = fullToolset.config.auth;
                setSelectedAuthType(
                  authConfig.type || 
                  (fullToolset.supportedAuthTypes && fullToolset.supportedAuthTypes.length > 0 
                    ? fullToolset.supportedAuthTypes[0] 
                    : 'API_TOKEN')
                );
                
                // Populate form data from existing config (including secret fields)
                const existingFormData: Record<string, any> = {};
                // Extract auth schema from the schema structure
                const toolsetSchemaData = fullToolset.schema?.toolset || fullToolset.schema;
                // Handle both schema structures: toolset.config.auth or toolset.auth or schema.auth
                const schemaAuthConfig = 
                  (toolsetSchemaData as any)?.config?.auth || 
                  (toolsetSchemaData as any)?.auth || 
                  (fullToolset.schema as any)?.auth || 
                  {};
                
                // Get schema for the current auth type (from config)
                const currentAuthType = authConfig.type || selectedAuthType;
                const authSchema = schemaAuthConfig.schemas?.[currentAuthType] || 
                                 schemaAuthConfig.schemas?.[Object.keys(schemaAuthConfig.schemas || {})[0]];
                
                if (authSchema?.fields) {
                  authSchema.fields.forEach((field: any) => {
                    // Populate ALL fields from config, including secret fields
                    // Secret fields should still be displayed (as password fields) so users can view/edit them
                    if (authConfig[field.name] !== undefined) {
                      existingFormData[field.name] = authConfig[field.name];
                    }
                  });
                }
                
                setFormData(existingFormData);
              } else {
                setSelectedAuthType(
                  (fullToolset.supportedAuthTypes && fullToolset.supportedAuthTypes.length > 0)
                    ? fullToolset.supportedAuthTypes[0]
                    : 'API_TOKEN'
                );
              }
            }
          } catch (err: any) {
            console.error('Failed to load toolset config:', err);
            setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to load toolset configuration');
          }
        } else {
          // New toolset - load schema only
          const toolsetName = (toolset as any).name || toolset.displayName || '';
          if (!toolsetName) {
            setError('Toolset name is required');
            setLoading(false);
            return;
          }
          
          const schema = await ToolsetApiService.getToolsetSchema(toolsetName);
          setToolsetSchema(schema);
          
          // Set default auth type
          setSelectedAuthType(
            (toolset.supportedAuthTypes && toolset.supportedAuthTypes.length > 0)
              ? toolset.supportedAuthTypes[0]
              : 'API_TOKEN'
          );
        }
      } catch (err: any) {
        console.error('Failed to load toolset data:', err);
        setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to load toolset configuration');
      } finally {
        setLoading(false);
      }
    };

    loadToolsetData();
    // Reload if toolsetId changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolsetId]);

  // Check authentication status
  const checkAuthStatus = useCallback(async () => {
    const currentToolsetId = toolsetId || (toolset as any)._id;
    if (!currentToolsetId) return;
    
    try {
      const status = await ToolsetApiService.checkToolsetStatus(currentToolsetId);
      setIsAuthenticated(status.isAuthenticated);
      setConfigSaved(status.isConfigured);
    } catch (err) {
      console.error('Failed to check toolset status:', err);
    }
  }, [toolsetId, toolset]);

  // Get current auth schema based on selected auth type
  // Schema structure from API: { toolset: { config: { auth: { schemas: { OAUTH: { fields: [...] } } } } } }
  const currentAuthSchema = useMemo(() => {
    if (!toolsetSchema) {
      return { fields: [], redirectUri: '', displayRedirectUri: false };
    }
    
    // Extract schemas from the correct path - handle both response formats
    const toolsetData = (toolsetSchema as any).toolset || toolsetSchema;
    const authConfig = toolsetData.config?.auth || toolsetData.auth || {};
    const schemas = authConfig.schemas || {};
    
    if (!schemas || Object.keys(schemas).length === 0) {
      return { fields: [], redirectUri: '', displayRedirectUri: false };
    }
    
    // Get schema for selected auth type
    let schema = null;
    if (selectedAuthType && schemas[selectedAuthType]) {
      schema = schemas[selectedAuthType];
    } else {
      // Fallback to first available schema
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
  }, [toolsetSchema, selectedAuthType]);

  // Get redirect URI value (from schema or form data)
  const redirectUriValue = useMemo(() => {
    if (currentAuthSchema.redirectUri) {
      // If it's a relative path, make it absolute
      const uri = currentAuthSchema.redirectUri;
      if (uri && !uri.startsWith('http')) {
        return `${window.location.origin}/${uri.replace(/^\//, '')}`;
      }
      return uri;
    }
    return '';
  }, [currentAuthSchema.redirectUri]);

  // State for redirect URI display
  const [showRedirectUri, setShowRedirectUri] = useState(true);
  const [copied, setCopied] = useState(false);

  const handleCopyRedirectUri = useCallback(() => {
    if (redirectUriValue) {
      navigator.clipboard.writeText(redirectUriValue);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [redirectUriValue]);

  // Handle field changes
  const handleFieldChange = useCallback((fieldName: string, value: any) => {
    setFormData((prev) => ({ ...prev, [fieldName]: value }));
    // Clear error for this field
    setFormErrors((prev) => {
      const newErrors = { ...prev };
      delete newErrors[fieldName];
      return newErrors;
    });
  }, []);

  // Validate form
  const validateForm = useCallback(() => {
    const errors: Record<string, string> = {};
    const fields = currentAuthSchema.fields || [];

    fields.forEach((field: any) => {
      const value = formData[field.name];
      
      if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
        errors[field.name] = `${field.displayName} is required`;
      }

      // Add more validation rules based on field.validation
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

    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  }, [currentAuthSchema, formData]);

  const handleSaveConfig = async () => {
    try {
      setSaving(true);
      setSaveAttempted(true);
      setError(null);
      setSuccess(null);

      // Validate form
      if (!validateForm()) {
        setError('Please fill in all required fields correctly');
        return;
      }

      // Prepare auth config
      const authConfig: any = {
        type: selectedAuthType,
        ...formData,
      };

      // Add redirect URI to auth config if OAuth (from schema)
      if (selectedAuthType === 'OAUTH' && currentAuthSchema.redirectUri) {
        authConfig.redirectUri = currentAuthSchema.redirectUri;
      }

      let currentToolsetId = toolsetId;

      // Create or update toolset
      if (!currentToolsetId) {
        // Create new toolset (creates node and saves config in one call)
        try {
          // Get toolset name - handle both RegistryToolset and Toolset types
          const toolsetName = (toolset as any).name || toolset.displayName || '';
          if (!toolsetName) {
            setError('Toolset name is required');
            return;
          }
          
          currentToolsetId = await ToolsetApiService.createToolset({
            name: toolsetName,
            displayName: toolset.displayName || toolsetName,
            type: (toolset as any).category || toolset.category || 'app',
            auth: authConfig,
          });
          
          // Store the created ID for subsequent operations
          (toolset as any)._id = currentToolsetId;
        } catch (err: any) {
          console.error('Failed to create toolset:', err);
          setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to create toolset. Please try again.');
          return;
        }
      } else {
        // Update existing toolset config
        try {
          await ToolsetApiService.updateToolsetConfig(currentToolsetId, {
            auth: authConfig,
          });
        } catch (err: any) {
          console.error('Failed to update toolset config:', err);
          setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to update configuration');
          return;
        }
      }

      setConfigSaved(true);

      // Refresh auth status after update
      if (currentToolsetId) {
        await checkAuthStatus();
      }

      // If OAuth, show authenticate button (or if auth status changed to false)
      if (selectedAuthType === 'OAUTH') {
        const message = toolsetId 
          ? 'Configuration updated! OAuth credentials have been updated. Please click "Authenticate" to complete the OAuth flow.'
          : 'Configuration saved! Now click "Authenticate" to complete the OAuth flow.';
        setSuccess(message);
        setIsAuthenticated(false); // OAuth updates require re-authentication
        
        // Show toast notification (don't auto-close so user can authenticate)
        if (onShowToast) {
          onShowToast(message, 'success');
        }
        // Don't auto-close dialog - user needs to authenticate
      } else {
        // For non-OAuth auth types, show success message
        const message = toolsetId 
          ? 'Configuration updated successfully!' 
          : 'Configuration saved successfully!';
        setSuccess(message);
        
        // Show toast notification
        if (onShowToast) {
          onShowToast(message, 'success');
        }
        
        // Auto-close after short delay for non-OAuth (no further action needed)
        setTimeout(() => {
          onSuccess();
        }, 1500);
      }
    } catch (err: any) {
      console.error('Failed to save toolset config:', err);
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    const currentToolsetId = toolsetId || (toolset as any)._id;
    if (!currentToolsetId) {
      setError('Toolset ID is required');
      return;
    }

    if (!window.confirm(`Are you sure you want to delete the configuration for ${toolset.displayName || (toolset as any).name}? This action cannot be undone.`)) {
      return;
    }

    try {
      setDeleting(true);
      setError(null);
      setSuccess(null);

      const toolsetName = (toolset as any).name || toolset.displayName || '';
      await ToolsetApiService.deleteToolsetConfig(toolsetName);
      
      setSuccess('Toolset configuration deleted successfully');
      setTimeout(() => {
        onSuccess();
      }, 1500);
    } catch (err: any) {
      console.error('Failed to delete toolset config:', err);
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to delete configuration');
    } finally {
      setDeleting(false);
    }
  };

  const handleAuthenticate = async () => {
    // Validate prerequisites
    const currentToolsetId = toolsetId || (toolset as any)._id;
    if (!currentToolsetId) {
      setError('Toolset ID is required. Please save configuration first.');
      return;
    }

    if (!configSaved) {
      setError('Please save configuration first');
      return;
    }

    try {
      setAuthenticating(true);
      setError(null);
      setSuccess(null);

      // Get OAuth authorization URL
      const response = await ToolsetApiService.getOAuthAuthorizationUrl(
        currentToolsetId,
        window.location.origin
      );

      if (!response.success || !response.authorizationUrl) {
        throw new Error('Failed to get authorization URL');
      }

      // Calculate popup position (centered)
      const width = 600;
      const height = 700;
      const left = window.screen.width / 2 - width / 2;
      const top = window.screen.height / 2 - height / 2;

      // Open OAuth popup window
      const popup = window.open(
        response.authorizationUrl,
        'oauth_popup',
        `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`
      );

      if (!popup) {
        throw new Error('Popup blocked. Please allow popups for this site and try again.');
      }

      // Focus the popup
      popup.focus();

      // Poll for popup closure and check auth status
      let pollCount = 0;
      const maxPolls = 300; // 5 minutes with 1 second intervals
      
      const pollInterval = setInterval(async () => {
        pollCount += 1;

        // Check if popup is closed
        if (popup.closed) {
          clearInterval(pollInterval);
          setAuthenticating(false);

          // Give backend time to update status
          await new Promise(resolve => setTimeout(resolve, 500));

          // Check if authentication was successful
          try {
            const toolsetIdToCheck = toolsetId || (toolset as any)._id;
            const status = await ToolsetApiService.checkToolsetStatus(toolsetIdToCheck);
            if (status.isAuthenticated) {
              setIsAuthenticated(true);
              setSuccess('Authentication successful!');
              
              // Show toast notification
              if (onShowToast) {
                onShowToast('Authentication successful!', 'success');
              }
              
              // Auto-close after short delay
              setTimeout(() => {
                onSuccess();
              }, 1500);
            } else {
              setError('Authentication failed or was cancelled');
              if (onShowToast) {
                onShowToast('Authentication failed or was cancelled', 'error');
              }
            }
          } catch (err) {
            console.error('Failed to verify auth status:', err);
            setError('Failed to verify authentication status');
          }
          return;
        }

        // Timeout after max polls
        if (pollCount >= maxPolls) {
          clearInterval(pollInterval);
          if (!popup.closed) {
            popup.close();
          }
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

  const isOAuth = selectedAuthType === 'OAUTH';
  const hasRequiredFields = currentAuthSchema.fields?.some((f: any) => f.required) || false;

  // Loading state with skeleton loader
  if (loading) {
    return (
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
            <Stack spacing={2}>
              <Skeleton variant="text" width="40%" height={24} />
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                <Skeleton variant="rectangular" width={100} height={26} sx={{ borderRadius: 1 }} />
                <Skeleton variant="rectangular" width={100} height={26} sx={{ borderRadius: 1 }} />
                <Skeleton variant="rectangular" width={100} height={26} sx={{ borderRadius: 1 }} />
              </Box>
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions
          sx={{
            px: 3,
            py: 2.5,
            borderTop: isDark
              ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
              : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          }}
        >
          <Skeleton variant="rectangular" width={80} height={36} sx={{ borderRadius: 1 }} />
          <Skeleton variant="rectangular" width={150} height={36} sx={{ borderRadius: 1 }} />
        </DialogActions>
      </Dialog>
    );
  }

  return (
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
              bgcolor: isDark
                ? alpha(theme.palette.common.white, 0.08)
                : alpha(theme.palette.grey[100], 0.8),
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
              src={toolset.iconPath || '/assets/icons/toolsets/default.svg'}
              alt={toolset.displayName || (toolset as any).name}
              width={32}
              height={32}
              style={{ objectFit: 'contain' }}
              onError={(e: React.SyntheticEvent<HTMLImageElement>) => {
                const target = e.target as HTMLImageElement;
                target.src = '/assets/icons/toolsets/default.svg';
              }}
            />
          </Box>
          <Box>
            <Typography
              variant="h6"
              sx={{
                fontWeight: 600,
                mb: 0.5,
                color: theme.palette.text.primary,
                fontSize: '1.125rem',
                letterSpacing: '-0.01em',
              }}
            >
              Configure {toolset.displayName || (toolset as any).name || 'Toolset'}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Chip
                label={toolset.category || 'app'}
                size="small"
                variant="outlined"
                sx={{
                  fontSize: '0.6875rem',
                  height: 20,
                  fontWeight: 500,
                  borderColor: isDark
                    ? alpha(theme.palette.divider, 0.3)
                    : alpha(theme.palette.divider, 0.2),
                  bgcolor: isDark ? alpha(theme.palette.common.white, 0.05) : 'transparent',
                  color: isDark
                    ? alpha(theme.palette.text.primary, 0.9)
                    : theme.palette.text.secondary,
                  '& .MuiChip-label': { px: 1.25, py: 0 },
                }}
              />
              {selectedAuthType && (
                <Chip
                  label={selectedAuthType.split('_').join(' ')}
                  size="small"
                  variant="outlined"
                  sx={{
                    fontSize: '0.6875rem',
                    height: 20,
                    fontWeight: 500,
                    borderColor: isDark
                      ? alpha(theme.palette.divider, 0.3)
                      : alpha(theme.palette.divider, 0.2),
                    bgcolor: isDark ? alpha(theme.palette.common.white, 0.05) : 'transparent',
                    color: isDark
                      ? alpha(theme.palette.text.primary, 0.9)
                      : theme.palette.text.secondary,
                    '& .MuiChip-label': { px: 1.25, py: 0 },
                  }}
                />
              )}
            </Box>
          </Box>
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          sx={{
            color: isDark ? alpha(theme.palette.text.secondary, 0.8) : theme.palette.text.secondary,
            p: 1,
            '&:hover': {
              backgroundColor: isDark
                ? alpha(theme.palette.common.white, 0.1)
                : alpha(theme.palette.text.secondary, 0.08),
              color: theme.palette.text.primary,
            },
            transition: 'all 0.2s ease',
          }}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ px: 3, py: 3 }}>
        <Stack spacing={3}>
          {/* Error Alert */}
          {error && (
            <Alert 
              severity="error" 
              onClose={() => setError(null)}
              sx={{ borderRadius: 1.5 }}
            >
              {error}
            </Alert>
          )}

          {/* Success Alert */}
          {success && (
            <Alert 
              severity="success" 
              onClose={() => setSuccess(null)}
              sx={{ borderRadius: 1.5 }}
            >
              {success}
            </Alert>
          )}

          {/* Authenticated Alert */}
          {isAuthenticated && (
            <Alert 
              severity="success" 
              icon={<Iconify icon={checkCircleIcon} />}
              sx={{ borderRadius: 1.5 }}
            >
              This toolset is authenticated and ready to use. You can update the configuration below.
            </Alert>
          )}

          {/* Description */}
          {toolset.description && (
            <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem', lineHeight: 1.6 }}>
              {toolset.description}
            </Typography>
          )}

          {/* Authentication Type Selector */}
          {toolset.supportedAuthTypes && toolset.supportedAuthTypes.length > 1 && (
            <FormControl fullWidth>
              <InputLabel>Authentication Type</InputLabel>
              <Select
                value={selectedAuthType}
                onChange={(e) => {
                  const newAuthType = e.target.value;
                  setSelectedAuthType(newAuthType);
                  
                  // Reload form data for the new auth type if config exists
                  if (loadedToolsetConfig?.config?.auth && loadedToolsetConfig.config.auth.type === newAuthType) {
                    const existingFormData: Record<string, any> = {};
                    const toolsetSchemaData = (toolsetSchema as any)?.toolset || toolsetSchema;
                    const schemaAuthConfig = 
                      (toolsetSchemaData as any)?.config?.auth || 
                      (toolsetSchemaData as any)?.auth || 
                      {};
                    const authSchema = schemaAuthConfig.schemas?.[newAuthType];
                    
                    if (authSchema?.fields && loadedToolsetConfig.config.auth) {
                      authSchema.fields.forEach((field: any) => {
                        if (loadedToolsetConfig.config.auth[field.name] !== undefined) {
                          existingFormData[field.name] = loadedToolsetConfig.config.auth[field.name];
                        }
                      });
                    }
                    setFormData(existingFormData);
                  } else {
                    setFormData({}); // Clear form data when switching to a new auth type
                  }
                  
                  setFormErrors({});
                  setSaveAttempted(false);
                }}
                label="Authentication Type"
                sx={{
                  borderRadius: 1.25,
                  '& .MuiSelect-select': {
                    py: 1.5,
                  },
                }}
              >
                {(toolset.supportedAuthTypes || []).map((type) => (
                  <MenuItem key={type} value={type}>
                    {type.split('_').map(word => 
                      word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
                    ).join(' ')}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}

          {/* Collapsible Redirect URI - Show for OAuth */}
          {isOAuth && redirectUriValue && currentAuthSchema.displayRedirectUri && (
            <Paper
              variant="outlined"
              sx={{
                borderRadius: 1.25,
                overflow: 'hidden',
                bgcolor: isDark
                  ? alpha(theme.palette.primary.main, 0.08)
                  : alpha(theme.palette.primary.main, 0.03),
                borderColor: isDark
                  ? alpha(theme.palette.primary.main, 0.25)
                  : alpha(theme.palette.primary.main, 0.15),
                boxShadow: isDark
                  ? `0 1px 3px ${alpha(theme.palette.primary.main, 0.15)}`
                  : `0 1px 3px ${alpha(theme.palette.primary.main, 0.05)}`,
              }}
            >
              <Box
                onClick={() => setShowRedirectUri(!showRedirectUri)}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  p: 1.5,
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                  '&:hover': {
                    bgcolor: alpha(theme.palette.primary.main, 0.05),
                  },
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box
                    sx={{
                      p: 0.625,
                      borderRadius: 1,
                      bgcolor: alpha(theme.palette.primary.main, 0.12),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Iconify icon={infoIcon} width={16} color={theme.palette.primary.main} />
                  </Box>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      fontSize: '0.875rem',
                      fontWeight: 600,
                      color: theme.palette.primary.main,
                    }}
                  >
                    Redirect URI
                  </Typography>
                </Box>
                <Iconify
                  icon={chevronDownIcon}
                  width={20}
                  color={theme.palette.text.secondary}
                  sx={{
                    transform: showRedirectUri ? 'rotate(180deg)' : 'rotate(0deg)',
                    transition: 'transform 0.2s',
                  }}
                />
              </Box>

              <Collapse in={showRedirectUri}>
                <Box sx={{ px: 1.5, pb: 1.5 }}>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ fontSize: '0.8125rem', mb: 1.25, lineHeight: 1.5 }}
                  >
                    Use this URL when configuring your {toolset.displayName || toolset.name} OAuth2 App.
                  </Typography>
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      p: 1.25,
                      borderRadius: 1,
                      bgcolor: isDark
                        ? alpha(theme.palette.grey[900], 0.4)
                        : alpha(theme.palette.grey[100], 0.8),
                      border: `1.5px solid ${alpha(theme.palette.primary.main, isDark ? 0.25 : 0.15)}`,
                      transition: 'all 0.2s',
                      '&:hover': {
                        borderColor: alpha(theme.palette.primary.main, isDark ? 0.4 : 0.3),
                        bgcolor: isDark
                          ? alpha(theme.palette.grey[900], 0.6)
                          : alpha(theme.palette.grey[100], 1),
                      },
                    }}
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        flex: 1,
                        fontFamily: '"SF Mono", "Roboto Mono", Monaco, Consolas, monospace',
                        fontSize: '0.8125rem',
                        wordBreak: 'break-all',
                        color:
                          theme.palette.mode === 'dark'
                            ? theme.palette.primary.light
                            : theme.palette.primary.dark,
                        fontWeight: 500,
                        userSelect: 'all',
                        lineHeight: 1.6,
                      }}
                    >
                      {redirectUriValue}
                    </Typography>
                    <Tooltip title={copied ? 'Copied!' : 'Copy to clipboard'} arrow>
                      <IconButton
                        size="small"
                        onClick={handleCopyRedirectUri}
                        sx={{
                          p: 0.75,
                          bgcolor: alpha(theme.palette.primary.main, 0.1),
                          transition: 'all 0.2s',
                          '&:hover': {
                            bgcolor: alpha(theme.palette.primary.main, 0.2),
                            transform: 'scale(1.05)',
                          },
                        }}
                      >
                        <Iconify
                          icon={copied ? checkIcon : copyIcon}
                          width={16}
                          color={theme.palette.primary.main}
                        />
                      </IconButton>
                    </Tooltip>
                  </Box>
                </Box>
              </Collapse>
            </Paper>
          )}

          {/* Dynamic Form Fields - Show always so users can view/edit their configuration */}
          {(
            <Paper
              variant="outlined"
              sx={{
                p: 2,
                borderRadius: 1.25,
                bgcolor: isDark
                  ? alpha(theme.palette.background.paper, 0.4)
                  : theme.palette.background.paper,
                borderColor: isDark
                  ? alpha(theme.palette.divider, 0.12)
                  : alpha(theme.palette.divider, 0.1),
                boxShadow: isDark
                  ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
                  : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
              }}
            >
              <Stack spacing={2}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box
                    sx={{
                      p: 0.625,
                      borderRadius: 1,
                      bgcolor: alpha(theme.palette.text.primary, 0.05),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Iconify
                      icon={isOAuth ? lockIcon : keyIcon}
                      width={16}
                      sx={{ color: theme.palette.text.primary }}
                    />
                  </Box>
                  <Typography 
                    variant="subtitle2" 
                    sx={{ fontWeight: 600, fontSize: '0.9375rem' }}
                  >
                    {isOAuth ? 'OAuth Credentials' : 'Authentication Details'}
                  </Typography>
                </Box>

                {currentAuthSchema.fields && currentAuthSchema.fields.length > 0 ? (
                  <Grid container spacing={2}>
                    {currentAuthSchema.fields.map((field: any) => (
                      <Grid item xs={12} key={field.name}>
                        <FieldRenderer
                          field={field}
                          value={formData[field.name] ?? field.defaultValue ?? ''}
                          onChange={(value) => handleFieldChange(field.name, value)}
                          error={saveAttempted ? formErrors[field.name] : undefined}
                        />
                      </Grid>
                    ))}
                  </Grid>
                ) : (
                  <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                    No authentication fields required for this authentication type.
                  </Alert>
                )}

                {/* OAuth Info */}
                {isOAuth && !isAuthenticated && (
                  <Alert 
                    severity="info" 
                    sx={{ borderRadius: 1.25 }}
                  >
                    {toolsetId
                      ? 'Update your OAuth credentials below, then click "Update Configuration" to save. After updating, click "Authenticate" to complete the OAuth flow.'
                      : configSaved
                        ? 'Configuration saved. Click "Authenticate" below to complete the OAuth flow.'
                        : 'Enter your OAuth credentials, then click "Save Configuration" to proceed.'}
                  </Alert>
                )}
                
                {/* API Token Info */}
                {!isOAuth && isAuthenticated && (
                  <Alert 
                    severity="success" 
                    sx={{ borderRadius: 1.25 }}
                  >
                    This toolset is authenticated and ready to use. You can update the configuration below.
                  </Alert>
                )}
              </Stack>
            </Paper>
          )}

          {/* Tool Preview */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600 }}>
              Available Tools ({toolset.toolCount || (toolset.tools ? toolset.tools.length : 0)})
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {toolset.tools && toolset.tools.length > 0 ? (
                <>
                  {toolset.tools.slice(0, 5).map((tool) => (
                    <Chip
                      key={tool.fullName || tool.name}
                      label={tool.name}
                      size="small"
                      variant="outlined"
                      sx={{
                        borderRadius: 1,
                        fontSize: '0.8125rem',
                        height: 26,
                      }}
                    />
                  ))}
                  {toolset.tools.length > 5 && (
                    <Chip
                      label={`+${toolset.tools.length - 5} more`}
                      size="small"
                      variant="outlined"
                      sx={{
                        borderRadius: 1,
                        fontSize: '0.8125rem',
                        height: 26,
                        fontWeight: 600,
                      }}
                    />
                  )}
                </>
              ) : (
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                  {toolset.toolCount || 0} tools will be available after configuration
                </Typography>
              )}
            </Stack>
          </Box>
        </Stack>
      </DialogContent>

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
        <Box>
          {/* Delete Button - Only show when editing existing toolset */}
          {toolsetId && (
            <Button
              onClick={handleDelete}
              disabled={deleting || saving || authenticating}
              variant="outlined"
              color="error"
              startIcon={
                deleting ? (
                  <CircularProgress size={16} />
                ) : (
                  <Iconify icon={deleteIcon} width={16} height={16} />
                )
              }
              sx={{
                textTransform: 'none',
                borderRadius: 1,
                px: 2.5,
                borderColor: isDark
                  ? alpha(theme.palette.error.main, 0.3)
                  : alpha(theme.palette.error.main, 0.5),
                color: theme.palette.error.main,
                '&:hover': {
                  borderColor: theme.palette.error.main,
                  backgroundColor: alpha(theme.palette.error.main, 0.08),
                },
              }}
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </Button>
          )}
        </Box>

        <Box sx={{ display: 'flex', gap: 1.5 }}>
          <Button 
            onClick={onClose} 
            disabled={saving || authenticating || deleting}
            variant="outlined"
            sx={{
              textTransform: 'none',
              borderRadius: 1,
              px: 2.5,
              borderColor: isDark
                ? alpha(theme.palette.divider, 0.3)
                : alpha(theme.palette.divider, 0.2),
              color: isDark
                ? alpha(theme.palette.text.secondary, 0.9)
                : theme.palette.text.secondary,
              '&:hover': {
                borderColor: isDark
                  ? alpha(theme.palette.text.secondary, 0.5)
                  : alpha(theme.palette.text.secondary, 0.4),
                backgroundColor: isDark
                  ? alpha(theme.palette.common.white, 0.08)
                  : alpha(theme.palette.text.secondary, 0.04),
              },
              transition: 'all 0.2s ease',
            }}
          >
            {isAuthenticated ? 'Close' : 'Cancel'}
          </Button>

          {/* When editing existing toolset: Always show Update button, then Authenticate if OAuth */}
          {toolsetId ? (
            <>
              {/* Update Configuration Button - Always show when editing */}
              <Button
                onClick={handleSaveConfig}
                variant="contained"
                disabled={saving}
                startIcon={
                  saving ? (
                    <CircularProgress size={16} />
                  ) : (
                    <Iconify icon={saveIcon} width={16} height={16} />
                  )
                }
                sx={{
                  textTransform: 'none',
                  borderRadius: 1,
                  px: 2.5,
                  boxShadow: isDark ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}` : 'none',
                  '&:hover': {
                    boxShadow: isDark
                      ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                      : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
                  },
                  '&:active': {
                    boxShadow: 'none',
                  },
                  transition: 'all 0.2s ease',
                }}
              >
                {saving ? 'Updating...' : 'Update Configuration'}
              </Button>

              {/* Authenticate Button - Show for OAuth after config is saved and not authenticated */}
              {isOAuth && configSaved && !isAuthenticated && (
                <Button
                  onClick={handleAuthenticate}
                  variant="outlined"
                  disabled={authenticating}
                  startIcon={
                    authenticating ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify icon={lockIcon} width={16} height={16} />
                    )
                  }
                  sx={{
                    textTransform: 'none',
                    borderRadius: 1,
                    px: 2.5,
                    borderColor: isDark
                      ? alpha(theme.palette.primary.main, 0.3)
                      : alpha(theme.palette.primary.main, 0.5),
                    color: theme.palette.primary.main,
                    '&:hover': {
                      borderColor: theme.palette.primary.main,
                      backgroundColor: alpha(theme.palette.primary.main, 0.08),
                    },
                  }}
                >
                  {authenticating ? 'Authenticating...' : 'Authenticate'}
                </Button>
              )}
            </>
          ) : (
            <>
              {/* Creating new toolset: Show Save first, then Authenticate for OAuth */}
              {isOAuth && !configSaved && (
                <Button
                  onClick={handleSaveConfig}
                  variant="outlined"
                  disabled={saving}
                  startIcon={
                    saving ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify icon={saveIcon} width={16} height={16} />
                    )
                  }
                  sx={{
                    textTransform: 'none',
                    borderRadius: 1,
                    px: 2.5,
                  }}
                >
                  {saving ? 'Saving...' : 'Save Configuration'}
                </Button>
              )}

              {/* Authenticate Button (OAuth only, after config saved for new toolset) */}
              {isOAuth && configSaved && !isAuthenticated && (
                <Button
                  onClick={handleAuthenticate}
                  variant="contained"
                  disabled={authenticating}
                  startIcon={
                    authenticating ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify icon={lockIcon} width={16} height={16} />
                    )
                  }
                  sx={{
                    textTransform: 'none',
                    borderRadius: 1,
                    px: 2.5,
                  }}
                >
                  {authenticating ? 'Authenticating...' : 'Authenticate'}
                </Button>
              )}

              {/* Save Button (Non-OAuth for new toolset) */}
              {!isOAuth && (
                <Button
                  onClick={handleSaveConfig}
                  variant="contained"
                  disabled={saving}
                  startIcon={
                    saving ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify icon={saveIcon} width={16} height={16} />
                    )
                  }
                  sx={{
                    textTransform: 'none',
                    borderRadius: 1,
                    px: 2.5,
                    boxShadow: isDark ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}` : 'none',
                    '&:hover': {
                      boxShadow: isDark
                        ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                        : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
                    },
                    '&:active': {
                      boxShadow: 'none',
                    },
                    transition: 'all 0.2s ease',
                  }}
                >
                  {saving ? 'Saving...' : 'Save Configuration'}
                </Button>
              )}
            </>
          )}
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default ToolsetConfigDialog;
