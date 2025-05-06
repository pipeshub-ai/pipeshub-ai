import webIcon from '@iconify-icons/mdi/web';
import keyIcon from '@iconify-icons/mdi/key';
import lockIcon from '@iconify-icons/mdi/lock';
import linkIcon from '@iconify-icons/mdi/link';
import closeIcon from '@iconify-icons/mdi/close';
import dbIcon from '@iconify-icons/mdi/database';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import pencilIcon from '@iconify-icons/mdi/pencil';
import folderIcon from '@iconify-icons/mdi/folder';
import bucketIcon from '@iconify-icons/mdi/bucket';
import packageIcon from '@iconify-icons/mdi/package';
import accountIcon from '@iconify-icons/mdi/account';
import shieldIcon from '@iconify-icons/mdi/shield-lock';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import mapMarkerIcon from '@iconify-icons/mdi/map-marker';
import warningIcon from '@iconify-icons/mdi/alert-circle';
import infoIcon from '@iconify-icons/mdi/information-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Alert,
  Select,
  Button,
  Tooltip,
  MenuItem,
  TextField,
  Typography,
  IconButton,
  InputLabel,
  FormControl,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

// Import configuration services
import { getStorageConfig, updateStorageConfig } from '../utils/services-configuration-service';

// Storage types enum
export const storageTypes = {
  LOCAL: 'local',
  S3: 's3',
  AZURE_BLOB: 'azureBlob',
} as const;

// Type for storage type values
type StorageType = (typeof storageTypes)[keyof typeof storageTypes];

interface StorageServiceFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface StorageServiceFormRef {
  handleSave: () => Promise<SaveResult>;
}

const StorageServiceForm = forwardRef<StorageServiceFormRef, StorageServiceFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState({
      storageType: storageTypes.LOCAL as StorageType,
      // Local storage fields
      mountName: '',
      baseUrl: '',
      // S3 fields
      s3AccessKeyId: '',
      s3SecretAccessKey: '',
      s3Region: '',
      s3BucketName: '',
      // Azure Blob fields
      endpointProtocol: 'https',
      accountName: '',
      accountKey: '',
      endpointSuffix: 'core.windows.net',
      containerName: '',
    });

    const [errors, setErrors] = useState({
      mountName: '',
      baseUrl: '',
      s3AccessKeyId: '',
      s3SecretAccessKey: '',
      s3Region: '',
      s3BucketName: '',
      accountName: '',
      accountKey: '',
      endpointSuffix: '',
      containerName: '',
    });

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [isEditing, setIsEditing] = useState(false);
    const [showPassword, setShowPassword] = useState(false);
    const [originalData, setOriginalData] = useState({
      storageType: storageTypes.LOCAL as StorageType,
      mountName: '',
      baseUrl: '',
      s3AccessKeyId: '',
      s3SecretAccessKey: '',
      s3Region: '',
      s3BucketName: '',
      endpointProtocol: 'https',
      accountName: '',
      accountKey: '',
      endpointSuffix: 'core.windows.net',
      containerName: '',
    });
    const [showStorageTypeWarning, setShowStorageTypeWarning] = useState(false);

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => handleSave(),
    }));

    // Load existing config on mount
    useEffect(() => {
      const fetchConfig = async () => {
        setIsLoading(true);
        try {
          const config = await getStorageConfig();

          // Initialize the form data based on the storage type
          const data = {
            storageType: config?.storageType || storageTypes.LOCAL,
            // Local storage fields
            mountName: config?.mountName || '',
            baseUrl: config?.baseUrl || '',
            // S3 fields
            s3AccessKeyId: config?.accessKeyId || '',
            s3SecretAccessKey: config?.secretAccessKey || '',
            s3Region: config?.region || '',
            s3BucketName: config?.bucketName || '',
            // Azure Blob fields
            endpointProtocol: config?.endpointProtocol || 'https',
            accountName: config?.accountName || '',
            accountKey: config?.accountKey || '',
            endpointSuffix: config?.endpointSuffix || 'core.windows.net',
            containerName: config?.containerName || '',
          };

          setFormData(data);
          setOriginalData(data);
        } catch (error) {
          console.error('Failed to load Storage config:', error);
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validate form and notify parent
    useEffect(() => {
      let isValid = false;

      switch (formData.storageType) {
        case storageTypes.LOCAL:
          // Local storage is always valid
          isValid = true;
          break;
        case storageTypes.S3:
          isValid = Boolean(
            formData.s3AccessKeyId &&
              formData.s3SecretAccessKey &&
              formData.s3Region &&
              formData.s3BucketName &&
              !errors.s3AccessKeyId &&
              !errors.s3SecretAccessKey &&
              !errors.s3Region &&
              !errors.s3BucketName
          );
          break;
        case storageTypes.AZURE_BLOB:
          isValid = Boolean(
            formData.accountName &&
              formData.accountKey &&
              formData.containerName &&
              !errors.accountName &&
              !errors.accountKey &&
              !errors.containerName
          );
          break;
        default:
          isValid = false;
      }

      // Only notify about validation if in edit mode and has changes
      const hasChanges = JSON.stringify(formData) !== JSON.stringify(originalData);

      onValidationChange(isValid && isEditing && hasChanges);
    }, [formData, errors, onValidationChange, isEditing, originalData]);

    // Check if storage type has changed and show warning
    useEffect(() => {
      if (isEditing && formData.storageType !== originalData.storageType) {
        setShowStorageTypeWarning(true);
      } else {
        setShowStorageTypeWarning(false);
      }
    }, [formData.storageType, originalData.storageType, isEditing]);

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

    // Handle select change
    const handleSelectChange = (e: any) => {
      const { name, value } = e.target;

      if (name === 'storageType') {
        setFormData({
          ...formData,
          storageType: value as StorageType,
        });
      } else if (name === 'endpointProtocol') {
        setFormData({
          ...formData,
          [name]: value,
        });
      } else {
        setFormData({
          ...formData,
          [name]: value === 'true', // Convert string to boolean
        });
      }
    };

    // Handle edit mode toggle
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - revert to original data
        setFormData(originalData);
        setErrors({
          mountName: '',
          baseUrl: '',
          s3AccessKeyId: '',
          s3SecretAccessKey: '',
          s3Region: '',
          s3BucketName: '',
          accountName: '',
          accountKey: '',
          endpointSuffix: '',
          containerName: '',
        });
      }
      setIsEditing(!isEditing);
    };

    // Field validation
    const validateField = (name: string, value: string) => {
      let error = '';

      // Mount name validation - add minimum length check
      if (name === 'mountName' && value.trim() !== '' && value.trim().length < 5) {
        error = 'Mount name must be at least 5 characters';
      }
      // S3 fields validation
      if (formData.storageType === storageTypes.S3) {
        if (
          (name === 's3AccessKeyId' ||
            name === 's3SecretAccessKey' ||
            name === 's3Region' ||
            name === 's3BucketName') &&
          value.trim() === ''
        ) {
          error = `${name.replace('s3', '')} is required`;
        }
      }

      // Azure Blob fields validation
      if (formData.storageType === storageTypes.AZURE_BLOB) {
        if (
          (name === 'accountName' || name === 'accountKey' || name === 'containerName') &&
          value.trim() === ''
        ) {
          error = `${name} is required`;
        }
      }

      if (name === 'baseUrl' && value.trim() !== '') {
        try {
          // Store the result to avoid "new for side effects" linting error
          const url = new URL(value);
          // We could use the url object here if needed
        } catch (e) {
          error = 'Must be a valid URL';
        }
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
        const configToSave = {
          storageType: formData.storageType,
          // Include only relevant fields based on storage type
          ...(formData.storageType === storageTypes.LOCAL &&
            formData.mountName && { mountName: formData.mountName }),
          ...(formData.storageType === storageTypes.LOCAL &&
            formData.baseUrl && { baseUrl: formData.baseUrl }),
          ...(formData.storageType === storageTypes.S3 && {
            s3AccessKeyId: formData.s3AccessKeyId,
            s3SecretAccessKey: formData.s3SecretAccessKey,
            s3Region: formData.s3Region,
            s3BucketName: formData.s3BucketName,
          }),
          ...(formData.storageType === storageTypes.AZURE_BLOB && {
            endpointProtocol: formData.endpointProtocol,
            accountName: formData.accountName,
            accountKey: formData.accountKey,
            containerName: formData.containerName,
          }),
          ...(formData.storageType === storageTypes.AZURE_BLOB &&
            formData.endpointSuffix && {
              endpointSuffix: formData.endpointSuffix,
            }),
        };

        const response = await updateStorageConfig(configToSave);
        const warningHeader = response.data?.warningMessage;

        // Update original data after successful save
        setOriginalData(formData);

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
        const errorMessage = 'Failed to save Storage configuration';
        setSaveError(error.message || errorMessage);
        console.error('Error saving Storage config:', error);

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
              Configure storage settings for your application data. Select a storage type and
              provide the necessary credentials.
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

        {showStorageTypeWarning && (
          <Alert
            severity="warning"
            sx={{
              mb: 3,
              borderRadius: 1,
              display: 'flex',
              alignItems: 'center',
              bgcolor: alpha(theme.palette.warning.main, 0.1),
              border: `1px solid ${alpha(theme.palette.warning.main, 0.2)}`,
            }}
            icon={<Iconify icon={warningIcon} width={24} height={24} />}
          >
            <Typography variant="body2">
              <strong>Warning:</strong> Changing the storage type will prevent access to data stored
              using the current storage type (
              {originalData.storageType === 'local'
                ? 'Local Storage'
                : originalData.storageType === 's3'
                  ? 'Amazon S3'
                  : 'Azure Blob Storage'}
              ).
            </Typography>
          </Alert>
        )}

        <Grid container spacing={2.5}>
          <Grid item xs={12}>
            <FormControl
              fullWidth
              size="small"
              disabled={!isEditing}
              sx={{
                '& .MuiOutlinedInput-root': {
                  '& fieldset': {
                    borderColor: alpha(theme.palette.text.primary, 0.15),
                  },
                },
              }}
            >
              <InputLabel>Storage Type</InputLabel>
              <Select
                name="storageType"
                value={formData.storageType}
                label="Storage Type"
                onChange={handleSelectChange}
                startAdornment={
                  <InputAdornment position="start">
                    <Iconify icon={dbIcon} width={18} height={18} />
                  </InputAdornment>
                }
              >
                <MenuItem value={storageTypes.LOCAL}>Local Storage</MenuItem>
                <MenuItem value={storageTypes.S3}>Amazon S3</MenuItem>
                <MenuItem value={storageTypes.AZURE_BLOB}>Azure Blob Storage</MenuItem>
              </Select>
            </FormControl>
          </Grid>

          {/* Local Storage Fields */}
          {formData.storageType === storageTypes.LOCAL && (
            <>
              <Grid item xs={12}>
                <Alert severity="info" sx={{ mb: 2 }}>
                  Local storage is configured automatically. Additional options are optional.
                </Alert>
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Mount Name (Optional)"
                  name="mountName"
                  value={formData.mountName}
                  onChange={handleChange}
                  error={Boolean(errors.mountName)}
                  helperText={errors.mountName}
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={folderIcon} width={18} height={18} />
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
                  label="Base URL (Optional)"
                  name="baseUrl"
                  value={formData.baseUrl}
                  onChange={handleChange}
                  placeholder="http://localhost:3000/files"
                  error={Boolean(errors.baseUrl)}
                  helperText={errors.baseUrl || 'e.g., http://localhost:3000/files'}
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={linkIcon} width={18} height={18} />
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
            </>
          )}

          {/* S3 Fields */}
          {formData.storageType === storageTypes.S3 && (
            <>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Access Key ID"
                  name="s3AccessKeyId"
                  value={formData.s3AccessKeyId}
                  onChange={handleChange}
                  error={Boolean(errors.s3AccessKeyId)}
                  helperText={errors.s3AccessKeyId}
                  required
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
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Secret Access Key"
                  name="s3SecretAccessKey"
                  value={formData.s3SecretAccessKey}
                  onChange={handleChange}
                  type={showPassword ? 'text' : 'password'}
                  error={Boolean(errors.s3SecretAccessKey)}
                  helperText={errors.s3SecretAccessKey}
                  required
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={lockIcon} width={18} height={18} />
                      </InputAdornment>
                    ),
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          onClick={() => setShowPassword(!showPassword)}
                          edge="end"
                          size="small"
                          disabled={!isEditing}
                        >
                          <Iconify
                            icon={showPassword ? eyeOffIcon : eyeIcon}
                            width={16}
                            height={16}
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
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Region"
                  name="s3Region"
                  value={formData.s3Region}
                  onChange={handleChange}
                  placeholder="us-east-1"
                  error={Boolean(errors.s3Region)}
                  helperText={errors.s3Region}
                  required
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={mapMarkerIcon} width={18} height={18} />
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
                  label="Bucket Name"
                  name="s3BucketName"
                  value={formData.s3BucketName}
                  onChange={handleChange}
                  error={Boolean(errors.s3BucketName)}
                  helperText={errors.s3BucketName}
                  required
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={bucketIcon} width={18} height={18} />
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
            </>
          )}

          {/* Azure Blob Fields */}
          {formData.storageType === storageTypes.AZURE_BLOB && (
            <>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Account Name"
                  name="accountName"
                  value={formData.accountName}
                  onChange={handleChange}
                  error={Boolean(errors.accountName)}
                  helperText={errors.accountName}
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
                  label="Container Name"
                  name="containerName"
                  value={formData.containerName}
                  onChange={handleChange}
                  error={Boolean(errors.containerName)}
                  helperText={errors.containerName}
                  required
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={packageIcon} width={18} height={18} />
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
                  label="Account Key"
                  name="accountKey"
                  value={formData.accountKey}
                  onChange={handleChange}
                  type={showPassword ? 'text' : 'password'}
                  error={Boolean(errors.accountKey)}
                  helperText={errors.accountKey}
                  required
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={keyIcon} width={18} height={18} />
                      </InputAdornment>
                    ),
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          onClick={() => setShowPassword(!showPassword)}
                          edge="end"
                          size="small"
                          disabled={!isEditing}
                        >
                          <Iconify
                            icon={showPassword ? eyeOffIcon : eyeIcon}
                            width={16}
                            height={16}
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
              <Grid item xs={12} md={6}>
                <FormControl
                  fullWidth
                  size="small"
                  disabled={!isEditing}
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      '& fieldset': {
                        borderColor: alpha(theme.palette.text.primary, 0.15),
                      },
                    },
                  }}
                >
                  <InputLabel>Protocol</InputLabel>
                  <Select
                    name="endpointProtocol"
                    value={formData.endpointProtocol}
                    label="Protocol"
                    onChange={handleSelectChange}
                    startAdornment={
                      <InputAdornment position="start">
                        <Iconify icon={shieldIcon} width={18} height={18} />
                      </InputAdornment>
                    }
                  >
                    <MenuItem value="https">HTTPS</MenuItem>
                    <MenuItem value="http">HTTP</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  label="Endpoint Suffix (Optional)"
                  name="endpointSuffix"
                  value={formData.endpointSuffix}
                  onChange={handleChange}
                  placeholder="core.windows.net"
                  error={Boolean(errors.endpointSuffix)}
                  helperText={errors.endpointSuffix || 'e.g., core.windows.net'}
                  size="small"
                  disabled={!isEditing}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={webIcon} width={18} height={18} />
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
            </>
          )}
        </Grid>

        {saveError && (
          <Alert severity="error" sx={{ mt: 3 }}>
            {saveError}
          </Alert>
        )}

        {isSaving && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
            <CircularProgress size={24} />
          </Box>
        )}
      </>
    );
  }
);

export default StorageServiceForm;
