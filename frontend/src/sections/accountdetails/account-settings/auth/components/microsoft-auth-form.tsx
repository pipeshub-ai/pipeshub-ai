import infoIcon from '@iconify-icons/eva/info-outline';
import hashIcon from '@iconify-icons/eva/hash-outline';
import globeIcon from '@iconify-icons/eva/globe-outline';
import React, { useState, useEffect, forwardRef, useImperativeHandle } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Alert,
  Switch,
  Snackbar,
  TextField,
  Typography,
  InputAdornment,
  CircularProgress,
  FormControlLabel,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';
import { getDocumentationUrl, shouldShowDocs, DOCS_PATHS } from 'src/utils/docs-helper';

import {
  getMicrosoftAuthConfig,
  updateMicrosoftAuthConfig,
} from '../utils/auth-configuration-service';

interface MicrosoftAuthFormProps {
  onValidationChange: (isValid: boolean) => void;
  onSaveSuccess?: () => void;
}

export interface MicrosoftAuthFormRef {
  handleSave: () => Promise<boolean>;
}
const getRedirectUris = async () => {
  // Get the current window URL without hash and search parameters
  const currentRedirectUri = `${window.location.origin}/auth/microsoft/callback`;

  // Get the frontend URL from the backend
  try {
    const response = await axios.get(`/api/v1/configurationManager/frontendPublicUrl`);
    const frontendBaseUrl = response.data.url;
    // Ensure the URL ends with a slash if needed
    const frontendUrl = frontendBaseUrl.endsWith('/')
      ? `${frontendBaseUrl}auth/microsoft/callback`
      : `${frontendBaseUrl}/auth/microsoft/callback`;

    return {
      currentRedirectUri,
      recommendedRedirectUri: frontendUrl,
      urisMismatch: currentRedirectUri !== frontendUrl,
    };
  } catch (error) {
    console.error('Error fetching frontend URL:', error);
    return {
      currentRedirectUri,
      recommendedRedirectUri: currentRedirectUri,
      urisMismatch: false,
    };
  }
};

const MicrosoftAuthForm = forwardRef<MicrosoftAuthFormRef, MicrosoftAuthFormProps>(
  ({ onValidationChange, onSaveSuccess }, ref) => {
    const theme = useTheme();

    const [redirectUris, setRedirectUris] = useState<{
      currentRedirectUri: string;
      recommendedRedirectUri: string;
      urisMismatch: boolean;
    } | null>(null);
    const [formData, setFormData] = useState({
      clientId: '',
      tenantId: '',
      redirectUri:
        redirectUris?.recommendedRedirectUri || `${window.location.origin}/auth/microsoft/callback`,
      enableJit: true,
      skipEmailScreen: false,
    });

    const [errors, setErrors] = useState({
      clientId: '',
      tenantId: '',
    });

    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [snackbar, setSnackbar] = useState({
      open: false,
      message: '',
      severity: 'error' as 'success' | 'error',
    });

    // Helper functions for snackbar
    const showErrorSnackbar = (message: string) => {
      setSnackbar({
        open: true,
        message,
        severity: 'error',
      });
    };

    const showSuccessSnackbar = (message: string) => {
      setSnackbar({
        open: true,
        message,
        severity: 'success',
      });
    };

    const handleCloseSnackbar = () => {
      setSnackbar((prev) => ({ ...prev, open: false }));
    };

    // Expose the handleSave method to the parent component
    useImperativeHandle(ref, () => ({
      handleSave,
    }));

    // Load existing config on mount
    useEffect(() => {
      const fetchConfig = async () => {
        setIsLoading(true);
        try {
          // Parallelize API calls for better performance
          const [uris, config] = await Promise.all([
            getRedirectUris(),
            getMicrosoftAuthConfig(),
          ]);

          setRedirectUris(uris);

          // Set default redirectUri from uris immediately (not from state)
          const recommendedUri =
            uris?.recommendedRedirectUri || `${window.location.origin}/auth/microsoft/callback`;

          // Consolidate all form data updates into a single atomic update
          setFormData({
            redirectUri: recommendedUri,
            clientId: config?.clientId || '',
            tenantId: config?.tenantId || '',
            enableJit: config?.enableJit ?? true,
            skipEmailScreen: config?.skipEmailScreen ?? false,
          });
        } catch (error) {
          console.error('Failed to load Microsoft authentication configuration:', error);
          // showErrorSnackbar('Failed to load Microsoft authentication configuration');
        } finally {
          setIsLoading(false);
        }
      };

      fetchConfig();
    }, []);

    // Validate form and notify parent
    useEffect(() => {
      const isValid =
        formData.clientId.trim() !== '' &&
        formData.tenantId.trim() !== '' &&
        !errors.clientId &&
        !errors.tenantId;

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
        error = 'Application ID appears to be too short';
      } else if (name === 'tenantId' && value.length < 4) {
        error = 'Directory ID appears to be too short';
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
        // Use the utility function to update Microsoft configuration
        await updateMicrosoftAuthConfig({
          clientId: formData.clientId,
          tenantId: formData.tenantId,
          enableJit: formData.enableJit,
          skipEmailScreen: formData.skipEmailScreen,
        });

        showSuccessSnackbar('Microsoft authentication configuration saved successfully');

        if (onSaveSuccess) {
          onSaveSuccess();
        }

        return true;
      } catch (error) {
        // showErrorSnackbar('Failed to save Microsoft authentication configuration');
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
                  Redirect URI (add to your Microsoft application registration):
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
                    {formData.redirectUri}
                  </Box>
                </Typography>
              </Box>
            </Box>

            <Grid container spacing={2.5}>
              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Application (Client) ID"
                  name="clientId"
                  value={formData.clientId}
                  onChange={handleChange}
                  placeholder="Enter your Microsoft Application ID"
                  error={Boolean(errors.clientId)}
                  helperText={
                    errors.clientId ||
                    'The Application (client) ID from your Microsoft app registration'
                  }
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

              <Grid item xs={12}>
                <TextField
                  fullWidth
                  label="Directory (Tenant) ID"
                  name="tenantId"
                  value={formData.tenantId}
                  onChange={handleChange}
                  placeholder="Enter your Microsoft Directory ID"
                  error={Boolean(errors.tenantId)}
                  helperText={
                    errors.tenantId ||
                    'The Directory (tenant) ID from your Microsoft app registration'
                  }
                  required
                  size="small"
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify icon={globeIcon} width={18} height={18} />
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
                <Box
                  sx={{
                    p: 2,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.primary.main, 0.04),
                    border: `1px solid ${alpha(theme.palette.primary.main, 0.15)}`,
                  }}
                >
                  <FormControlLabel
                    control={
                      <Switch
                        checked={formData.enableJit}
                        onChange={(e) =>
                          setFormData((prev) => ({ ...prev, enableJit: e.target.checked }))
                        }
                        color="primary"
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="subtitle2">
                          Enable Just-In-Time (JIT) Provisioning
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          Automatically create user accounts when they sign in with Microsoft for the
                          first time
                        </Typography>
                      </Box>
                    }
                    sx={{ alignItems: 'flex-start', ml: 0 }}
                  />
                </Box>
              </Grid>

              <Grid item xs={12}>
                <Box
                  sx={{
                    p: 2,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.warning.main, 0.04),
                    border: `1px solid ${alpha(theme.palette.warning.main, 0.15)}`,
                  }}
                >
                  <FormControlLabel
                    control={
                      <Switch
                        checked={formData.skipEmailScreen}
                        onChange={(e) =>
                          setFormData((prev) => ({ ...prev, skipEmailScreen: e.target.checked }))
                        }
                        color="warning"
                      />
                    }
                    label={
                      <Box>
                        <Typography variant="subtitle2">Skip Email Entry Screen</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Users will be taken directly to Microsoft sign-in without entering their email first.
                          Recommended for organizations using Microsoft as the only sign-in method.
                        </Typography>
                      </Box>
                    }
                    sx={{ alignItems: 'flex-start', ml: 0 }}
                  />
                </Box>
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
        {shouldShowDocs() && getDocumentationUrl(DOCS_PATHS.MICROSOFT_AUTH) && (
          <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
            Refer to{' '}
            <Link
              href={getDocumentationUrl(DOCS_PATHS.MICROSOFT_AUTH)}
              target="_blank"
              rel="noopener"
            >
              the documentation
            </Link>{' '}
            for more information.
          </Alert>
        )}
      </>
    );
  }
);

export default MicrosoftAuthForm;
