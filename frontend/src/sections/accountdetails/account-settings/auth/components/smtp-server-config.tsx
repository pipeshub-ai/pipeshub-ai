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

  handleConfigureMethod: (type: string) => void;

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


// SMTP configuration item
const SMTP_CONFIG = {
  type: 'smtp',
  icon: mailOutlineIcon,
  title: 'SMTP',
  description: 'Email server configuration for OTP and notifications',
  configurable: true,
  requiresSmtp: false,
};

const SmtpServerConfig: React.FC<AuthMethodsListProps> = ({

  handleConfigureMethod,

  smtpConfigured,
  configUpdated = 0,
}) => {
  const theme = useTheme();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showError, setShowError] = useState(false);

  const [checkingConfigs, setCheckingConfigs] = useState(true);
  const [lastConfigured, setLastConfigured] = useState<string | null>(null);


  useEffect(() => {
    const checkConfigurations = async () => {
      setCheckingConfigs(true);
      try {
        if (lastConfigured) {
          const wasConfigured =

            (lastConfigured === 'smtp' && smtpConfigured);

          if (wasConfigured) {
            const methodTitle = 'SMTP';
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



  const handleCloseError = () => {
    setShowError(false);
  };


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

export default SmtpServerConfig;