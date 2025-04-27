import { useNavigate } from 'react-router-dom';
import React, { useState, useEffect } from 'react';
import fileIcon from '@iconify-icons/eva/file-outline';
import fileTextIcon from '@iconify-icons/eva/file-text-outline';
import arrowBackIcon from '@iconify-icons/eva/arrow-ios-back-fill';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Link,
  Paper,
  Alert,
  Button,
  styled,
  Snackbar,
  Container,
  TextField,
  Typography,
  IconButton,
  CircularProgress,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import { useAuthContext } from 'src/auth/hooks';

import { getSamlSsoConfig, updateSamlSsoConfig } from '../utils/auth-configuration-service';

import type { SamlSsoConfig } from '../utils/auth-configuration-service';

const StyledTextarea = styled(TextField)(({ theme }) => ({
  '& .MuiOutlinedInput-root': {
    fontFamily: 'monospace',
    fontSize: '0.8rem',
  },
}));

// Default SAML Configuration with pre-filled values
const DEFAULT_SAML_CONFIG: SamlSsoConfig = {
  entryPoint: '',
  certificate: '',
  emailKey: '',
};

const SamlSsoConfigPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [configuration, setConfiguration] = useState<SamlSsoConfig>(DEFAULT_SAML_CONFIG);
  const [xmlFile, setXmlFile] = useState<File | null>(null);
  const { isAdmin } = useAdmin();
  const { user } = useAuthContext();
  const accountType = user?.accountType;
  const [errors, setErrors] = useState({
    entryPoint: '',
    certificate: '',
  });

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error',
  });

  // Load existing config on mount
  useEffect(() => {
    fetchSamlConfiguration();
    // eslint-disable-next-line
  }, []);

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

  // Fetch SAML Configuration
  const fetchSamlConfiguration = async () => {
    setIsLoading(true);
    try {
      const response = await getSamlSsoConfig();
      if (response && (response.entryPoint || response.certificate)) {
        setConfiguration(response);
      } else {
        // Use default if no config or incomplete
        setConfiguration(DEFAULT_SAML_CONFIG);
      }
    } catch (error) {
      // showErrorSnackbar('Failed to load SAML configuration');
    } finally {
      setIsLoading(false);
    }
  };

  // Handle file upload
  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.files && event.target.files[0]) {
      const file = event.target.files[0];
      setXmlFile(file);

      // Read file content
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;

        try {
          if (content) {
            const updatedConfig = { ...configuration };
            let fieldsUpdated = false;

            // Use more robust regex patterns for namespaced XML

            // Extract Entry Point URL (SSO URL) - try different namespace patterns
            const ssoRegexPatterns = [
              /<md:SingleSignOnService[^>]*?Location="([^"]+)"[^>]*?>/i,
              /<SingleSignOnService[^>]*?Location="([^"]+)"[^>]*?>/i,
              /<[^:>]*:SingleSignOnService[^>]*?Location="([^"]+)"[^>]*?>/i,
            ];

            // Using find() instead of for...of loop to avoid ESLint error
            const ssoMatch = ssoRegexPatterns
              .map((pattern) => content.match(pattern))
              .find((match) => match && match[1]);

            if (ssoMatch && ssoMatch[1]) {
              updatedConfig.entryPoint = ssoMatch[1];
              fieldsUpdated = true;
            }

            // Extract Certificate - try different namespace patterns
            const certRegexPatterns = [
              /<ds:X509Certificate>([\s\S]*?)<\/ds:X509Certificate>/i,
              /<X509Certificate>([\s\S]*?)<\/X509Certificate>/i,
              /<[^:>]*:X509Certificate>([\s\S]*?)<\/[^:>]*:X509Certificate>/i,
            ];

            // Using find() instead of for...of loop to avoid ESLint error
            const certMatch = certRegexPatterns
              .map((pattern) => content.match(pattern))
              .find((match) => match && match[1]);

            if (certMatch && certMatch[1]) {
              // Format certificate properly with line breaks
              const certContent = certMatch[1].trim().replace(/\s+/g, '');

              // Break the certificate into lines of 64 characters
              // Using reduce to build formatted certificate rather than for loop
              const formattedCert = `-----BEGIN CERTIFICATE-----
${Array.from({ length: Math.ceil(certContent.length / 64) })
  .map((_, i) => certContent.substring(i * 64, (i + 1) * 64))
  .join('\n')}
-----END CERTIFICATE-----`;

              updatedConfig.certificate = formattedCert;
              fieldsUpdated = true;
            }

            // Try to extract Entity ID
            const entityIdRegexPatterns = [
              /entityID="([^"]+)"/i,
              /<md:EntityDescriptor[^>]*?entityID="([^"]+)"[^>]*?>/i,
              /<EntityDescriptor[^>]*?entityID="([^"]+)"[^>]*?>/i,
            ];

            // Using find() instead of for...of loop to avoid ESLint error
            const entityIdMatch = entityIdRegexPatterns
              .map((pattern) => content.match(pattern))
              .find((match) => match && match[1]);

            if (entityIdMatch && entityIdMatch[1]) {
              updatedConfig.entityId = entityIdMatch[1];
              fieldsUpdated = true;
            }

            if (fieldsUpdated) {
              setConfiguration(updatedConfig);

              // Validate updated fields
              if (updatedConfig.entryPoint) {
                validateField('entryPoint', updatedConfig.entryPoint);
              }
              if (updatedConfig.certificate) {
                validateField('certificate', updatedConfig.certificate);
              }

              showSuccessSnackbar('Successfully parsed XML metadata file');
            } else {
              showErrorSnackbar(
                'Could not extract required fields from XML. Please check the format or enter details manually.'
              );
            }
          }
        } catch (err) {
          // showErrorSnackbar('Failed to parse XML file. Please check the format.');
        }
      };
      reader.readAsText(file);
    }
  };

  // Handle input change
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;

    setConfiguration((prev) => ({
      ...prev,
      [name]: value,
    }));

    // Validate
    validateField(name, value);
  };

  // Field validation
  const validateField = (name: string, value: string) => {
    let error = '';

    if (name === 'entryPoint') {
      if (value.trim() === '') {
        error = 'Entry Point URL is required';
      } else if (!value.startsWith('https://')) {
        error = 'Entry Point URL should start with https://';
      }
    } else if (name === 'certificate') {
      if (
        !value.includes('-----BEGIN CERTIFICATE-----') ||
        !value.includes('-----END CERTIFICATE-----')
      ) {
        error = 'Certificate must include BEGIN and END markers';
      }
    }

    setErrors((prev) => ({
      ...prev,
      [name]: error,
    }));
  };

  // Check form validity
  const isFormValid = () =>
    configuration.entryPoint?.trim() !== '' &&
    configuration.certificate?.includes('-----BEGIN CERTIFICATE-----') &&
    configuration.certificate?.includes('-----END CERTIFICATE-----') &&
    !errors.entryPoint &&
    !errors.certificate;

  // Handle save
  const handleSave = async () => {
    setIsSaving(true);

    try {
      // Prepare payload
      const payload = {
        entryPoint: configuration.entryPoint,
        certificate: configuration.certificate,
        emailKey: configuration.emailKey || 'nameID',
        entityId: configuration.entityId,
        logoutUrl: configuration.logoutUrl,
      };

      // Send to API
      await updateSamlSsoConfig(payload);
      showSuccessSnackbar('SAML configuration successfully updated');
    } catch (error) {
      // showErrorSnackbar('Failed to save SAML configuration');
    } finally {
      setIsSaving(false);
    }
  };

  // Handle back button
  const handleBack = () => {
    if (isAdmin && accountType === 'business') {
      navigate('/account/company-settings/settings/authentication');
      return;
    }
    if (isAdmin && accountType === 'individual') {
      navigate('/account/individual/settings/authentication');
      return;
    }
    setSnackbar({
      open: false,
      message: 'No access to the saml settings',
      severity: 'error',
    });
    navigate('/');
  };

  return (
    <Container maxWidth="lg">
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 3, mt: 3 }}>
        <IconButton onClick={handleBack} sx={{ mr: 1, color: theme.palette.text.secondary }}>
          <Iconify icon={arrowBackIcon} width={20} height={20} />
        </IconButton>
        <Typography variant="h6">Back to Authentication Settings</Typography>
      </Box>

      <Paper sx={{ borderRadius: 1, mb: 3, p: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
          <Typography variant="h6">SAML Configuration</Typography>
        </Box>

        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress size={32} />
          </Box>
        ) : (
          <Box>
            <Grid container spacing={3}>
              <Grid item xs={12}>
                <Box sx={{ mb: 2 }}>
                  <Typography variant="subtitle2">IdP Configuration XML</Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                    Upload your Identity Provider&apos;s metadata XML file to automatically populate
                    the fields below
                  </Typography>

                  <Button
                    variant="outlined"
                    component="label"
                    startIcon={<Iconify icon={fileIcon} />}
                    size="small"
                  >
                    Choose XML File
                    <input type="file" accept=".xml" hidden onChange={handleFileUpload} />
                  </Button>
                  {xmlFile && (
                    <Box sx={{ mt: 1, display: 'flex', alignItems: 'center' }}>
                      <Iconify icon={fileTextIcon} width={16} height={16} sx={{ mr: 0.5 }} />
                      <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                        {xmlFile.name}
                      </Typography>
                    </Box>
                  )}
                </Box>
              </Grid>

              <Grid item xs={12}>
                <Typography variant="subtitle2">Entry Point (SSO URL)</Typography>
                <TextField
                  fullWidth
                  name="entryPoint"
                  value={configuration.entryPoint || ''}
                  onChange={handleChange}
                  placeholder="https://idp.example.com/sso/saml"
                  error={Boolean(errors.entryPoint)}
                  helperText={
                    errors.entryPoint || 'The Single Sign-On URL from your Identity Provider'
                  }
                  required
                  size="small"
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
                <Typography variant="subtitle2">Identity Provider Certificate</Typography>
                <StyledTextarea
                  fullWidth
                  name="certificate"
                  value={configuration.certificate || ''}
                  onChange={handleChange}
                  error={Boolean(errors.certificate)}
                  helperText={errors.certificate || 'X.509 certificate provided by your IdP'}
                  required
                  multiline
                  rows={8}
                  size="small"
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
                <Typography variant="subtitle2">Email Attribute Key</Typography>
                <TextField
                  fullWidth
                  name="emailKey"
                  value={configuration.emailKey || 'nameID'}
                  onChange={handleChange}
                  placeholder="nameID"
                  size="small"
                  sx={{
                    '& .MuiOutlinedInput-root': {
                      '& fieldset': {
                        borderColor: alpha(theme.palette.text.primary, 0.15),
                      },
                    },
                  }}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: 'block', mt: 0.5, ml: 0.5 }}
                >
                  The attribute that contains the user&apos;s email address (default is nameID)
                </Typography>
              </Grid>
            </Grid>

            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 3 }}>
              <Button variant="outlined" onClick={handleBack} sx={{ mr: 2 }} size="small">
                Cancel
              </Button>
              <Button
                variant="contained"
                onClick={handleSave}
                disabled={!isFormValid() || isSaving}
                startIcon={isSaving ? <CircularProgress size={16} color="inherit" /> : null}
                size="small"
              >
                {isSaving ? 'Saving...' : 'Save'}
              </Button>
            </Box>
          </Box>
        )}
      </Paper>

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
      <Alert variant="outlined" severity="info" sx={{ my: 3 }}>
        Refer to{' '}
        <Link href="https://docs.pipeshub.com/auth/saml" target="_blank" rel="noopener">
          the documentation
        </Link>{' '}
        for more information.
      </Alert>
    </Container>
  );
};

export default SamlSsoConfigPage;
