import React, { useState, useEffect } from 'react';

import { alpha } from '@mui/material/styles';
import {
  Box,
  Grid,
  Fade,
  Chip,
  Paper,
  Alert,
  Switch,
  Tooltip,
  Snackbar,
  useTheme,
  Typography,
  IconButton,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import {
  getSamlSsoConfig,
  getAzureAuthConfig,
  getGoogleAuthConfig,
  getMicrosoftAuthConfig,
} from '../utils/auth-configuration-service';

// Authentication method type
interface AuthMethod {
  type: string;
  enabled: boolean;
}

// Component props interface
interface AuthMethodsListProps {
  authMethods: AuthMethod[];
  handleToggleMethod: (type: string) => void;
  handleConfigureMethod: (type: string) => void;
  isEditing: boolean;
  isLoading: boolean;
  smtpConfigured: boolean;
  configUpdated?: number; // Timestamp to trigger refresh when config is updated
}

// Configuration status interface
interface ConfigStatus {
  google: boolean;
  microsoft: boolean;
  azureAd: boolean;
  samlSso: boolean;
}

// Configuration for auth methods with icons and descriptions
const AUTH_METHODS_CONFIG = [
  {
    type: 'otp',
    icon: 'mdi:email-lock-outline',
    title: 'One-Time Password',
    description: 'Send a verification code via email',
    configurable: false,
    requiresSmtp: true,
  },
  {
    type: 'password',
    icon: 'solar:lock-linear',
    title: 'Password',
    description: 'Traditional email and password authentication',
    configurable: false,
    requiresSmtp: false,
  },
  {
    type: 'google',
    icon: 'ri:google-line',
    title: 'Google',
    description: 'Allow users to sign in with Google accounts',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
  {
    type: 'microsoft',
    icon: 'ri:microsoft-fill',
    title: 'Microsoft',
    description: 'Allow users to sign in with Microsoft accounts',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
  {
    type: 'azureAd',
    icon: 'solar:cloud-linear',
    title: 'Azure AD',
    description: 'Enterprise authentication via Azure Active Directory',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
  {
    type: 'samlSso',
    icon: 'solar:shield-linear',
    title: 'SAML SSO',
    description: 'Single Sign-On with SAML protocol',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
];

// SMTP configuration item
const SMTP_CONFIG = {
  type: 'smtp',
  icon: 'ic:round-mail-outline',
  title: 'SMTP',
  description: 'Email server configuration for OTP and notifications',
  configurable: true,
  requiresSmtp: false,
};

const AuthMethodsList: React.FC<AuthMethodsListProps> = ({
  authMethods,
  handleToggleMethod,
  handleConfigureMethod,
  isEditing,
  isLoading,
  smtpConfigured,
  configUpdated = 0,
}) => {
  const theme = useTheme();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showError, setShowError] = useState(false);
  const [configStatus, setConfigStatus] = useState<ConfigStatus>({
    google: false,
    microsoft: false,
    azureAd: false,
    samlSso: false,
  });
  const [checkingConfigs, setCheckingConfigs] = useState(true);
  const [lastConfigured, setLastConfigured] = useState<string | null>(null);

  // Check authentication configurations on component mount and when configUpdated changes
  useEffect(() => {
    const checkConfigurations = async () => {
      setCheckingConfigs(true);
      try {
        // Check all configurations in parallel
        const results = await Promise.allSettled([
          getGoogleAuthConfig(),
          getMicrosoftAuthConfig(),
          getAzureAuthConfig(),
          getSamlSsoConfig(),
        ]);

        // Check if each configuration has required fields
        const googleConfigured =
          results[0].status === 'fulfilled' && results[0].value && !!results[0].value.clientId;

        const microsoftConfigured =
          results[1].status === 'fulfilled' &&
          results[1].value &&
          !!results[1].value.clientId &&
          !!results[1].value.tenantId;

        const azureConfigured =
          results[2].status === 'fulfilled' &&
          results[2].value &&
          !!results[2].value.clientId &&
          !!results[2].value.tenantId;

        // Fixed SAML check - making sure it returns a boolean
        const samlConfigured =
          results[3].status === 'fulfilled' &&
          results[3].value &&
          !!results[3].value.emailKey &&
          !!results[3].value.certificate;

        const newConfigStatus = {
          google: googleConfigured,
          microsoft: microsoftConfigured,
          azureAd: azureConfigured,
          samlSso: samlConfigured, // Now this is definitely a boolean value
        };

        setConfigStatus(newConfigStatus);

        // If we just configured a method, show a success message
        if (lastConfigured) {
          const wasConfigured =
            (lastConfigured === 'google' && googleConfigured) ||
            (lastConfigured === 'microsoft' && microsoftConfigured) ||
            (lastConfigured === 'azureAd' && azureConfigured) ||
            (lastConfigured === 'samlSso' && samlConfigured) ||
            (lastConfigured === 'smtp' && smtpConfigured);

          if (wasConfigured) {
            const methodTitle = lastConfigured === 'smtp' ? 'SMTP' : getMethodTitle(lastConfigured);
            setErrorMessage(`${methodTitle} configuration has been successfully applied`);
            setShowError(true);
            setLastConfigured(null);
          }
        }
      } catch (error) {
        setErrorMessage('Error checking authentication configurations:');
      } finally {
        setCheckingConfigs(false);
      }
    };

    checkConfigurations();
  }, [configUpdated, lastConfigured, smtpConfigured]);

  // Helper function to get method title
  const getMethodTitle = (type: string): string => {
    const method = AUTH_METHODS_CONFIG.find((m) => m.type === type);
    return method?.title || type;
  };

  // Check if at least one method is enabled
  useEffect(() => {
    if (isEditing) {
      const enabledCount = authMethods.filter((method) => method.enabled).length;
      if (enabledCount === 0) {
        setErrorMessage('At least one authentication method must be enabled');
        setShowError(true);
      } else if (!(errorMessage && errorMessage.includes('successfully'))) {
        // Hide error only if it's not a success message
        setShowError(false);
      }
    }
  }, [authMethods, isEditing, errorMessage]);

  // Handle toggling with validation
  const handleToggleWithValidation = (type: string) => {
    // Find the current method
    const currentMethod = authMethods.find((m) => m.type === type);

    // If we're trying to disable the only enabled method, show error
    if (currentMethod?.enabled) {
      const enabledCount = authMethods.filter((m) => m.enabled).length;
      if (enabledCount === 1) {
        setErrorMessage('At least one authentication method must be enabled');
        setShowError(true);
        return;
      }
    }
    // If we're trying to enable a method that requires SMTP but SMTP isn't configured
    else if (!currentMethod?.enabled) {
      const methodConfig = AUTH_METHODS_CONFIG.find((m) => m.type === type);

      if (methodConfig?.requiresSmtp && !smtpConfigured) {
        setErrorMessage(
          `${methodConfig.title} requires SMTP configuration before it can be enabled`
        );
        setShowError(true);
        return;
      }

      // If method requires configuration but isn't configured yet
      if (methodConfig?.requiresConfig) {
        // Check if this method is configured
        let isConfigured = false;

        switch (type) {
          case 'google':
            isConfigured = configStatus.google;
            break;
          case 'microsoft':
            isConfigured = configStatus.microsoft;
            break;
          case 'azureAd':
            isConfigured = configStatus.azureAd;
            break;
          case 'samlSso':
            isConfigured = configStatus.samlSso;
            break;
          default:
            break;
        }

        if (!isConfigured) {
          setErrorMessage(`${methodConfig.title} requires configuration before it can be enabled`);
          setShowError(true);
          return;
        }
      }
    }

    // Otherwise proceed with toggle
    handleToggleMethod(type);
  };

  // Handle error snackbar close
  const handleCloseError = () => {
    setShowError(false);
  };

  // Check if a method should be disabled
  const isMethodDisabled = (methodType: string, isEnabled: boolean) => {
    const methodConfig = AUTH_METHODS_CONFIG.find((m) => m.type === methodType);

    // Basic disabled conditions
    if (!isEditing || isLoading || checkingConfigs) return true;

    // Check SMTP requirement
    if (methodConfig?.requiresSmtp && !smtpConfigured) return true;

    // Check configuration requirement if not already enabled
    if (!isEnabled && methodConfig?.requiresConfig) {
      switch (methodType) {
        case 'google':
          return !configStatus.google;
        case 'microsoft':
          return !configStatus.microsoft;
        case 'azureAd':
          return !configStatus.azureAd;
        case 'samlSso':
          return !configStatus.samlSso;
        default:
          break;
      }
    }

    return false;
  };

  // Get tooltip message for a method
  const getTooltipMessage = (methodType: string, isDisabled: boolean) => {
    if (!isDisabled) return '';

    const methodConfig = AUTH_METHODS_CONFIG.find((m) => m.type === methodType);

    if (methodConfig?.requiresSmtp && !smtpConfigured) {
      return 'Requires SMTP configuration';
    }

    if (!isEditing) {
      return 'Edit mode is required to change authentication methods';
    }

    if (methodConfig?.requiresConfig) {
      let isConfigured = false;

      switch (methodType) {
        case 'google':
          isConfigured = configStatus.google;
          break;
        case 'microsoft':
          isConfigured = configStatus.microsoft;
          break;
        case 'azureAd':
          isConfigured = configStatus.azureAd;
          break;
        case 'samlSso':
          isConfigured = configStatus.samlSso;
          break;
        default:
          break;
      }

      if (!isConfigured) {
        return `${methodConfig.title} must be configured before it can be enabled`;
      }
    }

    return '';
  };

  // Enhanced configure method handler that also tracks which method was configured
  const handleConfigureWithTracking = (type: string) => {
    setLastConfigured(type);
    handleConfigureMethod(type);
  };

  return (
    <>
      {/* Error/Success notification */}
      <Snackbar
        open={showError}
        autoHideDuration={6000}
        onClose={handleCloseError}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert
          onClose={handleCloseError}
          severity={errorMessage?.includes('successfully') ? 'success' : 'warning'}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {errorMessage}
        </Alert>
      </Snackbar>

      {/* Section header for Authentication Methods */}
      <Box sx={{ mb: 2, mt: 4 }}>
        <Typography variant="h6" sx={{ fontWeight: 600, mb: 1 }}>
          Authentication Methods
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Select the authentication method users will use to sign in
        </Typography>
      </Box>

      <Grid container spacing={2}>
        {AUTH_METHODS_CONFIG.map((methodConfig) => {
          const currentMethod = authMethods.find((m) => m.type === methodConfig.type);
          const isEnabled = currentMethod?.enabled || false;
          const isDisabled = isMethodDisabled(methodConfig.type, isEnabled);

          // Determine tooltip message based on conditions
          const tooltipMessage = getTooltipMessage(methodConfig.type, isDisabled);

          // Determine if this method is configured
          let isConfigured = false;
          if (methodConfig.requiresConfig) {
            switch (methodConfig.type) {
              case 'google':
                isConfigured = configStatus.google;
                break;
              case 'microsoft':
                isConfigured = configStatus.microsoft;
                break;
              case 'azureAd':
                isConfigured = configStatus.azureAd;
                break;
              case 'samlSso':
                isConfigured = configStatus.samlSso;
                break;
              default:
                break;
            }
          }

          return (
            <Grid item xs={16} sm={12} md={6} key={methodConfig.type}>
              <Fade in={Boolean(true)}>
                <Tooltip
                  title={tooltipMessage}
                  placement="top"
                  arrow
                  disableHoverListener={!tooltipMessage}
                >
                  <Paper
                    elevation={0}
                    sx={{
                      p: 2,
                      display: 'flex',
                      alignItems: 'center',
                      height: 72,
                      borderRadius: 1,
                      border: '1px solid',
                      borderColor: theme.palette.divider,
                      bgcolor: 'transparent',
                      transition: 'all 0.2s ease',
                      opacity: isDisabled && !isEnabled ? 0.6 : 1,
                      ...(isEditing &&
                        !isDisabled && {
                          '&:hover': {
                            borderColor: alpha(theme.palette.primary.main, 0.3),
                            bgcolor: alpha(theme.palette.primary.main, 0.05),
                          },
                        }),
                    }}
                  >
                    {/* Icon container */}
                    <Box
                      sx={{
                        width: 40,
                        height: 40,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        mr: 2,
                        bgcolor: alpha(theme.palette.grey[500], 0.08),
                        color: theme.palette.text.secondary,
                        borderRadius: 1,
                        flexShrink: 0,
                      }}
                    >
                      <Iconify icon={methodConfig.icon} width={22} height={22} />
                    </Box>

                    {/* Content */}
                    <Box
                      sx={{
                        flexGrow: 1,
                        overflow: 'hidden',
                        mr: 2,
                      }}
                    >
                      <Typography
                        variant="subtitle2"
                        sx={{
                          fontWeight: 500,
                          color: theme.palette.text.primary,
                        }}
                      >
                        {methodConfig.title}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{
                          display: '-webkit-box',
                          WebkitLineClamp: 1,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {methodConfig.description}
                      </Typography>
                    </Box>

                    {/* Status indicators */}
                    {isEnabled && (
                      <Chip
                        label="Enabled"
                        size="small"
                        sx={{
                          height: 24,
                          fontSize: '0.75rem',
                          mr: 1.5,
                          bgcolor: alpha(theme.palette.success.main, 0.08),
                          color: theme.palette.success.main,
                          borderColor: alpha(theme.palette.success.main, 0.2),
                          fontWeight: 500,
                          border: '1px solid',
                        }}
                      />
                    )}

                    {/* Configuration status indicator */}
                    {methodConfig.requiresConfig && (
                      <Chip
                        label={isConfigured ? 'Configured' : 'Not Configured'}
                        size="small"
                        sx={{
                          height: 24,
                          fontSize: '0.75rem',
                          mr: 1.5,
                          bgcolor: isConfigured
                            ? alpha(theme.palette.info.main, 0.08)
                            : alpha(theme.palette.warning.main, 0.08),
                          color: isConfigured
                            ? theme.palette.info.main
                            : theme.palette.warning.main,
                          borderColor: isConfigured
                            ? alpha(theme.palette.info.main, 0.2)
                            : alpha(theme.palette.warning.main, 0.2),
                          fontWeight: 500,
                          border: '1px solid',
                        }}
                      />
                    )}

                    {/* Warning for OTP */}
                    {methodConfig.type === 'otp' && !smtpConfigured && isEditing && (
                      <Tooltip title="SMTP must be configured first">
                        <Box
                          sx={{
                            color: theme.palette.warning.main,
                            display: 'flex',
                            mr: 1.5,
                          }}
                        >
                          <Iconify icon="solar:danger-triangle-linear" width={18} height={18} />
                        </Box>
                      </Tooltip>
                    )}

                    {/* Actions */}
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      {methodConfig.configurable && (
                        <IconButton
                          size="small"
                          onClick={() => handleConfigureWithTracking(methodConfig.type)}
                          sx={{
                            color: theme.palette.text.secondary,
                            '&:hover': {
                              bgcolor: alpha(theme.palette.primary.main, 0.08),
                              color: theme.palette.primary.main,
                            },
                          }}
                        >
                          <Iconify icon="solar:settings-linear" width={18} height={18} />
                        </IconButton>
                      )}

                      <Switch
                        checked={isEnabled}
                        onChange={() =>
                          !isDisabled && handleToggleWithValidation(methodConfig.type)
                        }
                        disabled={isDisabled}
                        size="small"
                        sx={{
                          '& .MuiSwitch-switchBase.Mui-checked': {
                            color: theme.palette.primary.main,
                          },
                          '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                            backgroundColor: alpha(theme.palette.primary.main, 0.5),
                          },
                        }}
                      />
                    </Box>
                  </Paper>
                </Tooltip>
              </Fade>
            </Grid>
          );
        })}
      </Grid>

      {/* Section header for Configuration */}
      <Box sx={{ mb: 2, mt: 4 }}>
        <Typography variant="h6" sx={{ fontWeight: 600, mb: 1 }}>
          Server Configuration
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Configure email and other server settings for authentication
        </Typography>
      </Box>

      {/* SMTP Configuration Card */}
      <Grid container spacing={2}>
        <Grid item xs={12} sm={10} md={6}>
          <Fade in={Boolean(true)}>
            <Paper
              elevation={0}
              sx={{
                p: 2,
                display: 'flex',
                alignItems: 'center',
                height: 72,
                borderRadius: 1,
                border: '1px solid',
                borderColor: theme.palette.divider,
                bgcolor: 'transparent',
                transition: 'all 0.2s ease',
                '&:hover': {
                  borderColor: smtpConfigured
                    ? alpha(theme.palette.success.main, 0.3)
                    : alpha(theme.palette.warning.main, 0.3),
                },
              }}
            >
              {/* Icon container */}
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  mr: 2,
                  bgcolor: alpha(theme.palette.grey[500], 0.08),
                  color: theme.palette.text.secondary,
                  borderRadius: 1,
                  flexShrink: 0,
                }}
              >
                <Iconify icon={SMTP_CONFIG.icon} width={22} height={22} />
              </Box>

              {/* Content */}
              <Box
                sx={{
                  flexGrow: 1,
                  overflow: 'hidden',
                  mr: 2,
                }}
              >
                <Typography
                  variant="subtitle2"
                  sx={{
                    fontWeight: 500,
                    color: theme.palette.text.secondary,
                  }}
                >
                  {SMTP_CONFIG.title}
                </Typography>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    display: '-webkit-box',
                    WebkitLineClamp: 1,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {SMTP_CONFIG.description}
                </Typography>
              </Box>

              {/* Status indicator */}
              <Chip
                label={smtpConfigured ? 'Configured' : 'Not Configured'}
                size="small"
                sx={{
                  height: 24,
                  fontSize: '0.75rem',
                  mr: 1.5,
                  bgcolor: smtpConfigured
                    ? alpha(theme.palette.success.main, 0.08)
                    : alpha(theme.palette.warning.main, 0.08),
                  color: smtpConfigured ? theme.palette.success.main : theme.palette.warning.main,
                  borderColor: smtpConfigured
                    ? alpha(theme.palette.success.main, 0.2)
                    : alpha(theme.palette.warning.main, 0.2),
                  fontWeight: 500,
                  border: '1px solid',
                }}
              />

              {/* Configure button */}
              <IconButton
                size="small"
                onClick={() => handleConfigureWithTracking('smtp')}
                sx={{
                  color: smtpConfigured ? theme.palette.success.main : theme.palette.warning.main,
                  '&:hover': {
                    bgcolor: smtpConfigured
                      ? alpha(theme.palette.success.main, 0.08)
                      : alpha(theme.palette.warning.main, 0.08),
                  },
                }}
              >
                <Iconify icon="solar:settings-linear" width={18} height={18} />
              </IconButton>
            </Paper>
          </Fade>
        </Grid>
      </Grid>
    </>
  );
};

export default AuthMethodsList;
