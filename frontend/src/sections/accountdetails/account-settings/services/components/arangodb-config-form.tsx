import linkIcon from '@iconify-icons/mdi/link';
import lockIcon from '@iconify-icons/mdi/lock';
import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';
import accountIcon from '@iconify-icons/mdi/account';
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

import { getArangoDBConfig, updateArangoDBConfig } from '../utils/services-configuration-service';

interface ArangoDBConfigFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}
interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

export interface ArangoDBConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

const ArangoDBConfigForm = forwardRef<ArangoDBConfigFormRef, ArangoDBConfigFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState({
      url: '',
      username: '',
      password: '',
    });

    const [errors, setErrors] = useState({
      url: '',
      username: '',
      password: '',
    });

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [isEditing, setIsEditing] = useState(false);
    const [originalData, setOriginalData] = useState({
      url: '',
      username: '',
      password: '',
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
          const config = await getArangoDBConfig();

          const data = {
            url: config?.url || '',
            username: config?.username || '',
            password: config?.password || '',
          };

          setFormData(data);
          setOriginalData(data);
        } catch (error) {
          console.error('Failed to load ArangoDB config:', error);
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validate form and notify parent
    useEffect(() => {
      const isValid =
        formData.url.trim() !== '' &&
        formData.username.trim() !== '' &&
        !errors.url &&
        !errors.username;

      // Only notify about validation if in edit mode and has changes
      const hasChanges =
        formData.url !== originalData.url ||
        formData.username !== originalData.username ||
        formData.password !== originalData.password;

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

    // Field validation
    const validateField = (name: string, value: string) => {
      let error = '';

      if (name === 'url' && value.trim() === '') {
        error = 'URL is required';
      } else if (name === 'username' && value.trim() === '') {
        error = 'Username is required';
      }

      setErrors({
        ...errors,
        [name]: error,
      });
    };

    // Handle edit mode toggle
    const handleToggleEdit = () => {
      if (isEditing) {
        // Cancel edit - revert to original data
        setFormData(originalData);
        setErrors({
          url: '',
          username: '',
          password: '',
        });
      }
      setIsEditing(!isEditing);
    };

    // Handle save
    const handleSave = async (): Promise<SaveResult> => {
      setIsSaving(true);
      setSaveError(null);

      try {
        const response = await updateArangoDBConfig({
          url: formData.url,
          username: formData.username,
          password: formData.password,
        });

        const warningHeader = response.data?.warningMessage;
        // Update original data after successful save
        setOriginalData({
          url: formData.url,
          username: formData.username,
          password: formData.password,
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
        const errorMessage = 'Failed to save ArangoDB configuration';
        setSaveError(error.message || errorMessage);
        console.error('Error saving ArangoDB config:', error);
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
              ArangoDB connection information.
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
          <Grid item md={12} xs={12}>
            <TextField
              fullWidth
              label="ArangoDB URL"
              name="url"
              value={formData.url}
              onChange={handleChange}
              placeholder="http://localhost:8529"
              error={Boolean(errors.url)}
              helperText={errors.url || 'ArangoDB server URL'}
              required
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

          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="Username"
              name="username"
              value={formData.username}
              onChange={handleChange}
              placeholder="username"
              error={Boolean(errors.username)}
              helperText={errors.username || 'ArangoDB username'}
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
              label="Password"
              name="password"
              type="password"
              value={formData.password}
              onChange={handleChange}
              error={Boolean(errors.password)}
              helperText={errors.password || 'ArangoDB password'}
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

export default ArangoDBConfigForm;
