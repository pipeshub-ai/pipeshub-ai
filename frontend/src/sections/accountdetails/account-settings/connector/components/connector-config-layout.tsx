import React, { ReactNode } from 'react';
import infoIcon from '@iconify-icons/eva/info-outline';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Grid,
  Alert,
  Paper,
  Switch,
  Tooltip,
  Snackbar,
  Container,
  Typography,
  AlertTitle,
  IconButton,
  CircularProgress,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import { getConnectorById, type ConnectorConfig } from './connectors-list';
import { useConnectorManager } from '../hooks/use-connector-manager';
import ConnectorStatistics from '../connector-stats';

interface ConnectorConfigLayoutProps {
  connectorId: string;
  accountType?: 'individual' | 'business';
  children?: ReactNode;
  configDialogComponent?: React.ComponentType<any>;
  showStats?: boolean;
  statsConnectorNames?: string[];
}

export const ConnectorConfigLayout = ({
  connectorId,
  accountType = 'business',
  children,
  configDialogComponent,
  showStats = true,
  statsConnectorNames,
}: ConnectorConfigLayoutProps) => {
  const theme = useTheme();
  const connector = getConnectorById(connectorId);
  
  const {
    isLoading,
    errorMessage,
    success,
    successMessage,
    configDialogOpen,
    currentConnector,
    checkingConfigs,
    lastConfigured,
    connectorStatus,
    configuredStatus,
    setConfigDialogOpen,
    setCurrentConnector,
    setSuccess,
    setErrorMessage,
    handleToggleConnector,
    handleFileRemoved,
    getConnectorTitle,
  } = useConnectorManager({ connectorId, accountType });

  if (!connector) {
    return (
      <Container maxWidth="lg">
        <Alert severity="error">
          <AlertTitle>Error</AlertTitle>
          Connector not found: {connectorId}
        </Alert>
      </Container>
    );
  }

  const isConfigured = configuredStatus[connectorId];
  const isEnabled = connectorStatus[connectorId];
  const connectorNames = statsConnectorNames || connector.supportedServices || [];

  const handleConfigureClick = () => {
    setCurrentConnector(connectorId);
    setConfigDialogOpen(true);
  };

  const handleToggle = (enabled: boolean) => {
    handleToggleConnector(connectorId, enabled);
  };

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          position: 'relative',
          p: { xs: 2, md: 3 },
          borderRadius: 1,
          border: '1px solid',
          borderColor: 'divider',
          backgroundColor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.paper, 0.6)
              : theme.palette.background.paper,
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
              backdropFilter: 'blur(4px)',
              zIndex: 10,
            }}
          >
            <CircularProgress size={28} />
          </Box>
        )}

        {/* Header section */}
        <Box
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            justifyContent: 'space-between',
            alignItems: { xs: 'flex-start', sm: 'center' },
            mb: 3,
            gap: 2,
          }}
        >
          <Box>
            <Typography
              variant="h5"
              component="h1"
              sx={{
                fontWeight: 600,
                mb: 0.5,
                fontSize: '1.25rem',
                color: theme.palette.text.primary,
              }}
            >
              {connector.title} Configuration
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: 500,
                lineHeight: 1.5,
              }}
            >
              {connector.description}
            </Typography>
          </Box>
        </Box>

        {/* Error message */}
        {errorMessage && (
          <Alert
            severity="error"
            onClose={() => setErrorMessage(null)}
            sx={{
              mb: 3,
              borderRadius: 1,
              border: 'none',
              '& .MuiAlert-icon': {
                color: theme.palette.error.main,
              },
            }}
          >
            <AlertTitle sx={{ fontWeight: 500, fontSize: '0.875rem' }}>Error</AlertTitle>
            <Typography variant="body2">{errorMessage}</Typography>
          </Alert>
        )}

        {/* Success Snackbar */}
        <Snackbar
          open={success}
          autoHideDuration={8000}
          onClose={() => setSuccess(false)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          sx={{ 
            zIndex: 9999,
            mb: 2,
            mr: 2
          }}
        >
          <Alert 
            onClose={() => setSuccess(false)} 
            severity="success" 
            sx={{ width: '100%' }}
          >
            {successMessage}
          </Alert>
        </Snackbar>

                {/* Info box */}
                <Box
          sx={{
            mt: 3,
            p: 2.5,
            borderRadius: 1,
            bgcolor: theme.palette.mode === 'dark' 
              ? alpha(theme.palette.info.main, 0.08)
              : alpha(theme.palette.info.main, 0.04),
            border: `1px solid ${alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.2 : 0.1)}`,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 1.5,
          }}
        >
          <Box sx={{ color: theme.palette.info.main, mt: 0.5 }}>
            <Iconify icon={infoIcon} width={18} height={18} />
          </Box>
          <Box>
            <Typography 
              variant="subtitle2" 
              color="text.primary" 
              sx={{ 
                mb: 0.5, 
                fontWeight: 600,
                fontSize: '0.875rem' 
              }}
            >
              Connector Configuration
            </Typography>
            <Typography 
              variant="body2" 
              color="text.secondary"
              sx={{
                fontSize: '0.8125rem',
                lineHeight: 1.5
              }}
            >
              Connectors must be properly configured before they can be enabled. Click the settings
              icon to set up the necessary credentials and authentication for each service. Once
              configured, you can enable or disable the connector as needed.
            </Typography>
          </Box>
        </Box>
        <br />

        {/* Connector Configuration Section */}
        <Box sx={{ mb: 3 }}>
          <Paper
            elevation={0}
            sx={{
              p: 2,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderRadius: 1,
              border: '1px solid',
              borderColor: isEnabled
                ? alpha(connector.color, theme.palette.mode === 'dark' ? 0.2 : 0.3)
                : theme.palette.divider,
              bgcolor: isEnabled
                ? alpha(connector.color, theme.palette.mode === 'dark' ? 0.05 : 0.03)
                : 'transparent',
              transition: 'all 0.15s ease-in-out',
              '&:hover': {
                transform: 'translateY(-2px)',
                boxShadow:
                  theme.palette.mode === 'dark'
                    ? `0 4px 12px ${alpha('#000', 0.15)}`
                    : `0 4px 12px ${alpha(theme.palette.grey[500], 0.1)}`,
                borderColor: alpha(
                  connector.color,
                  theme.palette.mode === 'dark' ? 0.3 : 0.4
                ),
              },
            }}
          >
            {/* Connector info */}
            <Box sx={{ display: 'flex', alignItems: 'center', flexGrow: 1 }}>
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  mr: 2,
                  bgcolor: alpha(connector.color, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                  color: connector.color,
                  borderRadius: 1,
                }}
              >
                {connector.src ? (
                  <img src={connector.src} alt={connector.title} width={22} height={22} />
                ) : connector.icon ? (
                  <Iconify icon={connector.icon} sx={{ color: connector.color, fontSize: 22 }} />
                ) : null}
              </Box>

              <Box>
                <Typography
                  variant="subtitle1"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.9375rem',
                  }}
                >
                  {connector.title}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{
                    fontSize: '0.8125rem',
                    lineHeight: 1.5,
                  }}
                >
                  {connector.description}
                </Typography>
              </Box>
            </Box>

            {/* Status badges */}
            <Box sx={{ display: 'flex', mr: 2, gap: 1 }}>
              {/* Configuration Status badge */}
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  px: 1,
                  py: 0.5,
                  borderRadius: 0.75,
                  bgcolor: alpha(
                    isConfigured ? theme.palette.warning.main : theme.palette.text.disabled,
                    theme.palette.mode === 'dark' ? 0.15 : 0.08
                  ),
                  color: isConfigured
                    ? theme.palette.warning.main
                    : theme.palette.text.disabled,
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
                <Typography
                  variant="caption"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.6875rem',
                  }}
                >
                  {checkingConfigs ? (
                    <CircularProgress size={12} />
                  ) : (
                    isConfigured ? 'Configured' : 'Not Configured'
                  )}
                </Typography>
              </Box>

              {/* Enable/Disable Status badge */}
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  px: 1,
                  py: 0.5,
                  borderRadius: 0.75,
                  bgcolor: alpha(
                    isEnabled ? connector.color : theme.palette.text.disabled,
                    theme.palette.mode === 'dark' ? 0.15 : 0.08
                  ),
                  color: isEnabled ? connector.color : theme.palette.text.disabled,
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
                <Typography
                  variant="caption"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.6875rem',
                  }}
                >
                  {isEnabled ? 'Active' : 'Inactive'}
                </Typography>
              </Box>
            </Box>

            {/* Action buttons */}
            <Box sx={{ display: 'flex', gap: 1 }}>
              <Tooltip title={`Configure ${connector.title} settings`}>
                <IconButton
                  size="small"
                  onClick={handleConfigureClick}
                  disabled={isLoading}
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
              </Tooltip>

              <Tooltip title={`${isEnabled ? 'Disable' : 'Enable'} ${connector.title} integration`}>
                <Switch
                  checked={isEnabled}
                  onChange={(e) => handleToggle(e.target.checked)}
                  disabled={!isConfigured || isLoading}
                  color="primary"
                  size="small"
                  sx={{
                    '& .MuiSwitch-switchBase.Mui-checked': {
                      color: connector.color,
                      '&:hover': {
                        backgroundColor: alpha(connector.color, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                      },
                    },
                    '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                      backgroundColor: connector.color,
                    },
                    '& .MuiSwitch-track': {
                      opacity: 0.8,
                    },
                  }}
                />
              </Tooltip>
            </Box>
          </Paper>
        </Box>

        {/* Statistics Section */}
        {showStats && (
          <Box sx={{ mb: 3 }}>
            <ConnectorStatistics
              title={`${connector.title} Statistics`}
              connectorNames={connectorNames}
              showUploadTab={false}
            />
          </Box>
        )}


        {/* Custom Children */}
        {children}

        {/* Configuration Dialog */}
        {configDialogOpen && currentConnector === connectorId && configDialogComponent && (
          <Box>
            {React.createElement(configDialogComponent, {
              open: configDialogOpen,
              onClose: () => setConfigDialogOpen(false),
              onSave: () => {
                setConfigDialogOpen(false);
                setCurrentConnector(null);
              },
              onFileRemoved: handleFileRemoved,
              connectorType: currentConnector,
              isEnabled: connectorStatus[currentConnector || ''] || false,
            })}
          </Box>
        )}
      </Paper>
    </Container>
  );
};
