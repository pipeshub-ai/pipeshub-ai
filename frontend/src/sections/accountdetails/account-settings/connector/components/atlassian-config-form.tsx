import { z } from 'zod';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import infoIcon from '@iconify-icons/eva/info-outline';
import lockIcon from '@iconify-icons/eva/lock-outline';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import editOutlineIcon from '@iconify-icons/eva/edit-outline';
import saveOutlineIcon from '@iconify-icons/eva/save-outline';
import closeOutlineIcon from '@iconify-icons/eva/close-outline';
import linkIcon from '@iconify-icons/eva/link-outline';
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
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import { getConnectorPublicUrl } from '../../services/utils/services-configuration-service';

interface AtlassianConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  isEnabled?: boolean;
}

export interface AtlassianConfigFormRef {
  handleSave: () => Promise<boolean>;
}

// Define Zod schema for form validation
const atlassianConfigSchema = z.object({
  clientId: z.string().min(1, { message: 'Client ID is required' }),
  clientSecret: z.string().min(1, { message: 'Client Secret is required' }),
});

type AtlassianConfigFormData = z.infer<typeof atlassianConfigSchema>;

const AtlassianConfigForm = forwardRef<AtlassianConfigFormRef, AtlassianConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, isEnabled }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState<AtlassianConfigFormData>({
      clientId: '',
      clientSecret: '',
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
        realTimeUpdatesEnabled,
      });

      setFormEditMode(true);
    };

    // Cancel edit mode and restore original values
    const handleCancelEdit = () => {
      setFormData({
        clientId: originalState.clientId,
        clientSecret: originalState.clientSecret,
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
              service: 'atlassian',
            },
          });

          if (response.data) {
            const formValues = {
              clientId: response.data.clientId || '',
              clientSecret: response.data.clientSecret || '',
            };

            setFormData(formValues);

            if (Object.prototype.hasOwnProperty.call(response.data, 'realTimeUpdatesEnabled')) {
              setRealTimeUpdatesEnabled(response.data.realTimeUpdatesEnabled);
            }

            // Set original state
            setOriginalState({
              clientId: formValues.clientId,
              clientSecret: formValues.clientSecret,
              realTimeUpdatesEnabled: response.data.realTimeUpdatesEnabled || false,
            });

            setIsConfigured(true);
          }
        } catch (error) {
          console.error('Error fetching Atlassian config:', error);
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
        atlassianConfigSchema.parse(formData);
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
        atlassianConfigSchema.parse(formData);

        const payload = {
          ...formData,
          realTimeUpdatesEnabled,
        };

        // Send the update request
        await axios.post('/api/v1/connectors/config', payload, {
          params: {
            service: 'atlassian',
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
          setSaveError('Failed to save Atlassian configuration');
          console.error('Error saving Atlassian config:', error);
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
            href="https://docs.pipeshub.com/individual/connectors/atlassian"
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
                <Typography variant="h6">Atlassian Configuration</Typography>

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
                Configuration saved successfully! You can now use OAuth2 to authenticate with Atlassian.
              </Alert>
            )}

            {/* Tenant URL Display */}
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
                    Use this URL when configuring your Atlassian OAuth2 App.
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
                  {tenantUrl}/atlassian/oauth/callback
                </Typography>
              </Box>
            </Box>

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
                  To configure Atlassian integration, you will need to create an OAuth2 App in the{' '}
                  <Link
                    href="https://developer.atlassian.com/console/myapps/"
                    target="_blank"
                    rel="noopener"
                    sx={{ fontWeight: 500 }}
                  >
                    Atlassian Developer Console
                  </Link>
                  . Enter your Client ID and Client Secret from your OAuth2 App below.
                </Typography>
                <Typography variant="body2" color="primary.main" sx={{ mt: 1, fontWeight: 500 }}>
                  Important: Configure the redirect URI in your Atlassian app to point to your application.
                </Typography>
              </Box>
            </Box>

            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              OAuth2 Credentials
            </Typography>

            <Grid container spacing={2.5}>
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Client ID"
                  name="clientId"
                  type="text"
                  value={formData.clientId}
                  onChange={handleChange}
                  placeholder="Enter your OAuth2 Client ID"
                  error={Boolean(getFieldError('clientId'))}
                  helperText={
                    getFieldError('clientId') || 'The Client ID from your Atlassian OAuth2 App'
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
                  label="Client Secret"
                  name="clientSecret"
                  type={showClientSecret ? 'text' : 'password'}
                  value={formData.clientSecret}
                  onChange={handleChange}
                  placeholder="Enter your OAuth2 Client Secret"
                  error={Boolean(getFieldError('clientSecret'))}
                  helperText={
                    getFieldError('clientSecret') ||
                    'The Client Secret from your Atlassian OAuth2 App configuration'
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

export default AtlassianConfigForm;