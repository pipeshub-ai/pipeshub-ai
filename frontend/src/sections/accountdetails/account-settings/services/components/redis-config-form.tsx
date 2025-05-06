import lockIcon from '@iconify-icons/mdi/lock';
import linkIcon from '@iconify-icons/mdi/link';
import closeIcon from '@iconify-icons/mdi/close';
import dbIcon from '@iconify-icons/mdi/database';
import poundIcon from '@iconify-icons/mdi/pound';
import pencilIcon from '@iconify-icons/mdi/pencil';
import serverIcon from '@iconify-icons/mdi/server';
import accountIcon from '@iconify-icons/mdi/account';
import shieldIcon from '@iconify-icons/mdi/shield-lock';
import infoIcon from '@iconify-icons/mdi/information-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Select,
  Button,
  Divider,
  Tooltip,
  MenuItem,
  TextField,
  Typography,
  InputLabel,
  FormControl,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import { getRedisConfig, updateRedisConfig } from '../utils/services-configuration-service';

interface RedisConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}
interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}
export interface RedisConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

const RedisConfigForm = forwardRef<RedisConfigFormRef, RedisConfigFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState({
      uri: '',
      host: '',
      port: '',
      password: '',
      username: '',
      db: '0',
      tls: false,
    });

    const [errors, setErrors] = useState({
      uri: '',
      host: '',
      port: '',
      password: '',
      username: '',
      db: '',
    });

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [isEditing, setIsEditing] = useState(false);
    const [originalData, setOriginalData] = useState({
      uri: '',
      host: '',
      port: '',
      password: '',
      username: '',
      db: '0',
      tls: false,
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
          const config = await getRedisConfig();
          const data = {
            uri: config?.uri || '',
            host: config?.host || '',
            port: config?.port?.toString() || '',
            password: config?.password || '',
            username: config?.username || '',
            db: config?.db || '0',
            // Convert string protocol to boolean tls
            tls: config?.tls,
          };

          setFormData(data);
          setOriginalData(data);
        } catch (error) {
          console.error('Failed to load Redis config:', error);
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validate form and notify parent
    useEffect(() => {
      const isValid =
        formData.host.trim() !== '' && formData.port.trim() !== '' && !errors.host && !errors.port;

      // Only notify about validation if in edit mode and has changes
      const hasChanges =
        formData.uri !== originalData.uri ||
        formData.host !== originalData.host ||
        formData.port !== originalData.port ||
        formData.password !== originalData.password ||
        formData.username !== originalData.username ||
        formData.db !== originalData.db ||
        formData.tls !== originalData.tls;

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

    // Handle select change
    const handleSelectChange = (e: any) => {
      const { name, value } = e.target;

      setFormData({
        ...formData,
        [name]: value === 'true', // Convert string to boolean for TLS
      });
    };

    // Handle edit mode toggle
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - revert to original data
        setFormData(originalData);
        setErrors({
          uri: '',
          host: '',
          port: '',
          password: '',
          username: '',
          db: '',
        });
      }
      setIsEditing(!isEditing);
    };

    // Field validation
    const validateField = (name: string, value: string) => {
      let error = '';

      if (name === 'host' && value.trim() === '') {
        error = 'Host is required';
      } else if (name === 'port') {
        if (value.trim() === '') {
          error = 'Port is required';
        } else if (
          !/^\d+$/.test(value) ||
          parseInt(value, 10) <= 0 ||
          parseInt(value, 10) > 65535
        ) {
          error = 'Port must be a valid number between 1-65535';
        }
      } else if (name === 'db' && value.trim() !== '' && !/^\d+$/.test(value)) {
        error = 'DB must be a number';
      }

      setErrors({
        ...errors,
        [name]: error,
      });
    };

    // Parse Redis URI
    const parseRedisUri = () => {
      try {
        if (formData.uri) {
          const url = new URL(formData.uri);
          const host = url.hostname;
          const { port } = url;
          const { password } = url;
          const { username } = url;
          const db = url.pathname ? url.pathname.substring(1) : '0';
          const tls = url.protocol === 'rediss:';

          const updatedFormData = {
            ...formData,
            host,
            port,
            password,
            username,
            db,
            tls,
          };

          setFormData(updatedFormData);

          // Validate parsed fields
          validateField('host', host);
          validateField('port', port);
          validateField('db', db);
        }
      } catch (error) {
        console.error('Failed to parse Redis URI:', error);
        setErrors({
          ...errors,
          uri: 'Invalid Redis URI format',
        });
      }
    };

    // Handle save
    const handleSave = async () => {
      setIsSaving(true);
      setSaveError(null);

      try {
        const response = await updateRedisConfig({
          uri: formData.uri,
          host: formData.host,
          port: parseInt(formData.port, 10), // Convert port to number
          password: formData.password,
          username: formData.username,
          db: formData.db,
          tls: formData.tls, // Send as boolean
        });
        const warningHeader = response.data?.warningMessage;
        // Update original data after successful save
        setOriginalData({
          uri: formData.uri,
          host: formData.host,
          port: formData.port,
          password: formData.password,
          username: formData.username,
          db: formData.db,
          tls: formData.tls,
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
        const errorMessage = 'Failed to save Redis configuration';
        setSaveError(error.message || errorMessage);
        console.error('Error saving Redis config:', error);
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
              Redis connection information. You can either enter a Redis URI or fill in the
              individual connection details.
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
          <Grid item xs={12}>
            <TextField
              fullWidth
              label="Redis URI"
              name="uri"
              value={formData.uri}
              onChange={handleChange}
              placeholder="redis://username:password@host:port/db"
              error={Boolean(errors.uri)}
              helperText={
                errors.uri || 'e.g., redis://username:password@host:port/db or rediss:// for TLS'
              }
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

          <Grid item xs={12}>
            <Button
              variant="outlined"
              onClick={parseRedisUri}
              disabled={!formData.uri || !isEditing}
              size="small"
            >
              Parse URI
            </Button>
          </Grid>

          <Grid item xs={12}>
            <Divider sx={{ my: 1 }}>
              <Typography variant="caption" color="text.secondary">
                OR Individual Connection Details
              </Typography>
            </Divider>
          </Grid>

          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="Host"
              name="host"
              value={formData.host}
              onChange={handleChange}
              placeholder="localhost"
              error={Boolean(errors.host)}
              helperText={errors.host || 'Redis server hostname or IP address'}
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
              label="Port"
              name="port"
              value={formData.port}
              onChange={handleChange}
              placeholder="6379"
              error={Boolean(errors.port)}
              helperText={errors.port || 'Redis server port (default: 6379)'}
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
              label="Username (optional)"
              name="username"
              value={formData.username}
              onChange={handleChange}
              error={Boolean(errors.username)}
              helperText={errors.username || 'Redis username if authentication is enabled'}
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
              label="Password (optional)"
              name="password"
              type="password"
              value={formData.password}
              onChange={handleChange}
              error={Boolean(errors.password)}
              helperText={errors.password || 'Redis password if authentication is enabled'}
              size="small"
              disabled={!isEditing}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={lockIcon} width={18} height={18} />
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
              label="Database (optional)"
              name="db"
              value={formData.db}
              onChange={handleChange}
              placeholder="0"
              error={Boolean(errors.db)}
              helperText={errors.db || 'Redis database number (default: 0)'}
              size="small"
              type="number"
              disabled={!isEditing}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={dbIcon} width={18} height={18} />
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
              <InputLabel>TLS Connection</InputLabel>
              <Select
                name="tls"
                value={formData.tls}
                label="TLS Connection"
                onChange={handleSelectChange}
                startAdornment={
                  <InputAdornment position="start">
                    <Iconify icon={shieldIcon} width={18} height={18} />
                  </InputAdornment>
                }
              >
                <MenuItem value="false">No TLS</MenuItem>
                <MenuItem value="true">With TLS (rediss)</MenuItem>
              </Select>
            </FormControl>
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

export default RedisConfigForm;
