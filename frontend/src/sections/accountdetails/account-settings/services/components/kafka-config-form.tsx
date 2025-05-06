import type { SelectChangeEvent } from '@mui/material';

import lockIcon from '@iconify-icons/mdi/lock';
import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';
import accountIcon from '@iconify-icons/mdi/account';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import serverIcon from '@iconify-icons/mdi/server-network';
import infoIcon from '@iconify-icons/mdi/information-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Alert,
  Paper,
  Button,
  Select,
  Switch,
  Tooltip,
  MenuItem,
  Collapse,
  TextField,
  Typography,
  IconButton,
  InputLabel,
  FormControl,
  InputAdornment,
  FormHelperText,
  CircularProgress,
  FormControlLabel,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import { getKafkaConfig, updateKafkaConfig } from '../utils/services-configuration-service';

interface KafkaConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}

// Define the save result interface
interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface KafkaConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

export interface KafkaConfig {
  brokers: string[];
  sasl?: {
    mechanism: 'plain' | 'scram-sha-256' | 'scram-sha-512';
    username: string;
    password: string;
  };
}

type SaslMechanism = 'plain' | 'scram-sha-256' | 'scram-sha-512';

const KafkaConfigForm = forwardRef<KafkaConfigFormRef, KafkaConfigFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState({
      brokers: [] as string[],
      currentBroker: '',
      saslEnabled: false,
      sasl: {
        mechanism: 'plain' as SaslMechanism,
        username: '',
        password: '',
      },
    });

    const [errors, setErrors] = useState({
      brokers: '',
      currentBroker: '',
      saslUsername: '',
      saslPassword: '',
    });

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [isEditing, setIsEditing] = useState(false);
    const [originalData, setOriginalData] = useState({
      brokers: [] as string[],
      saslEnabled: false,
      sasl: {
        mechanism: 'plain' as SaslMechanism,
        username: '',
        password: '',
      },
    });

    // Expose the handleSave method to the parent component with enhanced return type
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => handleSave(),
    }));

    // Load existing config on mount
    useEffect(() => {
      const fetchConfig = async () => {
        setIsLoading(true);
        try {
          const config = await getKafkaConfig();

          const data = {
            brokers: Array.isArray(config?.brokers) ? [...config.brokers] : [],
            currentBroker: '',
            saslEnabled: Boolean(config?.sasl),
            sasl: {
              mechanism: config?.sasl?.mechanism || 'plain',
              username: config?.sasl?.username || '',
              password: config?.sasl?.password || '',
            },
          };

          setFormData(data);
          setOriginalData({
            brokers: data.brokers,
            saslEnabled: data.saslEnabled,
            sasl: {
              mechanism: data.sasl.mechanism,
              username: data.sasl.username,
              password: data.sasl.password,
            },
          });
        } catch (error) {
          console.error('Failed to load Kafka config:', error);
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validate form and notify parent
    useEffect(() => {
      let isValid = formData.brokers.length > 0 && !errors.brokers;

      // Add SASL validation if enabled
      if (formData.saslEnabled) {
        isValid =
          isValid &&
          formData.sasl.username.trim() !== '' &&
          formData.sasl.password.trim() !== '' &&
          !errors.saslUsername &&
          !errors.saslPassword;
      }

      // Check if form has changes
      const brokersChanged =
        JSON.stringify(formData.brokers) !== JSON.stringify(originalData.brokers);
      const saslEnabledChanged = formData.saslEnabled !== originalData.saslEnabled;
      const saslChanged =
        formData.saslEnabled &&
        (formData.sasl.mechanism !== originalData.sasl.mechanism ||
          formData.sasl.username !== originalData.sasl.username ||
          formData.sasl.password !== originalData.sasl.password);

      const hasChanges = brokersChanged || saslEnabledChanged || saslChanged;

      onValidationChange(isValid && isEditing && hasChanges);
    }, [formData, errors, onValidationChange, isEditing, originalData]);

    // Handle input change for the current broker field
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const { name, value } = e.target;

      if (name === 'currentBroker') {
        setFormData({
          ...formData,
          currentBroker: value,
        });
        validateCurrentBroker(value);
      } else if (name === 'saslUsername' || name === 'saslPassword') {
        setFormData({
          ...formData,
          sasl: {
            ...formData.sasl,
            [name === 'saslUsername' ? 'username' : 'password']: value,
          },
        });
        validateSaslField(name, value);
      }
    };

    // Handle select change for SASL mechanism
    const handleMechanismChange = (event: SelectChangeEvent) => {
      const value = event.target.value as SaslMechanism;
      setFormData({
        ...formData,
        sasl: {
          ...formData.sasl,
          mechanism: value,
        },
      });
    };

    // Handle SASL toggle
    const handleSaslToggle = (e: React.ChangeEvent<HTMLInputElement>) => {
      setFormData({
        ...formData,
        saslEnabled: e.target.checked,
      });
    };

    // Validate current broker input
    const validateCurrentBroker = (value: string) => {
      let error = '';

      if (value !== '' && !value.includes(':')) {
        error = 'Broker should include host:port format';
      }

      setErrors({
        ...errors,
        currentBroker: error,
      });
    };

    // Validate SASL fields
    const validateSaslField = (name: string, value: string) => {
      let error = '';

      if (value.trim() === '') {
        error = 'This field is required';
      }

      setErrors({
        ...errors,
        [name]: error,
      });
    };

    // Validate brokers array
    const validateBrokers = (brokers: string[]) => {
      const error = brokers.length === 0 ? 'At least one broker is required' : '';
      setErrors({
        ...errors,
        brokers: error,
      });
      return error === '';
    };

    // Validate all SASL fields
    const validateSasl = () => {
      if (!formData.saslEnabled) return true;

      const usernameError = formData.sasl.username.trim() === '' ? 'Username is required' : '';
      const passwordError = formData.sasl.password.trim() === '' ? 'Password is required' : '';

      setErrors({
        ...errors,
        saslUsername: usernameError,
        saslPassword: passwordError,
      });

      return !usernameError && !passwordError;
    };

    // Handle add broker
    const handleAddBroker = () => {
      if (
        formData.currentBroker.trim() !== '' &&
        formData.currentBroker.includes(':') &&
        !formData.brokers.includes(formData.currentBroker.trim())
      ) {
        const newBrokers = [...formData.brokers, formData.currentBroker.trim()];
        setFormData({
          ...formData,
          brokers: newBrokers,
          currentBroker: '',
        });
        validateBrokers(newBrokers);
      }
    };

    // Handle broker deletion
    const handleDeleteBroker = (brokerToDelete: string) => {
      const newBrokers = formData.brokers.filter((broker) => broker !== brokerToDelete);
      setFormData({
        ...formData,
        brokers: newBrokers,
      });
      validateBrokers(newBrokers);
    };

    // Handle keypress in the broker input field
    const handleKeyPress = (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !errors.currentBroker && formData.currentBroker.trim() !== '') {
        e.preventDefault();
        handleAddBroker();
      }
    };

    // Handle edit mode toggle
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - revert to original data
        setFormData({
          ...formData,
          brokers: [...originalData.brokers],
          currentBroker: '',
          saslEnabled: originalData.saslEnabled,
          sasl: {
            mechanism: originalData.sasl.mechanism,
            username: originalData.sasl.username,
            password: originalData.sasl.password,
          },
        });
        setErrors({
          brokers: '',
          currentBroker: '',
          saslUsername: '',
          saslPassword: '',
        });
      }
      setIsEditing(!isEditing);
    };

    // Handle save with structured return type
    const handleSave = async (): Promise<SaveResult> => {
      // Validate all fields
      const brokersValid = validateBrokers(formData.brokers);
      const saslValid = validateSasl();

      if (!brokersValid || (formData.saslEnabled && !saslValid)) {
        return {
          success: false,
          error:
            formData.saslEnabled && !saslValid
              ? 'SASL credentials are required'
              : 'At least one broker is required',
        };
      }

      setIsSaving(true);
      setSaveError(null);

      try {
        const config: KafkaConfig = {
          brokers: formData.brokers,
        };

        // Add SASL if enabled
        if (formData.saslEnabled) {
          config.sasl = {
            mechanism: formData.sasl.mechanism,
            username: formData.sasl.username,
            password: formData.sasl.password,
          };
        }

        const response = await updateKafkaConfig(config);

        // Check for warnings in the response header
        const warningHeader = response.data?.warningMessage;

        // Update original data after successful save
        setOriginalData({
          brokers: [...formData.brokers],
          saslEnabled: formData.saslEnabled,
          sasl: {
            mechanism: formData.sasl.mechanism,
            username: formData.sasl.username,
            password: formData.sasl.password,
          },
        });

        // Exit edit mode
        setIsEditing(false);

        // Optional success callback
        if (onSaveSuccess) {
          onSaveSuccess();
        }

        // Return success with any warnings
        return {
          success: true,
          warning: warningHeader || undefined,
        };
      } catch (error) {
        const errorMessage = 'Failed to save Kafka configuration';
        setSaveError(error.message || errorMessage);
        console.error('Error saving Kafka config:', error);

        // Return error result
        return {
          success: false,
          error: error.message || errorMessage,
        };
      } finally {
        setIsSaving(false);
      }
    };

    if (isLoading) {
      return (
        <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
          <CircularProgress size={24} />
        </Box>
      );
    }

    return (
      <>
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
              Configure Kafka connection settings. Add brokers in <code>host:port</code> format and
              optionally configure SASL authentication.
            </Typography>
          </Box>
        </Box>

        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
          <Tooltip title="Editing external services is not allowed" arrow>
            <span>
              <Button
                onClick={handleToggleEdit}
                startIcon={<Iconify icon={isEditing ? closeIcon : pencilIcon} />}
                color={isEditing ? 'error' : 'primary'}
                size="small"
                disabled
              >
                {isEditing ? 'Cancel' : 'Edit'}
              </Button>
            </span>
          </Tooltip>
        </Box>

        {/* Broker Configuration Section */}
        <Paper
          elevation={0}
          sx={{
            mb: 2,
            p: 1.5,
            border: `1px solid ${alpha(theme.palette.text.primary, 0.1)}`,
            borderRadius: 1,
          }}
        >
          <Typography variant="subtitle1" sx={{ mb: 1 }}>
            Brokers
          </Typography>

          {formData.brokers.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1, fontStyle: 'italic' }}>
              No brokers configured
            </Typography>
          ) : (
            <Box sx={{ mb: 1 }}>
              {formData.brokers.map((broker, index) => (
                <Box
                  key={index}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    py: 0.5,
                    borderBottom:
                      index < formData.brokers.length - 1
                        ? `1px solid ${alpha(theme.palette.divider, 0.5)}`
                        : 'none',
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center' }}>
                    <Iconify icon={serverIcon} width={16} height={16} sx={{ mr: 0.75 }} />
                    <Typography variant="body2">{broker}</Typography>
                  </Box>
                  {isEditing && (
                    <IconButton
                      onClick={() => handleDeleteBroker(broker)}
                      size="small"
                      color="error"
                      sx={{ p: 0.5 }}
                    >
                      <Iconify icon={deleteIcon} width={18} height={18} />
                    </IconButton>
                  )}
                </Box>
              ))}
            </Box>
          )}

          {errors.brokers && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {errors.brokers}
            </Alert>
          )}

          {isEditing && (
            <Box sx={{ display: 'flex', alignItems: 'flex-start', mt: 1 }}>
              <TextField
                fullWidth
                name="currentBroker"
                value={formData.currentBroker}
                onChange={handleChange}
                onKeyPress={handleKeyPress}
                placeholder="host:port"
                error={Boolean(errors.currentBroker)}
                helperText={errors.currentBroker || ''}
                size="small"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify icon={serverIcon} width={16} height={16} />
                    </InputAdornment>
                  ),
                }}
                sx={{
                  '& .MuiOutlinedInput-root': {
                    '& fieldset': {
                      borderColor: alpha(theme.palette.text.primary, 0.15),
                    },
                  },
                  '& .MuiFormHelperText-root': {
                    mt: 0.5,
                    mb: 0,
                  },
                }}
              />
              <Button
                onClick={handleAddBroker}
                variant="contained"
                size="small"
                disabled={
                  formData.currentBroker.trim() === '' ||
                  Boolean(errors.currentBroker) ||
                  formData.brokers.includes(formData.currentBroker.trim())
                }
                sx={{ ml: 1, minWidth: '80px', height: 40 }}
              >
                Add
              </Button>
            </Box>
          )}
        </Paper>

        {/* SASL Authentication Section */}
        <Paper
          elevation={0}
          sx={{
            mb: 2,
            p: 1.5,
            border: `1px solid ${alpha(theme.palette.text.primary, 0.1)}`,
            borderRadius: 1,
          }}
        >
          <Box
            sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}
          >
            <Typography variant="subtitle1">SASL Authentication</Typography>
            <FormControlLabel
              control={
                <Switch
                  checked={formData.saslEnabled}
                  onChange={handleSaslToggle}
                  disabled={!isEditing}
                  color="primary"
                  size="small"
                />
              }
              label={formData.saslEnabled ? 'Enabled' : 'Disabled'}
              sx={{ m: 0 }}
            />
          </Box>

          <Collapse in={formData.saslEnabled}>
            <Grid container spacing={2}>
              <Grid item xs={12} md={4}>
                <FormControl fullWidth size="small" disabled={!isEditing}>
                  <InputLabel id="sasl-mechanism-label">Mechanism</InputLabel>
                  <Select
                    labelId="sasl-mechanism-label"
                    value={formData.sasl.mechanism}
                    onChange={handleMechanismChange}
                    label="Mechanism"
                  >
                    <MenuItem value="plain">PLAIN</MenuItem>
                    <MenuItem value="scram-sha-256">SCRAM-SHA-256</MenuItem>
                    <MenuItem value="scram-sha-512">SCRAM-SHA-512</MenuItem>
                  </Select>
                  <FormHelperText>The SASL authentication mechanism</FormHelperText>
                </FormControl>
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  fullWidth
                  label="Username"
                  name="saslUsername"
                  value={formData.sasl.username}
                  onChange={handleChange}
                  error={Boolean(errors.saslUsername)}
                  helperText={errors.saslUsername || ''}
                  required
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={accountIcon} width={18} height={18} />
                      </InputAdornment>
                    ),
                  }}
                />
              </Grid>
              <Grid item xs={12} md={4}>
                <TextField
                  fullWidth
                  label="Password"
                  name="saslPassword"
                  type="password"
                  value={formData.sasl.password}
                  onChange={handleChange}
                  error={Boolean(errors.saslPassword)}
                  helperText={errors.saslPassword || ''}
                  required
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={lockIcon} width={18} height={18} />
                      </InputAdornment>
                    ),
                  }}
                />
              </Grid>
            </Grid>
          </Collapse>

          {!formData.saslEnabled && !isEditing && (
            <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
              No authentication configured
            </Typography>
          )}
        </Paper>

        {isSaving && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
            <CircularProgress size={24} />
          </Box>
        )}
      </>
    );
  }
);

export default KafkaConfigForm;
