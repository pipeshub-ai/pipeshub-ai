import { z } from 'zod';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import infoIcon from '@iconify-icons/eva/info-outline';
import lockIcon from '@iconify-icons/eva/lock-outline';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import editOutlineIcon from '@iconify-icons/eva/edit-outline';
import saveOutlineIcon from '@iconify-icons/eva/save-outline';
import closeOutlineIcon from '@iconify-icons/eva/close-outline';
import linkIcon from '@iconify-icons/eva/link-outline';
import buildingIcon from '@iconify-icons/mdi/microsoft-onedrive';
import { useState, useEffect, forwardRef, useCallback, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Alert,
  Paper,
  Stack,
  Button,
  Tooltip,
  TextField,
  Typography,
  IconButton,
  InputAdornment,
  CircularProgress,
  FormControlLabel,
  Checkbox,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import { getConnectorPublicUrl } from '../../services/utils/services-configuration-service';

interface SharePointConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  isEnabled?: boolean;
}

export interface SharePointConfigFormRef {
  handleSave: () => Promise<boolean>;
}

// Define Zod schema for form validation
const sharepointConfigSchema = z.object({
  clientId: z.string().min(1, { message: 'Client ID is required' }),
  clientSecret: z.string().min(1, { message: 'Client Secret is required' }),
  tenantId: z.string().min(1, { message: 'Tenant ID is required' }),
  hasAdminConsent: z.boolean(),
});

type SharePointConfigFormData = z.infer<typeof sharepointConfigSchema>;

const SharePointConfigForm = forwardRef<SharePointConfigFormRef, SharePointConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, isEnabled }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState<SharePointConfigFormData>({
      clientId: '',
      clientSecret: '',
      tenantId: '',
      hasAdminConsent: true,
    });

    const [errors, setErrors] = useState<Record<string, string>>({});
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [showClientSecret, setShowClientSecret] = useState(false);
    const [isConfigured, setIsConfigured] = useState(false);
    const [saveSuccess, setSaveSuccess] = useState(false);
    const [tenantUrl, setTenantUrl] = useState('');
    const [realTimeUpdatesEnabled, setRealTimeUpdatesEnabled] = useState(false);

    // New state variables for edit mode
    const [formEditMode, setFormEditMode] = useState(false);

    // Store original values for cancel operation
    const [originalState, setOriginalState] = useState({
      clientId: '',
      clientSecret: '',
      tenantId: '',
      hasAdminConsent: true,
      realTimeUpdatesEnabled: false,
    });

    const handleEventSubscriptionsChange = (event: React.ChangeEvent<HTMLInputElement>) => {
      if (!formEditMode && isConfigured) {
        // If trying to change when not in edit mode, enter edit mode first
        handleEnterEditMode();
        return;
      }

      const { checked } = event.target;
      setRealTimeUpdatesEnabled(checked);
    };

    // Enable edit mode
    const handleEnterEditMode = () => {
      // Store original values before editing
      setOriginalState({
        clientId: formData.clientId,
        clientSecret: formData.clientSecret,
        tenantId: formData.tenantId,
        hasAdminConsent: formData.hasAdminConsent,
        realTimeUpdatesEnabled,
      });

      setFormEditMode(true);
    };

    // Cancel edit mode and restore original values
    const handleCancelEdit = () => {
      setFormData({
        clientId: originalState.clientId,
        clientSecret: originalState.clientSecret,
        tenantId: originalState.tenantId,
        hasAdminConsent: originalState.hasAdminConsent,
      });
      setRealTimeUpdatesEnabled(originalState.realTimeUpdatesEnabled);

      // Clear any errors
      setErrors({});
      setFormEditMode(false);
      setSaveError(null);
    };

    useEffect(() => {
      const initializeForm = async () => {
        setIsLoading(true);

        // Fetch existing config
        try {
          const response = await axios.get('/api/v1/connectors/config', {
            params: {
              service: 'sharepoint',
            },
          });

          if (response.data) {
            const formValues = {
              clientId: response.data.clientId || '',
              clientSecret: response.data.clientSecret || '',
              tenantId: response.data.tenantId || '',
              hasAdminConsent: response.data.hasAdminConsent !== undefined ? response.data.hasAdminConsent : true,
            };

            setFormData(formValues);

            if (Object.prototype.hasOwnProperty.call(response.data, 'realTimeUpdatesEnabled')) {
              setRealTimeUpdatesEnabled(response.data.realTimeUpdatesEnabled);
            }

            // Set original state
            setOriginalState({
              clientId: formValues.clientId,
              clientSecret: formValues.clientSecret,
              tenantId: formValues.tenantId,
              hasAdminConsent: formValues.hasAdminConsent,
              realTimeUpdatesEnabled: response.data.realTimeUpdatesEnabled || false,
            });

            setIsConfigured(true);
          }
        } catch (error) {
          console.error('Error fetching SharePoint config:', error);
          setSaveError('Failed to fetch configuration.');
        } finally {
          setIsLoading(false);
        }
      };

      initializeForm();
    }, []);

    useEffect(() => {
      const fetchTenantUrl = async () => {
        try {
          // Set tenant URL to the frontend URL (window.location.origin)
          setTenantUrl(window.location.origin);
        } catch (error) {
          console.error('Failed to get tenant URL', error);
          setTenantUrl(window.location.origin);
        }
      };

      fetchTenantUrl();
    }, []);

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave,
    }));

    // Validate form using Zod and notify parent
    useEffect(() => {
      try {
        // Parse the data with zod schema
        sharepointConfigSchema.parse(formData);
        setErrors({});
        onValidationChange(true);
      } catch (validationError) {
        if (validationError instanceof z.ZodError) {
          // Extract errors into a more manageable format
          const errorMap: Record<string, string> = {};
          validationError.errors.forEach((err) => {
            const path = err.path.join('.');
            errorMap[path] = err.message;
          });
          setErrors(errorMap);
          onValidationChange(false);
        }
      }
    }, [formData, onValidationChange]);

    // Handle input change
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (!formEditMode && isConfigured) {
        // If trying to edit when not in edit mode, enter edit mode first
        handleEnterEditMode();
        return;
      }

      const { name, value } = e.target;
      setFormData({
        ...formData,
        [name]: value,
      });
    };

    // Handle checkbox change
    const handleCheckboxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (!formEditMode && isConfigured) {
        // If trying to edit when not in edit mode, enter edit mode first
        handleEnterEditMode();
        return;
      }

      const { name, checked } = e.target;
      setFormData({
        ...formData,
        [name]: checked,
      });
    };

    // Toggle secret visibility
    const handleToggleClientSecretVisibility = () => {
      setShowClientSecret(!showClientSecret);
    };

    // Handle save
    const handleSave = async (): Promise<boolean> => {
      setIsSaving(true);
      setSaveError(null);
      setSaveSuccess(false);

      try {
        // Validate the form data with Zod before saving
        sharepointConfigSchema.parse(formData);

        const payload = {
          ...formData,
          realTimeUpdatesEnabled,
        };

        // Send the update request
        await axios.post('/api/v1/connectors/config', payload, {
          params: {
            service: 'sharepoint',
          },
        });

        // Update the configured state
        setIsConfigured(true);
        setSaveSuccess(true);

        // Exit edit mode
        setFormEditMode(false);

        if (onSaveSuccess) {
          onSaveSuccess();
        }

        return true;
      } catch (error) {
        if (error instanceof z.ZodError) {
          // Handle validation errors
          const errorMap: Record<string, string> = {};
          error.errors.forEach((err) => {
            const path = err.path.join('.');
            errorMap[path] = err.message;
          });
          setErrors(errorMap);
          setSaveError('Please correct the form errors before saving');
        } else {
          // Handle API errors
          setSaveError('Failed to save SharePoint configuration');
          console.error('Error saving SharePoint config:', error);
        }
        return false;
      } finally {
        setIsSaving(false);
      }
    };

    // Helper to get field error
    const getFieldError = (fieldName: string): string => errors[fieldName] || '';

    return (
      <>
        <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
          Refer to{' '}
          <Link
            href="https://docs.pipeshub.com/business/connectors/sharepoint"
            target="_blank"
            rel="noopener"
          >
            the documentation
          </Link>{' '}
          for more information.
        </Alert>
        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <>
            {/* Header with Edit button when configured */}
            {isConfigured && (
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  mb: 3,
                }}
              >
                <Typography variant="h6">SharePoint Configuration</Typography>

                {!formEditMode ? (
                  !isEnabled ? (
                    <Button
                      variant="contained"
                      startIcon={<Iconify icon={editOutlineIcon} width={18} height={18} />}
                      onClick={handleEnterEditMode}
                    >
                      Edit Configuration
                    </Button>
                  ) : (
                    <Tooltip title="Disable the connector before editing it" placement="top">
                      <span>
                        <Button
                          variant="contained"
                          startIcon={<Iconify icon={editOutlineIcon} width={18} height={18} />}
                          disabled={isEnabled}
                          onClick={handleEnterEditMode}
                        >
                          Edit Configuration
                        </Button>
                      </span>
                    </Tooltip>
                  )
                ) : (
                  <Stack direction="row" spacing={1}>
                    <Button
                      variant="outlined"
                      startIcon={<Iconify icon={closeOutlineIcon} width={18} height={18} />}
                      onClick={handleCancelEdit}
                      color="inherit"
                    >
                      Cancel
                    </Button>
                    <Button
                      variant="contained"
                      startIcon={<Iconify icon={saveOutlineIcon} width={18} height={18} />}
                      onClick={handleSave}
                      color="primary"
                    >
                      Save Changes
                    </Button>
                  </Stack>
                )}
              </Box>
            )}

            {saveError && (
              <Alert
                severity="error"
                sx={{
                  mb: 3,
                  borderRadius: 1,
                }}
              >
                {saveError}
              </Alert>
            )}

            {saveSuccess && (
              <Alert
                severity="success"
                sx={{
                  mb: 3,
                  borderRadius: 1,
                }}
              >
                Configuration saved successfully! You can now use OAuth2 to authenticate with SharePoint.
              </Alert>
            )}

            {/* Tenant URL Display - Only show when admin consent is not granted */}
            {!formData.hasAdminConsent && (
              <Box
                sx={{
                  mb: 3,
                  p: 2,
                  borderRadius: 1,
                  bgcolor: alpha(theme.palette.primary.main, 0.04),
                  border: `1px solid ${alpha(theme.palette.primary.main, 0.15)}`,
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 1,
                }}
              >
                <Iconify
                  icon={linkIcon}
                  width={20}
                  height={20}
                  color={theme.palette.primary.main}
                  style={{ marginTop: 2 }}
                />
                <Box>
                  <Typography variant="subtitle2" color="primary.main" sx={{ mb: 0.5 }}>
                    Redirect URL
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                      Use this URL when configuring your Azure AD App registration.
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{
                      mt: 1,
                      p: 1,
                      borderRadius: 0.5,
                      bgcolor: alpha(theme.palette.background.paper, 0.8),
                      border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                      fontFamily: 'monospace',
                      fontSize: '0.875rem',
                      wordBreak: 'break-all',
                    }}
                  >
                    {tenantUrl}/sharepoint/oauth/callback
                  </Typography>
                </Box>
              </Box>
            )}

            <Box
              sx={{
                mb: 3,
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.info.main, 0.04),
                border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
                display: 'flex',
                alignItems: 'flex-start',
                gap: 1,
              }}
            >
              <Iconify
                icon={infoIcon}
                width={20}
                height={20}
                color={theme.palette.info.main}
                style={{ marginTop: 2 }}
              />
              <Box>
                <Typography variant="body2" color="text.secondary">
                  To configure SharePoint integration, you will need to create an App registration in{' '}
                  <Link
                    href="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
                    target="_blank"
                    rel="noopener"
                    sx={{ fontWeight: 500 }}
                  >
                    Azure Active Directory
                  </Link>
                  . Enter your Application (client) ID, Client Secret, and Directory (tenant) ID from your App registration below.
                </Typography>
                <Typography variant="body2" color="primary.main" sx={{ mt: 1, fontWeight: 500 }}>
                  Important: Add the redirect URI above to your Azure AD app registration and ensure proper Microsoft Graph API permissions are granted.
                </Typography>
              </Box>
            </Box>

            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Azure AD App Registration Credentials
            </Typography>

            <Grid container spacing={2.5}>
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Application (Client) ID"
                  name="clientId"
                  type="text"
                  value={formData.clientId}
                  onChange={handleChange}
                  placeholder="Enter your Azure AD Application (Client) ID"
                  error={Boolean(getFieldError('clientId'))}
                  helperText={
                    getFieldError('clientId') || 'The Application (client) ID from your Azure AD App registration'
                  }
                  required
                  size="small"
                  disabled={isConfigured && !formEditMode}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={editOutlineIcon} width={18} height={18} />
                      </InputAdornment>
                    ),
                  }}
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      '& fieldset': {
                        borderColor: alpha(theme.palette.text.primary, 0.15),
                      },
                    },
                  }}
                />
              </Grid>

              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Directory (Tenant) ID"
                  name="tenantId"
                  type="text"
                  value={formData.tenantId}
                  onChange={handleChange}
                  placeholder="Enter your Azure AD Directory (Tenant) ID"
                  error={Boolean(getFieldError('tenantId'))}
                  helperText={
                    getFieldError('tenantId') || 'The Directory (tenant) ID from your Azure AD tenant'
                  }
                  required
                  size="small"
                  disabled={isConfigured && !formEditMode}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={buildingIcon} width={18} height={18} />
                      </InputAdornment>
                    ),
                  }}
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      '& fieldset': {
                        borderColor: alpha(theme.palette.text.primary, 0.15),
                      },
                    },
                  }}
                />
              </Grid>

              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Client Secret"
                  name="clientSecret"
                  type={showClientSecret ? 'text' : 'password'}
                  value={formData.clientSecret}
                  onChange={handleChange}
                  placeholder="Enter your Azure AD Client Secret"
                  error={Boolean(getFieldError('clientSecret'))}
                  helperText={
                    getFieldError('clientSecret') ||
                    'The Client Secret value from your Azure AD App registration'
                  }
                  required
                  size="small"
                  disabled={isConfigured && !formEditMode}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={lockIcon} width={18} height={18} />
                      </InputAdornment>
                    ),
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          onClick={handleToggleClientSecretVisibility}
                          edge="end"
                          size="small"
                          disabled={isConfigured && !formEditMode}
                        >
                          <Iconify
                            icon={showClientSecret ? eyeOffIcon : eyeIcon}
                            width={18}
                            height={18}
                          />
                        </IconButton>
                      </InputAdornment>
                    ),
                  }}
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      '& fieldset': {
                        borderColor: alpha(theme.palette.text.primary, 0.15),
                      },
                    },
                  }}
                />
              </Grid>

              <Grid item xs={12}>
                <FormControlLabel
                  control={
                    <Checkbox
                      name="hasAdminConsent"
                      checked={formData.hasAdminConsent}
                      onChange={handleCheckboxChange}
                      disabled={isConfigured && !formEditMode}
                    />
                  }
                  label="My Azure AD app has admin consent granted for Microsoft Graph API permissions"
                  sx={{
                    alignItems: 'center',
                    '& .MuiFormControlLabel-label': {
                      fontSize: '0.875rem',
                      color: 'text.secondary',
                      lineHeight: 1.4,
                    },
                  }}
                />
              </Grid>
            </Grid>

            {isSaving && (
              <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
                <CircularProgress size={24} />
              </Box>
            )}
          </>
        )}
      </>
    );
  }
);

export default SharePointConfigForm;