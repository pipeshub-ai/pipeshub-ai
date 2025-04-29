import { z } from 'zod';
import React, { useState, useEffect } from 'react';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';

import { Box, Grid, Link, Alert, Button, TextField, Typography } from '@mui/material';

import axios from 'src/utils/axios';

// API base URL
const API_BASE_URL = '/api/v1/configurationManager';

// Using export type to fix the TypeScript issue
export type UrlFormValues = {
  frontendUrl: string;
  connectorUrl: string;
};

interface UrlConfigStepProps {
  onSubmit: (data: UrlFormValues) => void;
  onSkip: () => void;
  initialValues: UrlFormValues | null;
  onValidationChange?: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
  submitDirectly?: boolean; // New prop to control direct API submission
}

// URL validation regex
const urlRegex = /^(https?:\/\/)?([\w.-]+)+(:\d+)?(\/[\w./?%&=-]*)?$/;

// URL validation function
const isValidURL = (url: string): boolean => {
  if (urlRegex.test(url)) {
    return true;
  }
  return false;
};

// Create Zod schema for URL validation
const urlSchema = z.object({
  frontendUrl: z
    .string()
    .refine((val) => val === '' || isValidURL(val), 'Please enter a valid URL'),
  connectorUrl: z
    .string()
    .refine((val) => val === '' || isValidURL(val), 'Please enter a valid URL'),
});

// Expose utility functions for validation from the parent component
if (typeof window !== 'undefined') {
  (window as any).submitUrlForm = async () => {
    try {
      // If the window function exists, call it
      if (typeof (window as any).__urlFormSubmit === 'function') {
        return (window as any).__urlFormSubmit();
      }
      return false;
    } catch (err) {
      console.error('Error submitting URL form:', err);
      return false;
    }
  };

  // More aligned with the LLM implementation
  (window as any).isUrlFormValid = async () => {
    try {
      // If the window function exists, call it
      if (typeof (window as any).__urlFormIsValid === 'function') {
        return (window as any).__urlFormIsValid();
      }
      return false;
    } catch (err) {
      console.error('Error validating URL form:', err);
      return false;
    }
  };

  // Add a function to check if any user input has been entered
  (window as any).hasUrlInput = () => {
    try {
      // If the window function exists, call it
      if (typeof (window as any).__urlFormHasInput === 'function') {
        return (window as any).__urlFormHasInput();
      }
      return false;
    } catch (err) {
      console.error('Error checking URL form input:', err);
      return false;
    }
  };
}

const UrlConfigStep: React.FC<UrlConfigStepProps> = ({
  onSubmit,
  onSkip,
  initialValues,
  onValidationChange,
  onSaveSuccess,
}) => {
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const {
    control,
    handleSubmit,
    formState: { isValid },
    setValue,
    trigger,
    watch,
  } = useForm<UrlFormValues>({
    resolver: zodResolver(urlSchema),
    mode: 'onChange', // Validate on change
    defaultValues: initialValues || {
      frontendUrl: '',
      connectorUrl: '',
    },
  });

  // Watch form values to track changes
  const formValues = watch();

  // Fetch existing URL configurations on component mount
  useEffect(() => {
    const fetchExistingUrls = async () => {
      try {
        setLoading(true);
        setErrorMessage(null);

        // Call the API to fetch existing URL configurations
        const frontendUrlResponse = await axios.get(`${API_BASE_URL}/frontendPublicUrl`);
        const connectorUrlResponse = await axios.get(`${API_BASE_URL}/connectorPublicUrl`);
        let frontendUrl = '';
        let connectorUrl = '';
        if (frontendUrlResponse.data || connectorUrlResponse.data) {
          if (frontendUrlResponse.data) {
            // Only use the URL if it doesn't contain "localhost"
            const {url} = frontendUrlResponse.data;
            if (url && !url.includes('localhost')) {
              frontendUrl = url;
            }
          }

          // Check for connectorUrl data and filter out localhost
          if (connectorUrlResponse.data) {
            // Only use the URL if it doesn't contain "localhost"
            const {url} = connectorUrlResponse.data;
            if (url && !url.includes('localhost')) {
              connectorUrl = url;
            }
          }

          // Set form values with the fetched URLs
          setValue('frontendUrl', frontendUrl || '');
          setValue('connectorUrl', connectorUrl || '');

          // Trigger validation after setting values
          setTimeout(() => {
            trigger();
          }, 0);
        }
      } catch (err) {
        console.error('Error fetching URL configurations:', err);
        setErrorMessage('Failed to fetch existing URL configurations. Please enter URLs manually.');
      } finally {
        setLoading(false);
      }
    };

    // Only fetch if we don't have initialValues (which would mean they were already set)
    if (!initialValues) {
      fetchExistingUrls();
    }
  }, [setValue, initialValues, trigger]);

  // Notify parent component about validation state changes
  useEffect(() => {
    if (onValidationChange) {
      onValidationChange(isValid);
    }
  }, [isValid, onValidationChange]);

  // Expose submit method to parent component
  useEffect(() => {
    // Store the form submission function in the window object
    (window as any).__urlFormSubmit = async () => {
      // Trigger validation for all fields
      const isFormValid = await trigger();

      if (isFormValid) {
        // Use handleSubmit to properly process the form submission
        const formSubmitHandler = handleSubmit((data) => {
          onSubmit(data);
          return true;
        });

        // Execute the submission handler directly
        formSubmitHandler();
        return true;
      }
      return false;
    };

    // Store the validation function in the window object
    (window as any).__urlFormIsValid = async () => trigger();

    // Store the has input function in the window object
    (window as any).__urlFormHasInput = () => {
      const { frontendUrl, connectorUrl } = formValues;
      return !!(frontendUrl || connectorUrl);
    };

    return () => {
      // Clean up window functions when component unmounts
      delete (window as any).__urlFormSubmit;
      delete (window as any).__urlFormIsValid;
      delete (window as any).__urlFormHasInput;
    };
  }, [handleSubmit, onSubmit, trigger, formValues]);
  const handleFormSubmit = async (data: UrlFormValues) => {
    try {
      setLoading(true);
      setErrorMessage(null);

      // Call the parent component's submit handler
      // This will now perform the API call in the parent component
      onSubmit(data);

      // Call onSaveSuccess if provided
      if (onSaveSuccess) {
        onSaveSuccess();
      }
    } catch (err) {
      console.error('Error in URL configuration form submission:', err);
      setErrorMessage('Failed to save URL configurations. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box component="form" id="url-config-form" onSubmit={handleSubmit(handleFormSubmit)} noValidate>
      <Typography variant="subtitle1" gutterBottom>
        Public URL Configuration
      </Typography>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Configure the public URLs for your frontend and connector services. These URLs are used for
        OAuth redirects and webhook callbacks.
      </Typography>

      <Alert severity="info" sx={{ mb: 3 }}>
        All fields marked with <span style={{ color: 'error.main' }}>*</span> are required.
      </Alert>

      <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
        Refer to{' '}
        <Link href="https://docs.pipeshub.com" target="_blank" rel="noopener">
          the documentation
        </Link>{' '}
        for more information.
      </Alert>

      {errorMessage && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {errorMessage}
        </Alert>
      )}

      {loading && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Loading existing URL configurations...
        </Alert>
      )}

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <Controller
            name="frontendUrl"
            control={control}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                id="frontendUrl"
                label="Frontend URL "
                placeholder="https://yourdomain.com"
                variant="outlined"
                fullWidth
                size="small"
                error={Boolean(fieldState.error)}
                helperText={
                  fieldState.error?.message ||
                  'The public URL where your frontend is hosted (e.g., https://yourdomain.com)'
                }
                required
                onBlur={() => {
                  field.onBlur();
                  trigger('frontendUrl');
                }}
              />
            )}
          />
        </Grid>

        <Grid item xs={12}>
          <Controller
            name="connectorUrl"
            control={control}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                id="connectorUrl"
                label="Connector URL "
                placeholder="https://connector.yourdomain.com"
                variant="outlined"
                fullWidth
                size="small"
                error={Boolean(fieldState.error)}
                helperText={
                  fieldState.error?.message ||
                  'The public URL where your connector service is hosted (e.g., https://connector.yourdomain.com)'
                }
                required
                onBlur={() => {
                  field.onBlur();
                  trigger('connectorUrl');
                }}
              />
            )}
          />
        </Grid>
      </Grid>

      {/* This hidden submit button ensures the form can be submitted programmatically */}
      <Button type="submit" style={{ display: 'none' }} id="url-form-submit-button">
        Submit
      </Button>
    </Box>
  );
};

// Remove export from the end of the file to avoid the conflict
export default UrlConfigStep;
