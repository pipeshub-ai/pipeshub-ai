import { z } from 'zod';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import infoIcon from '@iconify-icons/eva/info-outline';
import lockIcon from '@iconify-icons/eva/lock-outline';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import plusIcon from '@iconify-icons/eva/plus-outline';
import trashIcon from '@iconify-icons/eva/trash-2-outline';
import editOutlineIcon from '@iconify-icons/eva/edit-outline';
import saveOutlineIcon from '@iconify-icons/eva/save-outline';
import closeOutlineIcon from '@iconify-icons/eva/close-outline';
import linkIcon from '@iconify-icons/eva/link-2-outline';
import { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';

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

interface NotionConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  isEnabled?: boolean;
}

export interface NotionConfigFormRef {
  handleSave: () => Promise<boolean>;
}

// Define Zod schema for form validation
const notionConfigSchema = z.object({
  integrationSecrets: z.array(z.string()).min(1, 'At least one integration secret is required'),
});
type NotionConfigFormData = z.infer<typeof notionConfigSchema>;

const NotionConfigForm = forwardRef<NotionConfigFormRef, NotionConfigFormProps>(
  ({ onValidationChange, onSaveSuccess, isEnabled }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState<NotionConfigFormData>({
      integrationSecrets: [''],
    });

    const [errors, setErrors] = useState<Record<string, string>>({});
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [showSecrets, setShowSecrets] = useState<boolean[]>([false]);
    const [isConfigured, setIsConfigured] = useState(false);
    const [saveSuccess, setSaveSuccess] = useState(false);
    const [webhookUrl, setWebhookUrl] = useState('');

    // New state variables for edit mode
    const [formEditMode, setFormEditMode] = useState(false);

    // Store original values for cancel operation
    const [originalState, setOriginalState] = useState<NotionConfigFormData>({
      integrationSecrets: [''],
    });

    // Enable edit mode
    const handleEnterEditMode = () => {
      // Store original values before editing
      setOriginalState({
        ...formData,
      });

      setFormEditMode(true);
    };

    // Cancel edit mode and restore original values
    const handleCancelEdit = () => {
      setFormData({
        ...originalState,
      });

      // Clear any errors
      setErrors({});

      setFormEditMode(false);
      setSaveError(null);
    };

    useEffect(() => {
      const initializeForm = async () => {
        setIsLoading(true);

        try {
          const response = await axios.get('/api/v1/connectors/config', {
            params: {
              service: 'notion',
            },
          });

          if (response.data) {
            const formValues = {
              integrationSecrets: response.data.integrationSecrets || [''],
            };

            setFormData(formValues);
            setShowSecrets(new Array(formValues.integrationSecrets.length).fill(false));

            // Set original state
            setOriginalState({
              ...formValues,
            });

            setIsConfigured(true);
          }
        } catch (error) {
          console.error('Error fetching Notion config:', error);
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
            setWebhookUrl(`${config.url}/api/v1/connectors/notion/webhook`);
          } else {
            // Fallback if no URL from config
            setWebhookUrl(`${window.location.origin}/api/v1/connectors/notion/webhook`);
          }
        } catch (error) {
          console.error('Failed to load connector URL', error);
          // Fallback to window location if we can't get the connector URL
          setWebhookUrl(`${window.location.origin}/api/v1/connectors/notion/webhook`);
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
        notionConfigSchema.parse(formData);
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

    // Handle input change for integration secrets
    const handleSecretChange = (index: number, value: string) => {
      if (!formEditMode && isConfigured) {
        // If trying to edit when not in edit mode, enter edit mode first
        handleEnterEditMode();
        return;
      }

      const updatedSecrets = [...formData.integrationSecrets];
      updatedSecrets[index] = value;

      setFormData({
        ...formData,
        integrationSecrets: updatedSecrets,
      });
    };

    // Add a new secret input field
    const handleAddSecret = () => {
      if (!formEditMode && isConfigured) {
        // If trying to add when not in edit mode, enter edit mode first
        handleEnterEditMode();
        return;
      }

      setFormData({
        ...formData,
        integrationSecrets: [...formData.integrationSecrets, ''],
      });

      // Add corresponding visibility state
      setShowSecrets([...showSecrets, false]);
    };

    // Remove a secret input field
    const handleRemoveSecret = (index: number) => {
      if (!formEditMode && isConfigured) {
        // If trying to remove when not in edit mode, enter edit mode first
        handleEnterEditMode();
        return;
      }

      // Don't remove if it's the only one
      if (formData.integrationSecrets.length <= 1) {
        return;
      }

      const updatedSecrets = [...formData.integrationSecrets];
      updatedSecrets.splice(index, 1);

      setFormData({
        ...formData,
        integrationSecrets: updatedSecrets,
      });

      // Update visibility states
      const updatedShowSecrets = [...showSecrets];
      updatedShowSecrets.splice(index, 1);
      setShowSecrets(updatedShowSecrets);
    };

    // Toggle secret visibility
    const handleToggleSecretVisibility = (index: number) => {
      const updatedShowSecrets = [...showSecrets];
      updatedShowSecrets[index] = !updatedShowSecrets[index];
      setShowSecrets(updatedShowSecrets);
    };

    // Handle save
    const handleSave = async (): Promise<boolean> => {
      setIsSaving(true);
      setSaveError(null);
      setSaveSuccess(false);

      try {
        // Validate the form data with Zod before saving
        notionConfigSchema.parse(formData);

        // Filter out any empty integration secrets
        const filteredSecrets = formData.integrationSecrets.filter(
          (secret) => secret.trim() !== ''
        );

        const payload = {
          integrationSecrets: filteredSecrets,
        };

        // Send the update request
        await axios.post('/api/v1/connectors/config', payload, {
          params: {
            service: 'notion',
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
          setSaveError('Failed to save Notion configuration');
          console.error('Error saving Notion config:', error);
        }
        return false;
      } finally {
        setIsSaving(false);
      }
    };

    return (
      <>
        <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
          Refer to{' '}
          <Link href="https://docs.pipeshub.com/notion" target="_blank" rel="noopener">
            the documentation
          </Link>{' '}
          for more information on setting up Notion integration.
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
                <Typography variant="h6">Notion Configuration</Typography>

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
                Configuration saved successfully!
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
                  To configure Notion integration, you will need to create integrations in{' '}
                  <Link
                    href="https://notion.so/my-integrations"
                    target="_blank"
                    rel="noopener"
                    sx={{ fontWeight: 500, mx: 0.5 }}
                  >
                    Notion Integrations
                  </Link>
                  page. Add your integration secrets below.
                </Typography>
              </Box>
            </Box>

            {/* Webhook URL Information Box */}
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
                <Typography variant="body2" fontWeight={500} color="text.primary" gutterBottom>
                  Set the following webhook URL in your Notion integration settings for real-time
                  updates:
                </Typography>
                <Paper
                  variant="outlined"
                  sx={{
                    p: 1.5,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    bgcolor: alpha(theme.palette.background.default, 0.8),
                    borderColor: alpha(theme.palette.divider, 0.2),
                    borderRadius: 1,
                    mt: 1,
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center' }}>
                    <Iconify
                      icon={linkIcon}
                      width={16}
                      height={16}
                      sx={{ mr: 1, color: 'text.secondary' }}
                    />
                    <Typography variant="body2" component="code" sx={{ fontFamily: 'monospace' }}>
                      {webhookUrl}
                    </Typography>
                  </Box>
                  <Tooltip title="Copy webhook URL">
                    <IconButton
                      size="small"
                      onClick={() => {
                        navigator.clipboard.writeText(webhookUrl);
                      }}
                    >
                      <Iconify icon="eva:copy-outline" width={16} height={16} />
                    </IconButton>
                  </Tooltip>
                </Paper>
              </Box>
            </Box>

            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Integration Secrets
            </Typography>

            {formData.integrationSecrets.map((secret, index) => (
              <Box key={index} sx={{ mb: 2 }}>
                <Grid container spacing={1} alignItems="center">
                  <Grid item xs>
                    <TextField
                      fullWidth
                      label={`Notion Integration Secret ${index + 1}`}
                      type={showSecrets[index] ? 'text' : 'password'}
                      value={secret}
                      onChange={(e) => handleSecretChange(index, e.target.value)}
                      placeholder="Enter your Notion integration secret"
                      error={Boolean(errors[`integrationSecrets.${index}`])}
                      helperText={errors[`integrationSecrets.${index}`] || ''}
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
                              onClick={() => handleToggleSecretVisibility(index)}
                              edge="end"
                              size="small"
                              disabled={isConfigured && !formEditMode}
                            >
                              <Iconify
                                icon={showSecrets[index] ? eyeOffIcon : eyeIcon}
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
                  <Grid item>
                    <IconButton
                      color="error"
                      onClick={() => handleRemoveSecret(index)}
                      disabled={
                        (isConfigured && !formEditMode) || formData.integrationSecrets.length <= 1
                      }
                      size="small"
                      sx={{
                        borderRadius: 1,
                        border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
                        '&:hover': {
                          bgcolor: alpha(theme.palette.error.main, 0.08),
                        },
                      }}
                    >
                      <Iconify icon={trashIcon} width={18} height={18} />
                    </IconButton>
                  </Grid>
                </Grid>
              </Box>
            ))}

            <Button
              variant="outlined"
              color="primary"
              startIcon={<Iconify icon={plusIcon} width={18} height={18} />}
              onClick={handleAddSecret}
              disabled={isConfigured && !formEditMode}
              sx={{ mt: 1, mb: 3 }}
              size="small"
            >
              Add Another Integration Secret
            </Button>

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

export default NotionConfigForm;
