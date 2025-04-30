import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';
import serverIcon from '@iconify-icons/mdi/server-outline';
import infoIcon from '@iconify-icons/mdi/information-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Button,
  TextField,
  Typography,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import {
  getFrontendPublicUrl,
  updateFrontendPublicUrl,
} from '../utils/services-configuration-service';

const removeTrailingSlash = (url: string): string => (url.endsWith('/') ? url.slice(0, -1) : url);

interface FrontendUrlFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}
const urlRegex = /^(https?:\/\/)?([\w.-]+)+(:\d+)?(\/[\w./?%&=-]*)?$/;

const isValidURL = (url: string): boolean => {
  if (urlRegex.test(url)) {
    return true;
  }
  return false;
};

export interface FrontendUrlConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

const FrontendUrlConfigForm = forwardRef<FrontendUrlConfigFormRef, FrontendUrlFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();
    const [formData, setFormData] = useState({
      url: '',
    });

    const [errors, setErrors] = useState({
      url: '',
    });

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [isEditing, setIsEditing] = useState(false);
    const [originalData, setOriginalData] = useState({
      url: '',
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
          const config = await getFrontendPublicUrl();
          const url = config?.url || '';

          // Set both current and original data
          const data = {
            url,
          };

          setFormData(data);
          setOriginalData(data);
        } catch (error) {
          console.error('Failed to load Frontend DNS', error);
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validate form and notify parent
    useEffect(() => {
      const isValid = formData.url.trim() !== '' && !errors.url;

      // Only notify about validation if in edit mode or if the form has changed
      const hasChanges = formData.url !== originalData.url;

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

      if (name === 'url') {
        if (!value.trim()) {
          error = 'Frontend DNS is required';
        } else if (!isValidURL(value)) {
          error = 'Enter a valid URL';
        }
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
        });
      }
      setIsEditing(!isEditing);
    };

    // Handle save
    const handleSave = async () => {
      setIsSaving(true);
      setSaveError(null);

      try {
        const urlToSave = removeTrailingSlash(formData.url);
        const response = await updateFrontendPublicUrl(urlToSave);
        const warningHeader = response.data?.warningMessage;
        // Update original data after successful save
        setOriginalData({
          url: urlToSave,
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
        const errorMessage = 'Failed to save Frontend DNS';
        setSaveError(error.message || errorMessage);
        console.error('Error saving Frontend DNS', error);
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
              Enter the Frontend Public DNS that your application will connect to. Include the
              complete URL with protocol (e.g., http:// or https://), host, and port if necessary.
            </Typography>
          </Box>
        </Box>

        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
          <Button
            onClick={handleToggleEdit}
            startIcon={<Iconify icon={isEditing ? closeIcon : pencilIcon} />}
            color={isEditing ? 'error' : 'primary'}
            size="small"
          >
            {isEditing ? 'Cancel' : 'Edit'}
          </Button>
        </Box>

        <Grid container spacing={2.5}>
          <Grid item xs={12}>
            <TextField
              fullWidth
              label="Frontend DNS"
              name="url"
              value={formData.url}
              onChange={handleChange}
              placeholder="https://api.example.com"
              error={Boolean(errors.url)}
              helperText={errors.url || 'The URL of your Frontend DNS server'}
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

export default FrontendUrlConfigForm;
