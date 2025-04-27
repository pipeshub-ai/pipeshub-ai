import { useState, useEffect } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Link,
  Paper,
  Alert,
  Snackbar,
  Container,
  Typography,
  AlertTitle,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import AuthMethodsList from './components/auth-methods-list';
// Component imports
import AuthMethodsHeader from './components/auth-methods-header';
import ConfigureMethodDialog from './components/configure-method-dialog';
import { validateOtpConfiguration, validateSingleMethodSelection } from './utils/validations';

import type { AuthMethod } from './utils/validations';

// API schema for validation
const AUTH_METHOD_TYPES = ['password', 'otp', 'google', 'microsoft', 'azureAd', 'samlSso'];

// Main component
const AuthenticationSettings: React.FC = () => {
  const theme = useTheme();
  const [authMethods, setAuthMethods] = useState<AuthMethod[]>([]);
  const [isEditing, setIsEditing] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [configureDialogOpen, setConfigureDialogOpen] = useState(false);
  const [currentConfigMethod, setCurrentConfigMethod] = useState<string | null>(null);
  const [smtpConfigured, setSmtpConfigured] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error',
  });

  // Fetch auth methods from API
  useEffect(() => {
    fetchAuthMethods();
    checkSmtpConfiguration();
    // eslint-disable-next-line
  }, []);

  // Check if SMTP is configured for OTP validation
  const checkSmtpConfiguration = async () => {
    try {
      // This would be the actual API call to check SMTP configuration
      const response = await axios.get('/api/v1/configurationManager/smtpConfig');
      // Set SMTP as configured if we have at least host and from fields
      setSmtpConfigured(!!response.data?.host && !!response.data?.fromEmail);
    } catch (err) {
      setSmtpConfigured(false);
      // showErrorSnackbar('Failed to check SMTP configuration');
    }
  };

  const fetchAuthMethods = async () => {
    setIsLoading(true);
    try {
      // API call to get current auth methods
      const response = await axios.get('/api/v1/orgAuthConfig/authMethods');
      const { data } = response;

      // Get all allowed methods from the API
      const enabledMethodTypes = new Set<string>();
      data.authMethods.forEach((method: any) => {
        method.allowedMethods.forEach((allowedMethod: any) => {
          enabledMethodTypes.add(allowedMethod.type);
        });
      });

      // Create a complete list with enabled flag
      const allMethods = AUTH_METHOD_TYPES.map((type) => ({
        type,
        enabled: enabledMethodTypes.has(type),
      }));

      setAuthMethods(allMethods);
    } catch (err) {
      setError('Failed to load authentication settings');
      // showErrorSnackbar('Failed to load authentication settings');
    } finally {
      setIsLoading(false);
    }
  };

  // Handle save changes
  const handleSaveChanges = async () => {
    setIsLoading(true);
    setError(null);

    try {
      // Validate that only one method is enabled
      const enabledMethods = authMethods.filter((method) => method.enabled);

      // Validation: Single authentication method
      if (!validateSingleMethodSelection(enabledMethods)) {
        setError('Only one authentication method can be enabled at a time');
        showErrorSnackbar('Only one authentication method can be enabled at a time');
        setIsLoading(false);
        return;
      }

      // Validation: OTP requires SMTP configuration
      if (!validateOtpConfiguration(enabledMethods, smtpConfigured)) {
        setError('OTP authentication requires SMTP configuration. Please configure SMTP first.');
        showErrorSnackbar(
          'OTP authentication requires SMTP configuration. Please configure SMTP first.'
        );
        setIsLoading(false);
        return;
      }

      // Create an API-compatible payload
      const payload = {
        authMethod: [
          {
            order: 1,
            allowedMethods: enabledMethods.map(({ type }) => ({ type })),
          },
        ],
      };

      // API call to update auth methods
      await axios.post('/api/v1/orgAuthConfig/updateAuthMethod', payload);

      // Show success message
      showSuccessSnackbar('Authentication settings updated successfully');
      setIsEditing(false);

      // Refresh data
      fetchAuthMethods();
    } catch (err) {
      setError('Failed to save changes. Please try again.');
      // showErrorSnackbar('Failed to save changes. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  // Helper functions for snackbars
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

  // Handle toggling auth methods - allow only one active method
  // Updated handleToggleMethod - allow proper toggling but respect single-method constraint
  const handleToggleMethod = (type: string) => {
    setAuthMethods((prev) => {
      const method = prev.find((m) => m.type === type);

      // If trying to disable the only enabled method, prevent it
      const enabledCount = prev.filter((m) => m.enabled).length;
      if (method?.enabled && enabledCount === 1) {
        return prev; // Don't allow disabling the only enabled method
      }

      // If enabling this method, disable all others (for single method selection)
      if (!method?.enabled) {
        return prev.map((m) => ({
          ...m,
          enabled: m.type === type,
        }));
      }

      // Otherwise just toggle the specific method
      return prev.map((m) => ({
        ...m,
        enabled: m.type === type ? !m.enabled : m.enabled,
      }));
    });
  };

  // Handle opening the configure dialog
  const handleConfigureMethod = (type: string) => {
    setCurrentConfigMethod(type);
    setConfigureDialogOpen(true);
  };

  // Handle save in configure dialog
  const handleSaveConfiguration = () => {
    // Refresh SMTP status if we just configured SMTP
    if (currentConfigMethod === 'smtp') {
      checkSmtpConfiguration();
      showSuccessSnackbar('SMTP configuration updated successfully');
    } else {
      showSuccessSnackbar('Authentication provider configured successfully');
    }

    setConfigureDialogOpen(false);
    setCurrentConfigMethod(null);

    // Exit edit mode if we were in it
    if (isEditing) {
      setIsEditing(false);
    }
  };

  // Handle cancel editing
  const handleCancelEdit = () => {
    setIsEditing(false);
    fetchAuthMethods();
  };

  return (
    <Container maxWidth="lg">
      <Paper
        sx={{
          overflow: 'hidden',
          position: 'relative',
          p: 3,
          borderRadius: 2,
        }}
      >
        {/* Loading overlay */}
        {isLoading && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: alpha(theme.palette.background.paper, 0.7),
              zIndex: 10,
            }}
          >
            <CircularProgress size={32} />
          </Box>
        )}

        {/* Header section */}
        <Box
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            justifyContent: 'space-between',
            alignItems: { xs: 'flex-start', sm: 'center' },
            mb: 4,
            gap: 2,
          }}
        >
          <Box>
            <Typography
              variant="h5"
              component="h1"
              sx={{
                fontWeight: 600,
                mb: 1,
                color: theme.palette.text.primary,
              }}
            >
              Authentication Settings
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 500 }}>
              Configure how users sign in to your application
            </Typography>
          </Box>
        </Box>

        {/* Error message */}
        {error && (
          <Alert
            severity="error"
            onClose={() => setError(null)}
            sx={{
              mb: 3,
              borderRadius: 1,
              border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
              '& .MuiAlert-icon': {
                color: theme.palette.error.main,
              },
            }}
          >
            <AlertTitle sx={{ fontWeight: 500 }}>Error</AlertTitle>
            {error}
          </Alert>
        )}

        {/* Current active methods header */}
        <AuthMethodsHeader
          authMethods={authMethods}
          isEditing={isEditing}
          setIsEditing={setIsEditing}
          handleSaveChanges={handleSaveChanges}
          handleCancelEdit={handleCancelEdit}
          isLoading={isLoading}
        />

        {/* Authentication methods list - now includes SMTP configuration */}
        <AuthMethodsList
          authMethods={authMethods}
          handleToggleMethod={handleToggleMethod}
          handleConfigureMethod={handleConfigureMethod}
          isEditing={isEditing}
          isLoading={isLoading}
          smtpConfigured={smtpConfigured}
        />

        {/* Info box */}
        <Box
          sx={{
            mt: 4,
            p: 3,
            borderRadius: 2,
            bgcolor: alpha(theme.palette.primary.main, 0.04),
            border: `1px solid ${alpha(theme.palette.primary.main, 0.1)}`,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 2,
          }}
        >
          <Box sx={{ color: theme.palette.info.main, mt: 0.5 }}>
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M12 16V12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M12 8H12.01"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Box>
          <Box>
            <Typography variant="subtitle2" color="text.primary" sx={{ mb: 0.5, fontWeight: 500 }}>
              Authentication Method Policy
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Only one authentication method can be active at a time. To change the method, please
              disable the current one and enable a different method.
              {!smtpConfigured && (
                <Box
                  component="span"
                  sx={{
                    display: 'block',
                    mt: 1,
                    color: theme.palette.warning.dark,
                    fontWeight: 500,
                  }}
                >
                  Note: OTP authentication requires SMTP configuration. Please configure SMTP
                  settings first.
                </Box>
              )}
            </Typography>
          </Box>
        </Box>
      </Paper>

      {/* Configure Method Dialog - handles both auth methods and SMTP */}
      <ConfigureMethodDialog
        open={configureDialogOpen}
        onClose={() => setConfigureDialogOpen(false)}
        onSave={handleSaveConfiguration}
        methodType={currentConfigMethod}
      />

      {/* Snackbar for success and error messages */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
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
        <Link href="https://docs.pipeshub.com/auth" target="_blank" rel="noopener">
          the documentation
        </Link>{' '}
        for more information.
      </Alert>
    </Container>
  );
};

export default AuthenticationSettings;
