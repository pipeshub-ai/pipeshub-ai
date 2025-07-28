import React, { useState, useEffect } from 'react';
// Ri icons
import googleLineIcon from '@iconify-icons/ri/google-line';
// Solar icons
import lockLinearIcon from '@iconify-icons/solar/lock-linear';
import cloudLinearIcon from '@iconify-icons/solar/cloud-linear';
import settingsIcon from '@iconify-icons/solar/settings-linear';
import microsoftFillIcon from '@iconify-icons/ri/microsoft-fill';
import shieldLinearIcon from '@iconify-icons/solar/shield-linear';
// IC icons
import mailOutlineIcon from '@iconify-icons/ic/round-mail-outline';
import dangerIcon from '@iconify-icons/solar/danger-triangle-linear';
import emailLockOutlineIcon from '@iconify-icons/mdi/email-lock-outline';

import { alpha } from '@mui/material/styles';
import {
  Box,
  Grid,
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
  getOAuthConfig,
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
  oauth: boolean;
}

// Configuration for auth methods with icons and descriptions
const AUTH_METHODS_CONFIG = [
  {
    type: 'otp',
    icon: emailLockOutlineIcon,
    title: 'One-Time Password',
    description: 'Send a verification code via email',
    configurable: false,
    requiresSmtp: true,
  },
  {
    type: 'password',
    icon: lockLinearIcon,
    title: 'Password',
    description: 'Traditional email and password authentication',
    configurable: false,
    requiresSmtp: false,
  },
  {
    type: 'google',
    icon: googleLineIcon,
    title: 'Google',
    description: 'Allow users to sign in with Google accounts',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
  {
    type: 'microsoft',
    icon: microsoftFillIcon,
    title: 'Microsoft',
    description: 'Allow users to sign in with Microsoft accounts',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
  {
    type: 'azureAd',
    icon: cloudLinearIcon,
    title: 'Azure AD',
    description: 'Enterprise authentication via Azure Active Directory',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
  {
    type: 'samlSso',
    icon: shieldLinearIcon,
    title: 'SAML SSO',
    description: 'Single Sign-On with SAML protocol',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
  {
    type: 'oauth',
    icon: 'mdi:key-variant',
    title: 'OAuth',
    description: 'Generic OAuth 2.0 provider authentication',
    configurable: true,
    requiresSmtp: false,
    requiresConfig: true,
  },
];

// SMTP configuration item
const SMTP_CONFIG = {
  type: 'smtp',
  icon: mailOutlineIcon,
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
    oauth: false,
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
          getOAuthConfig(),
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

        // Check OAuth configuration
        const oauthConfigured =
          results[4].status === 'fulfilled' &&
          results[4].value &&
          !!results[4].value.clientId &&
          !!results[4].value.providerName;


        const newConfigStatus = {
          google: googleConfigured,
          microsoft: microsoftConfigured,
          azureAd: azureConfigured,
          samlSso: samlConfigured, // Now this is definitely a boolean value
          oauth: oauthConfigured,
        };

        setConfigStatus(newConfigStatus);

        // If we just configured a method, show a success message
        if (lastConfigured) {
          const wasConfigured =
            (lastConfigured === 'google' && googleConfigured) ||
            (lastConfigured === 'microsoft' && microsoftConfigured) ||
            (lastConfigured === 'azureAd' && azureConfigured) ||
            (lastConfigured === 'samlSso' && samlConfigured) ||
            (lastConfigured === 'oauth' && oauthConfigured) ||
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
          case 'oauth':
            isConfigured = configStatus.oauth;
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
        case 'oauth':
          return !configStatus.oauth;
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
        case 'oauth':
          isConfigured = configStatus.oauth;
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
        autoHideDuration={4000}
        onClose={handleCloseError}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseError}
          severity={errorMessage?.includes('successfully') ? 'success' : 'warning'}
          variant="filled"
          sx={{ 
            width: '100%',
            boxShadow: theme.palette.mode === 'dark'
              ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
              : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {errorMessage}
        </Alert>
      </Snackbar>

      {/* Section header for Authentication Methods */}
      <Box sx={{ mb: 2, mt: 3 }}>
        <Typography 
          variant="h6" 
          sx={{ 
            fontWeight: 600, 
            mb: 0.5,
            fontSize: '1rem',
          }}
        >
          Authentication Methods
        </Typography>
        <Typography 
          variant="body2" 
          color="text.secondary"
          sx={{ 
            fontSize: '0.8125rem',
            lineHeight: 1.5 
          }}
        >
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
              case 'oauth':
                isConfigured = configStatus.oauth;
                break;
              default:
                break;
            }
          }

          return (
            <Grid item xs={12} sm={12} md={6} key={methodConfig.type}>
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
                    minHeight: 68,
                    borderRadius: 1,
                    border: '1px solid',
                    borderColor: isEnabled 
                      ? alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.3 : 0.2) 
                      : theme.palette.divider,
                    bgcolor: isEnabled 
                      ? alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.05 : 0.02)
                      : 'transparent',
                    transition: 'all 0.15s ease',
                    opacity: isDisabled && !isEnabled ? 0.7 : 1,
                    ...(isEditing &&
                      !isDisabled && {
                        '&:hover': {
                          borderColor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.4 : 0.3),
                          bgcolor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.08 : 0.04),
                          transform: 'translateY(-1px)',
                        },
                      }),
                  }}
                >
                  {/* Icon container */}
                  <Box
                    sx={{
                      width: 36,
                      height: 36,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mr: 2,
                      bgcolor: theme.palette.mode === 'dark' 
                        ? alpha(theme.palette.background.paper, 0.3)
                        : alpha(theme.palette.grey[100], 0.8),
                      color: isEnabled
                        ? theme.palette.primary.main
                        : theme.palette.text.secondary,
                      borderRadius: 1,
                      flexShrink: 0,
                      border: '1px solid',
                      borderColor: theme.palette.divider,
                    }}
                  >
                    <Iconify icon={methodConfig.icon} width={20} height={20} />
                  </Box>

                  {/* Content */}
                  <Box
                    sx={{
                      flexGrow: 1,
                      overflow: 'hidden',
                      mr: 1,
                    }}
                  >
                    <Typography
                      variant="subtitle2"
                      sx={{
                        fontWeight: 600,
                        fontSize: '0.875rem',
                        color: isEnabled
                          ? theme.palette.primary.main
                          : theme.palette.text.primary,
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
                        fontSize: '0.75rem',
                        lineHeight: 1.4,
                      }}
                    >
                      {methodConfig.description}
                    </Typography>
                  </Box>

                  {/* Status indicators */}
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mr: 1 }}>
                    {isEnabled && (
                      <Box
                        sx={{
                          height: 22,
                          fontSize: '0.6875rem',
                          px: 1,
                          display: 'flex',
                          alignItems: 'center',
                          borderRadius: 0.75,
                          bgcolor: theme.palette.mode === 'dark' 
                            ? alpha(theme.palette.success.main, 0.15)
                            : alpha(theme.palette.success.main, 0.08),
                          color: theme.palette.success.main,
                          fontWeight: 600,
                        }}
                      >
                        <Box
                          sx={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            bgcolor: 'currentColor',
                            mr: 0.5,
                          }}
                        />
                        Enabled
                      </Box>
                    )}

                    {/* Configuration status indicator */}
                    {methodConfig.requiresConfig && (
                      <Box
                        sx={{
                          height: 22,
                          fontSize: '0.6875rem',
                          px: 1,
                          display: 'flex',
                          alignItems: 'center',
                          borderRadius: 0.75,
                          bgcolor: theme.palette.mode === 'dark' 
                            ? alpha(isConfigured ? theme.palette.info.main : theme.palette.warning.main, 0.15)
                            : alpha(isConfigured ? theme.palette.info.main : theme.palette.warning.main, 0.08),
                          color: isConfigured ? theme.palette.info.main : theme.palette.warning.main,
                          fontWeight: 650,
                          minWidth:'98px'
                        }}
                      >
                        {isConfigured ? 'Configured' : 'Not Configured'}
                      </Box>
                    )}
                  </Box>

                  {/* Warning for OTP */}
                  {methodConfig.type === 'otp' && !smtpConfigured && isEditing && (
                    <Tooltip title="SMTP must be configured first">
                      <Box
                        sx={{
                          color: theme.palette.warning.main,
                          display: 'flex',
                          mr: 1,
                        }}
                      >
                        <Iconify icon={dangerIcon} width={18} height={18} />
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
                          p: 0.75,
                          color: theme.palette.text.secondary,
                          bgcolor: theme.palette.mode === 'dark' 
                            ? alpha(theme.palette.background.paper, 0.3)
                            : alpha(theme.palette.background.default, 0.8),
                          border: '1px solid',
                          borderColor: theme.palette.divider,
                          '&:hover': {
                            bgcolor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.15 : 0.08),
                            color: theme.palette.primary.main,
                          },
                        }}
                      >
                        <Iconify icon={settingsIcon} width={18} height={18} />
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
                        ml: 0.5,
                        '& .MuiSwitch-switchBase.Mui-checked': {
                          color: theme.palette.primary.main,
                        },
                        '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                          backgroundColor: alpha(theme.palette.primary.main, 0.5),
                        },
                        '& .MuiSwitch-track': {
                          opacity: 0.8,
                        },
                      }}
                    />
                  </Box>
                </Paper>
              </Tooltip>
            </Grid>
          );
        })}
      </Grid>

      {/* Section header for Configuration */}
      <Box sx={{ mb: 2, mt: 3 }}>
        <Typography 
          variant="h6" 
          sx={{ 
            fontWeight: 600, 
            mb: 0.5,
            fontSize: '1rem',
          }}
        >
          Server Configuration
        </Typography>
        <Typography 
          variant="body2" 
          color="text.secondary"
          sx={{ 
            fontSize: '0.8125rem',
            lineHeight: 1.5 
          }}
        >
          Configure email and other server settings for authentication
        </Typography>
      </Box>

      {/* SMTP Configuration Card */}
      <Grid container spacing={2}>
        <Grid item xs={12} sm={10} md={6}>
          <Paper
            elevation={0}
            sx={{
              p: 2,
              display: 'flex',
              alignItems: 'center',
              minHeight: 68,
              borderRadius: 1,
              border: '1px solid',
              borderColor: smtpConfigured 
                ? alpha(theme.palette.success.main, theme.palette.mode === 'dark' ? 0.3 : 0.2) 
                : alpha(theme.palette.warning.main, theme.palette.mode === 'dark' ? 0.3 : 0.2),
              bgcolor: smtpConfigured 
                ? alpha(theme.palette.success.main, theme.palette.mode === 'dark' ? 0.05 : 0.02)
                : alpha(theme.palette.warning.main, theme.palette.mode === 'dark' ? 0.05 : 0.02),
              transition: 'all 0.15s ease',
              '&:hover': {
                borderColor: smtpConfigured 
                  ? alpha(theme.palette.success.main, theme.palette.mode === 'dark' ? 0.4 : 0.3)
                  : alpha(theme.palette.warning.main, theme.palette.mode === 'dark' ? 0.4 : 0.3),
                transform: 'translateY(-1px)',
              },
            }}
          >
            {/* Icon container */}
            <Box
              sx={{
                width: 36,
                height: 36,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                mr: 2,
                bgcolor: theme.palette.mode === 'dark' 
                  ? alpha(theme.palette.background.paper, 0.3)
                  : alpha(theme.palette.grey[100], 0.8),
                color: smtpConfigured ? theme.palette.success.main : theme.palette.warning.main,
                borderRadius: 1,
                flexShrink: 0,
                border: '1px solid',
                borderColor: theme.palette.divider,
              }}
            >
              <Iconify icon={SMTP_CONFIG.icon} width={20} height={20} />
            </Box>

            {/* Content */}
            <Box
              sx={{
                flexGrow: 1,
                overflow: 'hidden',
                mr: 1,
              }}
            >
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  color: smtpConfigured ? theme.palette.success.main : theme.palette.warning.main,
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
                  fontSize: '0.75rem',
                  lineHeight: 1.4,
                }}
              >
                {SMTP_CONFIG.description}
              </Typography>
            </Box>

            {/* Status indicator */}
            <Box
              sx={{
                height: 22,
                fontSize: '0.6875rem',
                px: 1,
                display: 'flex',
                alignItems: 'center',
                borderRadius: 0.75,
                mr: 1.5,
                bgcolor: theme.palette.mode === 'dark' 
                  ? alpha(smtpConfigured ? theme.palette.success.main : theme.palette.warning.main, 0.15)
                  : alpha(smtpConfigured ? theme.palette.success.main : theme.palette.warning.main, 0.08),
                color: smtpConfigured ? theme.palette.success.main : theme.palette.warning.main,
                fontWeight: 600,
              }}
            >
              <Box
                sx={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  bgcolor: 'currentColor',
                  mr: 0.5,
                }}
              />
              {smtpConfigured ? 'Configured' : 'Not Configured'}
            </Box>

            {/* Configure button */}
            <IconButton
              size="small"
              onClick={() => handleConfigureWithTracking('smtp')}
              sx={{
                p: 0.75,
                color: theme.palette.text.secondary,
                bgcolor: theme.palette.mode === 'dark' 
                  ? alpha(theme.palette.background.paper, 0.3)
                  : alpha(theme.palette.background.default, 0.8),
                border: '1px solid',
                borderColor: theme.palette.divider,
                '&:hover': {
                  bgcolor: alpha(
                    smtpConfigured ? theme.palette.success.main : theme.palette.warning.main,
                    theme.palette.mode === 'dark' ? 0.15 : 0.08
                  ),
                  color: smtpConfigured ? theme.palette.success.main : theme.palette.warning.main,
                },
              }}
            >
              <Iconify icon={settingsIcon} width={18} height={18} />
            </IconButton>
          </Paper>
        </Grid>
      </Grid>
    </>
  );
};

export default AuthMethodsList;