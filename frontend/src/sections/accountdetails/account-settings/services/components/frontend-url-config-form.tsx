import closeIcon from '@iconify-icons/mdi/close';
import pencilIcon from '@iconify-icons/mdi/pencil';
import serverIcon from '@iconify-icons/mdi/server-outline';
import infoIcon from '@iconify-icons/mdi/information-outline';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import React, { useState, useEffect, forwardRef, useImperativeHandle, useCallback } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Button,
  TextField,
  Typography,
  InputAdornment,
  CircularProgress,
  Fade,
  Collapse,
  Alert,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import {
  getFrontendPublicUrl,
  updateFrontendPublicUrl,
} from '../utils/services-configuration-service';

// Constants
const URL_REGEX = /^(?:https?:\/\/)?(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?::\d{2,5})?(?:\/\S*)?$/;
const HELPER_TEXT = 'The URL of your Frontend DNS server';
const ERROR_MESSAGES = {
  REQUIRED: 'Frontend DNS is required',
  INVALID: 'Enter a valid URL',
  SAVE_FAILED: 'Failed to save Frontend DNS',
} as const;

// Utility functions
const removeTrailingSlash = (url: string): string => 
  url.endsWith('/') ? url.slice(0, -1) : url;

const isValidURL = (url: string): boolean => URL_REGEX.test(url);

// Types
interface FrontendUrlFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}

export interface FrontendUrlConfigFormRef {
  handleSave: () => Promise<SaveResult>;
}

interface SaveResult {
  success: boolean;
  warning?: string;
  error?: string;
}

interface FormData {
  url: string;
}

interface FormErrors {
  url: string;
}

const FrontendUrlConfigForm = forwardRef<FrontendUrlConfigFormRef, FrontendUrlFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();

    // State management
    const [formData, setFormData] = useState<FormData>({ url: '' });
    const [originalData, setOriginalData] = useState<FormData>({ url: '' });
    const [errors, setErrors] = useState<FormErrors>({ url: '' });
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [showSuccess, setShowSuccess] = useState(false);
    const [feedbackMessage, setFeedbackMessage] = useState<{
      type: 'success' | 'warning' | 'error';
      message: string;
    } | null>(null);

    // Expose handleSave to parent
    useImperativeHandle(ref, () => ({
      handleSave: async (): Promise<SaveResult> => handleSave(),
    }));

    // Fetch initial configuration
    useEffect(() => {
      const fetchConfig = async () => {
        setIsLoading(true);
        try {
          const config = await getFrontendPublicUrl();
          const data: FormData = { url: config?.url || '' };
          setFormData(data);
          setOriginalData(data);
        } catch (error) {
          console.error('Failed to load Frontend DNS', error);
          setFeedbackMessage({
            type: 'error',
            message: 'Failed to load configuration. Please refresh the page.',
          });
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validation effect
    useEffect(() => {
      const hasChanges = formData.url !== originalData.url;
      const isValid = formData.url.trim() !== '' && !errors.url;
      onValidationChange(isValid && isEditing && hasChanges);
    }, [formData, errors, onValidationChange, isEditing, originalData]);

    // Auto-hide success message
    useEffect(() => {
      if (showSuccess) {
        const timer = setTimeout(() => {
          setShowSuccess(false);
        }, 3000);
        return () => clearTimeout(timer);
      }
      return undefined;
    }, [showSuccess]);

    // Auto-hide feedback messages
    useEffect(() => {
      if (feedbackMessage) {
        const timer = setTimeout(() => {
          setFeedbackMessage(null);
        }, 5000);
        return () => clearTimeout(timer);
      }
      return undefined;
    }, [feedbackMessage]);

    // Field validation
    const validateField = useCallback((name: keyof FormErrors, value: string): string => {
      if (name === 'url') {
        if (!value.trim()) {
          return ERROR_MESSAGES.REQUIRED;
        }
        if (!isValidURL(value)) {
          return ERROR_MESSAGES.INVALID;
        }
      }
      return '';
    }, []);

    // Handle input change
    const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
      const { name, value } = e.target;
      
      setFormData((prev) => ({
        ...prev,
        [name]: value,
      }));

      const error = validateField(name as keyof FormErrors, value);
      setErrors((prev) => ({
        ...prev,
        [name]: error,
      }));
    }, [validateField]);

    // Toggle edit mode
    const handleToggleEdit = useCallback(() => {
      if (isEditing) {
        setFormData(originalData);
        setErrors({ url: '' });
        setFeedbackMessage(null);
      }
      setIsEditing((prev) => !prev);
    }, [isEditing, originalData]);

    // Save handler
    const handleSave = async (): Promise<SaveResult> => {
      setIsSaving(true);
      setFeedbackMessage(null);
      setShowSuccess(false);

      try {
        const urlToSave = removeTrailingSlash(formData.url.trim());
        const response = await updateFrontendPublicUrl(urlToSave);
        const warningMessage = response.data?.warningMessage;

        const updatedData: FormData = { url: urlToSave };
        setOriginalData(updatedData);
        setFormData(updatedData);
        setIsEditing(false);
        setShowSuccess(true);

        if (warningMessage) {
          setFeedbackMessage({
            type: 'warning',
            message: warningMessage,
          });
        }

        if (onSaveSuccess) {
          onSaveSuccess();
        }

        return {
          success: true,
          warning: warningMessage || undefined,
        };
      } catch (error: any) {
        const errorMessage = error.message || ERROR_MESSAGES.SAVE_FAILED;
        console.error('Error saving Frontend DNS', error);
        
        setFeedbackMessage({
          type: 'error',
          message: errorMessage,
        });

        return {
          success: false,
          error: errorMessage,
        };
      } finally {
        setIsSaving(false);
      }
    };

    // Get computed values
    const hasChanges = formData.url !== originalData.url;
    const isFormDisabled = !isEditing || isSaving;

    // Loading state
    if (isLoading) {
      return (
        <Box 
          sx={{ 
            display: 'flex', 
            flexDirection: 'column',
            justifyContent: 'center', 
            alignItems: 'center',
            minHeight: 280,
            gap: 2,
          }}
        >
          <CircularProgress size={32} thickness={4} />
          <Typography variant="body2" color="text.secondary">
            Loading configuration...
          </Typography>
        </Box>
      );
    }

    return (
      <Box sx={{ my:2 }}>

        {/* Info Banner */}
        <Box
          sx={{
            mb: 3,
            p: 2,
            borderRadius: 1.5,
            bgcolor: alpha(theme.palette.info.main, 0.04),
            border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 1.5,
            transition: 'all 0.2s ease-in-out',
            '&:hover': {
              bgcolor: alpha(theme.palette.info.main, 0.06),
            },
          }}
        >
          <Iconify
            icon={infoIcon}
            width={20}
            height={20}
            color={theme.palette.info.main}
            sx={{ mt: 0.25, flexShrink: 0 }}
          />
          <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6 }}>
            Enter the Frontend Public DNS that your application will connect to. Include the
            complete URL with protocol (e.g., http:// or https://), host, and port if necessary.
          </Typography>
        </Box>

        {/* Action Bar */}
        <Box 
          sx={{ 
            display: 'flex', 
            justifyContent: 'flex-end',
            alignItems: 'center',
            mb: 2.5,
          }}
        >
          {/* Edit/Cancel Button */}
          <Button
            onClick={handleToggleEdit}
            startIcon={<Iconify icon={isEditing ? closeIcon : pencilIcon} />}
            variant={isEditing ? 'outlined' : 'text'}
            color={isEditing ? 'error' : 'primary'}
            size="small"
            disabled={isSaving}
            sx={{
              transition: 'all 0.2s ease-in-out',
              minWidth: 100,
            }}
          >
            {isEditing ? 'Cancel' : 'Edit'}
          </Button>
        </Box>

        {/* Form Content */}
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
              helperText={errors.url || HELPER_TEXT}
              required
              size="small"
              disabled={isFormDisabled}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify 
                      icon={serverIcon} 
                      width={18} 
                      height={18}
                      sx={{
                        color: errors.url 
                          ? theme.palette.error.main 
                          : theme.palette.text.secondary,
                        transition: 'color 0.2s ease-in-out',
                      }}
                    />
                  </InputAdornment>
                ),
                endAdornment: isSaving && (
                  <InputAdornment position="end">
                    <CircularProgress size={18} thickness={4} />
                  </InputAdornment>
                ),
              }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  transition: 'all 0.2s ease-in-out',
                  '& fieldset': {
                    borderColor: alpha(theme.palette.text.primary, 0.15),
                    transition: 'border-color 0.2s ease-in-out',
                  },
                  '&:hover fieldset': {
                    borderColor: alpha(theme.palette.primary.main, 0.3),
                  },
                  '&.Mui-focused fieldset': {
                    borderColor: theme.palette.primary.main,
                    borderWidth: 2,
                  },
                  '&.Mui-disabled': {
                    bgcolor: alpha(theme.palette.action.disabled, 0.02),
                  },
                },
                '& .MuiInputBase-input.Mui-disabled': {
                  WebkitTextFillColor: theme.palette.text.primary,
                  opacity: 0.7,
                },
              }}
            />
          </Grid>
        </Grid>

        {/* Saving Status Text */}
        <Fade in={isSaving} timeout={200}>
          <Box
            sx={{
              my: 2,
              display: isSaving ? 'flex' : 'none',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 1,
            }}
          >
            <Typography 
              variant="body2" 
              color="text.secondary"
              sx={{ fontWeight: 500 }}
            >
              Saving configuration...
            </Typography>
          </Box>
        </Fade>
      </Box>
    );
  }
);

FrontendUrlConfigForm.displayName = 'FrontendUrlConfigForm';

export default FrontendUrlConfigForm;