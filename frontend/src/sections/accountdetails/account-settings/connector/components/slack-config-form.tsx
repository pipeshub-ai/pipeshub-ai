import { z } from 'zod';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import infoIcon from '@iconify-icons/eva/info-outline';
import hashIcon from '@iconify-icons/eva/hash-outline';
import lockIcon from '@iconify-icons/eva/lock-outline';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import editOutlineIcon from '@iconify-icons/eva/edit-outline';
import saveOutlineIcon from '@iconify-icons/eva/save-outline';
import closeOutlineIcon from '@iconify-icons/eva/close-outline';
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

interface SlackConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  isEnabled?: boolean;
}

export interface SlackConfigFormRef {
  handleSave: () => Promise<boolean>;
}

// Define Zod schema for form validation
const slackConfigSchema = z.object({
  botToken: z.string().min(1, { message: 'Bot Token is required' }).regex(/^xoxb-/, {
    message: 'Bot Token should start with "xoxb-"',
  }),
  signingSecret: z.string().min(1, { message: 'Signing Secret is required' }),
});

type SlackConfigFormData = z.infer<typeof slackConfigSchema>;

const SlackConfigForm = forwardRef<SlackConfigFormRef, SlackConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, isEnabled }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState<SlackConfigFormData>({
      botToken: '',
      signingSecret: '',
    });

    const [errors, setErrors] = useState<Record<string, string>>({});
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [showBotToken, setShowBotToken] = useState(false);
    const [showSigningSecret, setShowSigningSecret] = useState(false);
    const [isConfigured, setIsConfigured] = useState(false);
    const [saveSuccess, setSaveSuccess] = useState(false);
    const [webhookBaseUrl, setWebhookBaseUrl] = useState('');
    const [realTimeUpdatesEnabled, setRealTimeUpdatesEnabled] = useState(false);

    // New state variables for edit mode
    const [formEditMode, setFormEditMode] = useState(false);

    // Store original values for cancel operation
    const [originalState, setOriginalState] = useState({
      botToken: '',
      signingSecret: '',
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
        botToken: formData.botToken,
        signingSecret: formData.signingSecret,
        realTimeUpdatesEnabled,
      });

      setFormEditMode(true);
    };

    // Cancel edit mode and restore original values
    const handleCancelEdit = () => {
      setFormData({
        botToken: originalState.botToken,
        signingSecret: originalState.signingSecret,
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
              service: 'slack',
            },
          });

          if (response.data) {
            const formValues = {
              botToken: response.data.botToken || '',
              signingSecret: response.data.signingSecret || '',
            };

            setFormData(formValues);

            if (Object.prototype.hasOwnProperty.call(response.data, 'realTimeUpdatesEnabled')) {
              setRealTimeUpdatesEnabled(response.data.realTimeUpdatesEnabled);
            }

            // Set original state
            setOriginalState({
              botToken: formValues.botToken,
              signingSecret: formValues.signingSecret,
              realTimeUpdatesEnabled: response.data.realTimeUpdatesEnabled || false,
            });

            setIsConfigured(true);
          }
        } catch (error) {
          console.error('Error fetching Slack config:', error);
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
        slackConfigSchema.parse(formData);
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
    const handleToggleBotTokenVisibility = () => {
      setShowBotToken(!showBotToken);
    };

    const handleToggleSigningSecretVisibility = () => {
      setShowSigningSecret(!showSigningSecret);
    };

    // Handle save
    const handleSave = async (): Promise<boolean> => {
      setIsSaving(true);
      setSaveError(null);
      setSaveSuccess(false);

      try {
        // Validate the form data with Zod before saving
        slackConfigSchema.parse(formData);

        const payload = {
          ...formData,
          realTimeUpdatesEnabled,
        };

        // Send the update request
        await axios.post('/api/v1/connectors/config', payload, {
          params: {
            service: 'slack',
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
          setSaveError('Failed to save Slack configuration');
          console.error('Error saving Slack config:', error);
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
            href="https://docs.pipeshub.com/individual/connectors/slack"
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
                <Typography variant="h6">Slack Configuration</Typography>

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
                Configuration saved successfully! Remember to configure your Slack app with these
                credentials.
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
                  To configure Slack integration, you will need to create a Slack App in the{' '}
                  <Link
                    href="https://api.slack.com/apps"
                    target="_blank"
                    rel="noopener"
                    sx={{ fontWeight: 500 }}
                  >
                    Slack API Dashboard
                  </Link>
                  . Enter your Bot Token and Signing Secret from your Slack App below.
                </Typography>
                <Typography variant="body2" color="primary.main" sx={{ mt: 1, fontWeight: 500 }}>
                  Important: Your Bot Token must have the necessary scopes to read messages and
                  channels.
                </Typography>
              </Box>
            </Box>

            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Slack Credentials
            </Typography>

            <Grid container spacing={2.5}>
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Bot Token"
                  name="botToken"
                  type={showBotToken ? 'text' : 'password'}
                  value={formData.botToken}
                  onChange={handleChange}
                  placeholder="xoxb-your-token"
                  error={Boolean(getFieldError('botToken'))}
                  helperText={
                    getFieldError('botToken') || 'Enter your Bot Token (starts with xoxb-)'
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
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          onClick={handleToggleBotTokenVisibility}
                          edge="end"
                          size="small"
                          disabled={isConfigured && !formEditMode}
                        >
                          <Iconify
                            icon={showBotToken ? eyeOffIcon : eyeIcon}
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
                <TextField
                  fullWidth
                  label="Signing Secret"
                  name="signingSecret"
                  type={showSigningSecret ? 'text' : 'password'}
                  value={formData.signingSecret}
                  onChange={handleChange}
                  placeholder="Enter your Slack Signing Secret"
                  error={Boolean(getFieldError('signingSecret'))}
                  helperText={
                    getFieldError('signingSecret') ||
                    'The signing secret from your Slack App configuration'
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
                          onClick={handleToggleSigningSecretVisibility}
                          edge="end"
                          size="small"
                          disabled={isConfigured && !formEditMode}
                        >
                          <Iconify
                            icon={showSigningSecret ? eyeOffIcon : eyeIcon}
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
                    htmlFor="event-subscriptions-checkbox"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      cursor: isConfigured && !formEditMode ? 'default' : 'pointer',
                    }}
                  >
                    <Box
                      component="input"
                      type="checkbox"
                      id="event-subscriptions-checkbox"
                      checked={realTimeUpdatesEnabled}
                      onChange={handleEventSubscriptionsChange}
                      disabled={isConfigured && !formEditMode}
                      sx={{
                        mr: 1.5,
                        width: 20,
                        height: 20,
                        cursor: isConfigured && !formEditMode ? 'default' : 'pointer',
                      }}
                    />
                    <Typography variant="subtitle2">Enable Event Subscriptions</Typography>
                  </Box>
                </Box>

                <Typography variant="body2" color="text.secondary" sx={{ mb: 2, pl: 3.5 }}>
                  By enabling this feature, you will receive real-time updates for messages and
                  events from your Slack workspace.
                </Typography>

                {realTimeUpdatesEnabled && (
                  <Box sx={{ pl: 3.5 }}>
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
                      <Box>
                        <Typography variant="body2" color="text.secondary">
                          When configuring your Slack App&apos;s Event Subscriptions, set the Request URL
                          as{' '}
                          <Typography component="span" variant="body2" fontWeight="bold">
                            &quot;{webhookBaseUrl}/slack/events&quot;
                          </Typography>
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                          Subscribe to these bot events:
                          <Typography component="ul" variant="body2" sx={{ mt: 0.5, pl: 2 }}>
                            <li>message.channels</li>
                            <li>message.groups</li>
                            <li>channel_created</li>
                            <li>channel_renamed</li>
                          </Typography>
                        </Typography>
                      </Box>
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
  }
);

export default SlackConfigForm;