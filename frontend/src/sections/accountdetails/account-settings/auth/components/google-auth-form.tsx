import infoIcon from '@iconify-icons/eva/info-outline';
import hashIcon from '@iconify-icons/eva/hash-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Alert,
  Snackbar,
  TextField,
  Typography,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';
import { getDocumentationUrl, shouldShowDocs, DOCS_PATHS } from 'src/utils/docs-helper';

import { getGoogleAuthConfig, updateGoogleAuthConfig } from '../utils/auth-configuration-service';

interface GoogleAuthFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}
const API_ENDPOINTS = {
  FRONTEND_URL: '/api/v1/configurationManager/frontendPublicUrl',
};

export interface GoogleAuthFormRef {
  handleSave: () => Promise<boolean>;
}

const getRedirectUris = async () => {
  // Get the current window URL without hash and search parameters
  const currentRedirectUri = `${window.location.origin}/auth/google/callback`;

  // Get the frontend URL from the backend
  try {
    const response = await axios.get(API_ENDPOINTS.FRONTEND_URL);
    const frontendBaseUrl = response.data.url || '';
    // Ensure the URL ends with a slash if needed
    const frontendUrl = frontendBaseUrl.endsWith('/')
      ? `${frontendBaseUrl}auth/google/callback`
      : `${frontendBaseUrl}/auth/google/callback`;

    return {
      currentRedirectUri,
      recommendedRedirectUri: frontendUrl,
      frontendBaseUrl,
      urisMismatch: currentRedirectUri !== frontendUrl,
    };
  } catch (error) {
    console.error('Error fetching frontend URL:', error);
    return {
      currentRedirectUri,
      recommendedRedirectUri: currentRedirectUri,
      frontendBaseUrl: window.location.origin,
      urisMismatch: false,
    };
  }
};
const GoogleAuthForm = forwardRef<GoogleAuthFormRef, GoogleAuthFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [snackbar, setSnackbar] = useState({
      open: false,
      message: '',
      severity: 'success' as 'success' | 'error',
    });
    const [redirectUris, setRedirectUris] = useState<{
      currentRedirectUri: string;
      recommendedRedirectUri: string;
      urisMismatch: boolean;
      frontendBaseUrl: string;
    } | null>(null);

    const [formData, setFormData] = useState({
      clientId: '',
      redirectUri:
        redirectUris?.recommendedRedirectUri || `${window.location.origin}/auth/google/callback`,
    });

    const [errors, setErrors] = useState({
      clientId: '',
    });
    // Helper functions for snackbar
    const showSuccessSnackbar = (message: string) => {
      setSnackbar({
        open: true,
        message,
        severity: 'success',
      });
    };

    const showErrorSnackbar = (message: string) => {
      setSnackbar({
        open: true,
        message,
        severity: 'error',
      });
    };

    const handleCloseSnackbar = () => {
      setSnackbar((prev) => ({ ...prev, open: false }));
    };

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave,
    }));

    useEffect(() => {
      const fetchConfig = async () => {
        setIsLoading(true);

        try {
          // First get redirect URIs
          const uris = await getRedirectUris();
          setRedirectUris(uris);

          // Set default redirectUri from uris immediately (not from state)
          const recommendedUri =
            uris?.recommendedRedirectUri || `${window.location.origin}/auth/google/callback`;

          setFormData((prevFormData) => ({
            ...prevFormData,
            redirectUri: recommendedUri,
          }));

          // Then fetch existing config
          const config = await getGoogleAuthConfig();

          if (config) {
            // Update form with existing config data
            setFormData((prev) => ({
              ...prev,
              clientId: config?.clientId || '',
              // Add other fields you need from config
            }));
          }
        } catch (error) {
          console.error('Failed to load Google authentication configuration', error);
          // showErrorSnackbar('Failed to load Google authentication configuration');
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []); // Remove redirectUris from dependency array to avoid infinite loop

    // Validate form and notify parent
    useEffect(() => {
      const isValid = formData.clientId.trim() !== '' && !errors.clientId;

      onValidationChange(isValid);
    }, [formData, errors, onValidationChange]);

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

      if (value.trim() === '') {
        error = 'This field is required';
      } else if (name === 'clientId' && value.length < 8) {
        error = 'Client ID appears to be too short';
      }

      setErrors({
        ...errors,
        [name]: error,
      });
    };

    // Handle save
    const handleSave = async (): Promise<boolean> => {
      setIsSaving(true);

      try {
        // Use the utility function to update Google configuration
        await updateGoogleAuthConfig({
          clientId: formData.clientId,
        });

        showSuccessSnackbar('Google authentication configuration saved successfully');

        if (onSaveSuccess) {
          onSaveSuccess();
        }

        return true;
      } catch (error) {
        // showErrorSnackbar('Failed to save Google authentication configuration');
        return false;
      } finally {
        setIsSaving(false);
      }
    };

    return (
      <>
        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <>
            {redirectUris?.urisMismatch && (
              <>
                <Alert
                  severity="warning"
                  sx={{
                    mb: 3,
                    borderRadius: 1,
                  }}
                >
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    Redirect URI mismatch detected! Using the recommended URI from backend
                    configuration.
                  </Typography>
                  <Typography variant="caption" component="div">
                    Current redirect Uri: {redirectUris.currentRedirectUri}
                  </Typography>
                  <Typography variant="caption" component="div">
                    Recommended redirect URI: {redirectUris.recommendedRedirectUri}
                  </Typography>
                </Alert>
                <Alert
                  severity="warning"
                  sx={{
                    mb: 3,
                    borderRadius: 1,
                  }}
                >
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    Origin mismatch detected! Using the recommended URI from backend configuration.
                  </Typography>
                  <Typography variant="caption" component="div">
                    Current Window Location: {window.location.origin}
                  </Typography>
                  <Typography variant="caption" component="div">
                    Recommended Window Location:{redirectUris.frontendBaseUrl}
                  </Typography>
                </Alert>
              </>
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
                  Redirect URI (add to your Google OAuth settings):
                  <Box
                    component="code"
                    sx={{
                      display: 'block',
                      p: 1.5,
                      my: 1,
                      bgcolor: alpha(theme.palette.background.default, 0.7),
                      borderRadius: 1,
                      fontSize: '0.8rem',
                      fontFamily: 'monospace',
                      wordBreak: 'break-all',
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    {formData.redirectUri}
                  </Box>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Authorized Origin (add to your Google OAuth settings):
                  <Box
                    component="code"
                    sx={{
                      display: 'block',
                      p: 1.5,
                      mt: 1,
                      bgcolor: alpha(theme.palette.background.default, 0.7),
                      borderRadius: 1,
                      fontSize: '0.8rem',
                      fontFamily: 'monospace',
                      wordBreak: 'break-all',
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    {redirectUris?.frontendBaseUrl}
                  </Box>
                </Typography>
              </Box>
            </Box>

            <Grid container spacing={2.5}>
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Client ID"
                  name="clientId"
                  value={formData.clientId}
                  onChange={handleChange}
                  placeholder="Enter your Google OAuth Client ID"
                  error={Boolean(errors.clientId)}
                  helperText={errors.clientId || 'The client ID from your Google OAuth credentials'}
                  required
                  size="small"
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={hashIcon} width={18} height={18} />
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
        )}

        {/* Snackbar for success and error messages */}
        <Snackbar
          open={snackbar.open}
          autoHideDuration={6000}
          onClose={handleCloseSnackbar}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        >
          <Alert
            onClose={handleCloseSnackbar}
            severity={snackbar.severity}
            variant="filled"
            sx={{
              width: '100%',
              boxShadow: '0px 3px 8px rgba(0, 0, 0, 0.12)',
              ...(snackbar.severity === 'error' && {
                bgcolor: theme.palette.error.main,
                color: theme.palette.error.contrastText,
              }),
              ...(snackbar.severity === 'success' && {
                bgcolor: theme.palette.success.main,
                color: theme.palette.success.contrastText,
              }),
            }}
          >
            {snackbar.message}
          </Alert>
        </Snackbar>
        {shouldShowDocs() && getDocumentationUrl(DOCS_PATHS.GOOGLE_AUTH) && (
          <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
            Refer to{' '}
            <Link href={getDocumentationUrl(DOCS_PATHS.GOOGLE_AUTH)} target="_blank" rel="noopener">
              the documentation
            </Link>{' '}
            for more information.
          </Alert>
        )}
      </>
    );
  }
);

export default GoogleAuthForm;
