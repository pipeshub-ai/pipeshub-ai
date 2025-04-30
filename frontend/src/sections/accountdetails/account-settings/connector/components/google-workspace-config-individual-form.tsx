import { z } from 'zod';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import infoIcon from '@iconify-icons/eva/info-outline';
import hashIcon from '@iconify-icons/eva/hash-outline';
import lockIcon from '@iconify-icons/eva/lock-outline';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import uploadIcon from '@iconify-icons/eva/upload-outline';
import editOutlineIcon from '@iconify-icons/eva/edit-outline';
import saveOutlineIcon from '@iconify-icons/eva/save-outline';
import fileTextIcon from '@iconify-icons/mdi/file-text-outline';
import closeOutlineIcon from '@iconify-icons/eva/close-outline';
import { useRef, useState, useEffect, forwardRef, useCallback, useImperativeHandle } from 'react';

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

interface GoogleWorkspaceConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  isEnabled?: boolean;
}

export interface GoogleWorkspaceConfigFormRef {
  handleSave: () => Promise<boolean>;
}

const getRedirectUris = async () => {
  // Get the current window URL without hash and search parameters
  const currentUrl = new URL(window.location.href);
  currentUrl.hash = '';
  currentUrl.search = '';
  const currentWindowLocation = currentUrl.toString();

  // Get the frontend URL from the backend
  try {
    const response = await axios.get(`/api/v1/configurationManager/frontendPublicUrl`);
    const frontendBaseUrl = response.data.url;
    // Ensure the URL ends with a slash if needed
    const frontendUrl = frontendBaseUrl.endsWith('/')
      ? `${frontendBaseUrl}account/individual/settings/connector/googleWorkspace`
      : `${frontendBaseUrl}/account/individual/settings/connector/googleWorkspace`;

    return {
      currentWindowLocation,
      recommendedRedirectUri: frontendUrl,
      urisMismatch: currentWindowLocation !== frontendUrl,
    };
  } catch (error) {
    console.error('Error fetching frontend URL:', error);
    return {
      currentWindowLocation,
      recommendedRedirectUri: currentWindowLocation,
      urisMismatch: false,
    };
  }
};

// Define Zod schema for form validation
const googleWorkspaceConfigSchema = z.object({
  clientId: z.string().min(1, { message: 'Client ID is required' }),
  clientSecret: z.string().min(1, { message: 'Client Secret is required' }),
  redirectUri: z.string().url({ message: 'Please enter a valid URL' }),
});

type GoogleWorkspaceConfigFormData = z.infer<typeof googleWorkspaceConfigSchema>;

const GoogleWorkspaceConfigForm = forwardRef<
  GoogleWorkspaceConfigFormRef,
  GoogleWorkspaceConfigFormProps
>(({ onValidationChange, onSaveSuccess, isEnabled }, ref) => {
  const theme = useTheme();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [formData, setFormData] = useState<GoogleWorkspaceConfigFormData>({
    clientId: '',
    clientSecret: '',
    redirectUri: '', // Will be set after fetching
  });

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(true); // Start with loading true
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showClientSecret, setShowClientSecret] = useState(false);
  const [isConfigured, setIsConfigured] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileUploadError, setFileUploadError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [webhookBaseUrl, setWebhookBaseUrl] = useState('');
  const [enableRealTimeUpdates, setEnableRealTimeUpdates] = useState(false);
  const [topicName, setTopicName] = useState('');
  const [topicNameError, setTopicNameError] = useState<string | null>(null);
  const [redirectUris, setRedirectUris] = useState<{
    currentWindowLocation: string;
    recommendedRedirectUri: string;
    urisMismatch: boolean;
  } | null>(null);

  // New state variables for edit mode
  const [formEditMode, setFormEditMode] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  // Store original values for cancel operation
  const [originalState, setOriginalState] = useState({
    clientId: '',
    clientSecret: '',
    redirectUri: '',
    topicName: '',
    enableRealTimeUpdates: false,
    fileName: null as string | null,
  });

  const handleRealTimeUpdatesChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (!formEditMode && isConfigured) {
      // If trying to change when not in edit mode, enter edit mode first
      handleEnterEditMode();
      return;
    }

    const { checked } = event.target;
    setEnableRealTimeUpdates(checked);

    if (!checked) {
      // Clear topic name error when disabling
      setTopicNameError(null);
    } else {
      // Validate topic name when enabling
      validateTopicName(topicName);
    }
  };

  // Handle topic name change
  const handleTopicNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!formEditMode && isConfigured) {
      // If trying to change when not in edit mode, enter edit mode first
      handleEnterEditMode();
      return;
    }

    const name = e.target.value;
    setTopicName(name);
    validateTopicName(name);
  };

  const validateTopicName = useCallback(
    (name: string): boolean => {
      if (enableRealTimeUpdates && !name.trim()) {
        setTopicNameError('Topic name is required when real-time updates are enabled');
        return false;
      }

      setTopicNameError(null);
      return true;
    },
    [enableRealTimeUpdates]
  );

  // Enable edit mode
  const handleEnterEditMode = () => {
    // Store original values before editing
    setOriginalState({
      clientId: formData.clientId,
      clientSecret: formData.clientSecret,
      redirectUri: formData.redirectUri,
      topicName,
      enableRealTimeUpdates,
      fileName,
    });

    setFormEditMode(true);
  };

  // Cancel edit mode and restore original values
  const handleCancelEdit = () => {
    setFormData({
      clientId: originalState.clientId,
      clientSecret: originalState.clientSecret,
      redirectUri: originalState.redirectUri,
    });
    setTopicName(originalState.topicName);
    setEnableRealTimeUpdates(originalState.enableRealTimeUpdates);
    setFileName(originalState.fileName);

    // Clear any errors
    setErrors({});
    setTopicNameError(null);
    setFileUploadError(null);

    setFormEditMode(false);
    setSaveError(null);
  };

  useEffect(() => {
    const initializeForm = async () => {
      setIsLoading(true);
      // Get redirect URIs info
      const uris = await getRedirectUris();
      setRedirectUris(uris);

      // Initialize form with recommended redirect URI
      setFormData((prev) => ({
        ...prev,
        redirectUri: uris.recommendedRedirectUri,
      }));

      // Then fetch existing config
      try {
        const response = await axios.get('/api/v1/connectors/config', {
          params: {
            service: 'googleWorkspace',
          },
        });

        if (response.data) {
          const formValues = {
            clientId: response.data.googleClientId || '',
            clientSecret: response.data.googleClientSecret || '',
            redirectUri: uris.recommendedRedirectUri,
          };

          setFormData(formValues);

          if (Object.prototype.hasOwnProperty.call(response.data, 'enableRealTimeUpdates')) {
            setEnableRealTimeUpdates(response.data.enableRealTimeUpdates);

            if (response.data.topicName) {
              setTopicName(response.data.topicName);
            }
          }

          // Set original state
          setOriginalState({
            clientId: formValues.clientId,
            clientSecret: formValues.clientSecret,
            redirectUri: formValues.redirectUri,
            topicName: response.data.topicName || '',
            enableRealTimeUpdates: response.data.enableRealTimeUpdates || false,
            fileName: 'google-workspace-credentials.json',
          });

          setIsConfigured(true);
          setFileName('google-workspace-credentials.json');
        }
      } catch (error) {
        console.error('Error fetching Google Workspace config:', error);
        setSaveError('Failed to fetch configuration.');
      } finally {
        setIsLoading(false);
      }
    };

    initializeForm();
  }, []);

  useEffect(() => {
    const fetchConnectorUrl = async () => {
      try {
        const config = await getConnectorPublicUrl();
        if (config?.url) {
          setWebhookBaseUrl(config.url);
        }
      } catch (error) {
        console.error('Failed to load connector URL', error);
        // Fallback to window location if we can't get the connector URL
        setWebhookBaseUrl(window.location.origin);
      }
    };

    fetchConnectorUrl();
  }, []);

  // Expose the handleSave method to the parent component
  useImperativeHandle(ref, () => ({
    handleSave,
  }));

  // Validate form using Zod and notify parent
  useEffect(() => {
    try {
      // Parse the data with zod schema
      googleWorkspaceConfigSchema.parse(formData);
      setErrors({});

      // Also validate topic name if real-time updates are enabled
      const isTopicValid = !enableRealTimeUpdates || validateTopicName(topicName);

      onValidationChange(isTopicValid);
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
  }, [formData, enableRealTimeUpdates, topicName, onValidationChange, validateTopicName]);

  // Handle input change
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!formEditMode && isConfigured) {
      // If trying to edit when not in edit mode, enter edit mode first
      handleEnterEditMode();
      return;
    }

    const { name, value } = e.target;

    // Don't update redirectUri when user tries to change it
    if (name === 'redirectUri') return;

    setFormData({
      ...formData,
      [name]: value,
    });
  };

  // Handle file upload click
  const handleUploadClick = () => {
    if (!formEditMode && isConfigured) {
      // If trying to upload when not in edit mode, enter edit mode first
      handleEnterEditMode();
      return;
    }

    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  // Handle file selection
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setFileName(file.name);
    setFileUploadError(null);

    // Read the file
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        if (event.target?.result) {
          const jsonContent = JSON.parse(event.target.result as string);

          // Keep the current redirectUri
          const currentRedirectUri = formData.redirectUri;

          // Check if the JSON has the required fields
          if (jsonContent.web) {
            // Google OAuth credentials format
            const { client_id, client_secret } = jsonContent.web;

            setFormData({
              clientId: client_id || '',
              clientSecret: client_secret || '',
              redirectUri: currentRedirectUri, // Keep the current redirectUri
            });
          } else if (jsonContent.installed) {
            // Service account credentials format
            const { client_id, client_secret } = jsonContent.installed;

            setFormData({
              clientId: client_id || '',
              clientSecret: client_secret || '',
              redirectUri: currentRedirectUri, // Keep the current redirectUri
            });
          } else if (jsonContent.clientId) {
            // Direct format
            setFormData({
              clientId: jsonContent.clientId || '',
              clientSecret: jsonContent.clientSecret || '',
              redirectUri: currentRedirectUri, // Keep the current redirectUri
            });
          } else {
            setFileUploadError('Invalid JSON format. Could not find client credentials.');
          }
        }
      } catch (error) {
        console.error('Error parsing JSON file:', error);
        setFileUploadError('Error parsing JSON file. Please ensure it is a valid JSON.');
      }
    };

    reader.readAsText(file);
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
      googleWorkspaceConfigSchema.parse(formData);

      const payload = {
        ...formData,
        enableRealTimeUpdates,
        topicName: enableRealTimeUpdates ? topicName : '',
      };

      if (enableRealTimeUpdates && !validateTopicName(topicName)) {
        setIsSaving(false);
        setSaveError('Please enter a valid topic name for real-time updates');
        return false;
      }
      // Send the update request
      await axios.post('/api/v1/connectors/config', payload, {
        params: {
          service: 'googleWorkspace',
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
        setSaveError('Failed to save Google Workspace configuration');
        console.error('Error saving Google Workspace config:', error);
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
          href="https://docs.pipeshub.com/individual/connectors/googleWorkspace"
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
              sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}
            >
              <Typography variant="h6">Google Workspace Configuration</Typography>

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
              Configuration saved successfully! Remember to configure this redirect URI in your
              Google Cloud Console.
            </Alert>
          )}

          {redirectUris?.urisMismatch && (
            <Alert
              severity="warning"
              sx={{
                mb: 3,
                borderRadius: 1,
              }}
            >
              <Typography variant="body2" sx={{ mb: 1 }}>
                Redirect URI mismatch detected! Using the recommended URI from backend
                configuration.
              </Typography>
              <Typography variant="caption" component="div">
                Current window location: {redirectUris.currentWindowLocation}
              </Typography>
              <Typography variant="caption" component="div">
                Recommended redirect URI: {redirectUris.recommendedRedirectUri}
              </Typography>
            </Alert>
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
                To configure Google Workspace integration, you will need to create OAuth 2.0
                credentials in the{' '}
                <Link
                  href="https://console.cloud.google.com/apis/credentials"
                  target="_blank"
                  rel="noopener"
                  sx={{ fontWeight: 500 }}
                >
                  Google Cloud Console
                </Link>
                . You can either upload your JSON credentials file or enter the details manually.
              </Typography>
              <Typography variant="body2" color="primary.main" sx={{ mt: 1, fontWeight: 500 }}>
                Important: You must set the redirect URI in your Google Cloud Console to exactly
                match: {formData.redirectUri}
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
                <Typography
                  component="span"
                  variant="body2"
                  color="primary.main"
                  sx={{ fontWeight: 500 }}
                >
                  Redirect URI:
                </Typography>{' '}
                {formData.redirectUri}
              </Typography>
            </Box>
          </Box>

          {/* File Upload Section */}
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Upload Credentials JSON
            </Typography>

            <Paper
              variant="outlined"
              sx={{
                p: 2,
                display: 'flex',
                flexDirection: { xs: 'column', sm: 'row' },
                alignItems: 'center',
                justifyContent: 'space-between',
                borderColor: alpha(theme.palette.primary.main, 0.2),
                borderStyle: 'dashed',
                borderRadius: 1,
                bgcolor: alpha(theme.palette.primary.main, 0.02),
                gap: 2,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 40,
                    height: 40,
                    borderRadius: '8px',
                    bgcolor: alpha(theme.palette.primary.main, 0.08),
                  }}
                >
                  <Iconify
                    icon={fileTextIcon}
                    width={24}
                    height={24}
                    color={theme.palette.primary.main}
                  />
                </Box>

                <Box>
                  <Typography variant="subtitle2">{fileName || 'No file selected'}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Upload your Google Cloud OAuth credentials JSON file
                  </Typography>
                </Box>
              </Box>

              <Button
                variant="outlined"
                size="small"
                onClick={handleUploadClick}
                startIcon={<Iconify icon={uploadIcon} width={18} height={18} />}
                disabled={isConfigured && !formEditMode}
                sx={{
                  minWidth: 120,
                  flexShrink: 0,
                }}
              >
                Upload JSON
              </Button>

              <input
                ref={fileInputRef}
                type="file"
                accept="application/json"
                onChange={handleFileChange}
                style={{ display: 'none' }}
              />
            </Paper>

            {fileUploadError && (
              <Typography variant="caption" color="error" sx={{ mt: 1, display: 'block' }}>
                {fileUploadError}
              </Typography>
            )}
          </Box>

          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
            Or Enter Details Manually
          </Typography>

          <Grid container spacing={2.5}>
            <Grid item xs={12}>
              <TextField
                fullWidth
                label="Client ID"
                name="clientId"
                value={formData.clientId}
                onChange={handleChange}
                placeholder="Enter your Google Client ID"
                error={Boolean(getFieldError('clientId'))}
                helperText={
                  getFieldError('clientId') || 'The OAuth 2.0 client ID from Google Cloud Console'
                }
                required
                size="small"
                disabled={isConfigured && !formEditMode}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify icon={hashIcon} width={18} height={18} />
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
                placeholder="Enter your Google Client Secret"
                error={Boolean(getFieldError('clientSecret'))}
                helperText={
                  getFieldError('clientSecret') ||
                  'The OAuth 2.0 client secret from Google Cloud Console'
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
          <Box sx={{ mb: 3, mt: 3 }}>
            <Paper
              variant="outlined"
              sx={{
                p: 2.5,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.background.default, 0.8),
                borderColor: alpha(theme.palette.divider, 0.2),
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'flex-start', mb: 1.5 }}>
                <Box
                  component="label"
                  htmlFor="realtime-updates-checkbox"
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    cursor: isConfigured && !formEditMode ? 'default' : 'pointer',
                  }}
                >
                  <Box
                    component="input"
                    type="checkbox"
                    id="realtime-updates-checkbox"
                    checked={enableRealTimeUpdates}
                    onChange={handleRealTimeUpdatesChange}
                    disabled={isConfigured && !formEditMode}
                    sx={{
                      mr: 1.5,
                      width: 20,
                      height: 20,
                      cursor: isConfigured && !formEditMode ? 'default' : 'pointer',
                    }}
                  />
                  <Typography variant="subtitle2">Enable Real-time Gmail Updates</Typography>
                </Box>
              </Box>

              <Typography variant="body2" color="text.secondary" sx={{ mb: 2, pl: 3.5 }}>
                By enabling this feature, you will receive real-time updates for new emails in your
                Google Workspace. This requires a valid Google Pub/Sub topic name.
              </Typography>

              {enableRealTimeUpdates && (
                <Box sx={{ pl: 3.5 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Google Pub/Sub Topic Name
                  </Typography>
                  <TextField
                    fullWidth
                    required
                    value={topicName}
                    onChange={handleTopicNameChange}
                    placeholder="projects/your-project/topics/your-topic"
                    error={!!topicNameError}
                    disabled={isConfigured && !formEditMode}
                    helperText={
                      topicNameError ||
                      'Enter the Google Pub/Sub topic that will receive Gmail notifications'
                    }
                    size="small"
                    sx={{ mb: 2 }}
                  />

                  <Box
                    sx={{
                      p: 2,
                      mt: 1,
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
                    <Typography variant="body2" color="text.secondary">
                      When creating your Pub/Sub topic, set the endpoint URL as{' '}
                      <Typography component="span" variant="body2" fontWeight="bold">
                        &quot;{webhookBaseUrl}/gmail/webhook&quot;
                      </Typography>
                    </Typography>
                  </Box>
                </Box>
              )}
            </Paper>
          </Box>

          {isSaving && (
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
              <CircularProgress size={24} />
            </Box>
          )}
        </>
      )}
    </>
  );
});

export default GoogleWorkspaceConfigForm;
