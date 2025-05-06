import keyIcon from '@iconify-icons/mdi/key';
import closeIcon from '@iconify-icons/mdi/close';
import poundIcon from '@iconify-icons/mdi/pound';
import pencilIcon from '@iconify-icons/mdi/pencil';
import serverIcon from '@iconify-icons/mdi/server';
import infoIcon from '@iconify-icons/mdi/information-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Button,
  Tooltip,
  TextField,
  Typography,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import { getQdrantConfig, updateQdrantConfig } from '../utils/services-configuration-service';

interface QdrantConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}
export interface QdrantConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

const QdrantConfigForm = forwardRef<QdrantConfigFormRef, QdrantConfigFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState({
      host: '',
      port: '',
      grpcPort: '',
      apiKey: '',
    });

    const [errors, setErrors] = useState({
      host: '',
      port: '',
      grpcPort: '',
      apiKey: '',
    });

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [isEditing, setIsEditing] = useState(false);
    const [originalData, setOriginalData] = useState({
      host: '',
      port: '',
      grpcPort: '',
      apiKey: '',
    });

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => handleSave(),
    }));

    // Load existing config on mount
    useEffect(() => {
      const fetchConfig = async () => {
        setIsLoading(true);
        try {
          const config = await getQdrantConfig();

          const data = {
            host: config?.host || '',
            port: config?.port?.toString() || '',
            grpcPort: config?.grpcPort?.toString() || '',
            apiKey: config?.apiKey || '',
          };

          setFormData(data);
          setOriginalData(data);
        } catch (error) {
          console.error('Failed to load Qdrant config:', error);
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validate form and notify parent
    useEffect(() => {
      const isValid =
        formData.host.trim() !== '' &&
        formData.port.trim() !== '' &&
        !errors.host &&
        !errors.port &&
        !errors.grpcPort &&
        !errors.apiKey;

      // Only notify about validation if in edit mode and form has changes
      const hasChanges =
        formData.host !== originalData.host ||
        formData.port !== originalData.port ||
        formData.grpcPort !== originalData.grpcPort ||
        formData.apiKey !== originalData.apiKey;

      onValidationChange(isValid && isEditing && hasChanges);
    }, [formData, errors, onValidationChange, isEditing, originalData]);

    // Handle input change
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const { name, value } = e.target;

      setFormData({
        ...formData,
        [name]: value,
      });

      // Validate
      validateField(name, value);
    };

    // Handle edit mode toggle
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - revert to original data
        setFormData(originalData);
        setErrors({
          host: '',
          port: '',
          grpcPort: '',
          apiKey: '',
        });
      }
      setIsEditing(!isEditing);
    };

    // Field validation
    const validateField = (name: string, value: string) => {
      let error = '';

      if ((name === 'host' || name === 'port') && value.trim() === '') {
        error = 'This field is required';
      } else if (
        (name === 'port' || name === 'grpcPort') &&
        value.trim() !== '' &&
        !/^\d+$/.test(value)
      ) {
        error = 'Port must be a number';
      } else if (
        (name === 'port' || name === 'grpcPort') &&
        value.trim() !== '' &&
        (parseInt(value, 10) <= 0 || parseInt(value, 10) > 65535)
      ) {
        error = 'Port must be between 1 and 65535';
      } else if (name === 'apiKey' && value.trim() !== '' && value.length < 16) {
        error = 'API key should be at least 16 characters';
      }

      setErrors({
        ...errors,
        [name]: error,
      });
    };

    // Handle save
    const handleSave = async () => {
      setIsSaving(true);
      setSaveError(null);

      try {
        const response = await updateQdrantConfig({
          host: formData.host,
          port: parseInt(formData.port, 10),
          ...(formData.grpcPort && { grpcPort: parseInt(formData.grpcPort, 10) }),
          apiKey: formData.apiKey,
        });

        const warningHeader = response.data?.warningMessage;
        // Update original data after successful save
        setOriginalData({
          host: formData.host,
          port: formData.port,
          grpcPort: formData.grpcPort,
          apiKey: formData.apiKey,
        });

        // Exit edit mode
        setIsEditing(false);

        if (onSaveSuccess) {
          onSaveSuccess();
        }
        return {
          success: true,
          warning: warningHeader || undefined,
        };
      } catch (error) {
        const errorMessage = 'Failed to save Qdrant configuration';
        setSaveError(error.message || errorMessage);
        console.error('Error saving Qdrant config:', error);
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
              Qdrant connection details. Enter the host, HTTP port, optional gRPC port, and optional
              API key for connecting to the Qdrant service.
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

        <Grid container spacing={2.5}>
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="Host"
              name="host"
              value={formData.host}
              onChange={handleChange}
              placeholder="qdrant-service.example.com"
              error={Boolean(errors.host)}
              helperText={errors.host || 'Hostname or IP address of the Qdrant service'}
              required
              size="small"
              disabled={!isEditing}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={serverIcon} width={18} height={18} />
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
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="HTTP Port"
              name="port"
              value={formData.port}
              onChange={handleChange}
              placeholder="6333"
              error={Boolean(errors.port)}
              helperText={errors.port || 'Port for HTTP communication'}
              required
              size="small"
              type="number"
              disabled={!isEditing}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={poundIcon} width={18} height={18} />
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
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="gRPC Port (optional)"
              name="grpcPort"
              value={formData.grpcPort}
              onChange={handleChange}
              placeholder="50051"
              error={Boolean(errors.grpcPort)}
              helperText={errors.grpcPort || 'Port for gRPC communication (if needed)'}
              size="small"
              type="number"
              disabled={!isEditing}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={poundIcon} width={18} height={18} />
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
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="API Key (optional)"
              name="apiKey"
              value={formData.apiKey}
              onChange={handleChange}
              type="password"
              error={Boolean(errors.apiKey)}
              helperText={
                errors.apiKey || 'Authentication key for the Qdrant service (if required)'
              }
              size="small"
              disabled={!isEditing}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={keyIcon} width={18} height={18} />
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
    );
  }
);

export default QdrantConfigForm;
