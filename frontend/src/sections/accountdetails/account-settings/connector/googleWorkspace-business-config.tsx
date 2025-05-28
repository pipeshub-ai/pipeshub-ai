import infoIcon from '@iconify-icons/eva/info-outline';
import { useState, useEffect, useCallback } from 'react';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';

import { alpha, useTheme } from '@mui/material/styles';
// MUI Components
import {
  Box,
  Grid,
  Link,
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

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import ConnectorStatistics from './connector-stats';
import { CONNECTORS_LIST } from './components/connectors-list';
import ConfigureConnectorDialog from './components/configure-connector-company-dialog';

interface Connector {
  key: string;
  isEnabled: boolean;
}

const GoogleWorkspaceBusinessPage = () => {
  const theme = useTheme();
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState('Connector settings updated successfully');
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [checkingConfigs, setCheckingConfigs] = useState(true);
  const [connectorStatus, setConnectorStatus] = useState<boolean>(false);
  const [configuredStatus, setConfiguredStatus] = useState<boolean>(false);
  const connectorNames = ['DRIVE', 'GMAIL'];
  const connector = CONNECTORS_LIST.find((current) => current.id === 'googleWorkspace');
  const connectorID = 'googleWorkspace';
  // Fetch connector config
  const fetchConnectorConfig = useCallback(async (connectorId: string) => {
    try {
      const response = await axios.get(`/api/v1/connectors/credentials`, {
        params: {
          service: connectorId,
        },
      });
      return response.data;
    } catch (err) {
      console.error(`Error fetching ${connectorId} configuration:`, err);
      setErrorMessage(`Failed to fetch ${connectorId} connector configuration. ${err.message}`);
      return null;
    }
  }, []);

  const handleFileRemoved = async (connectorId: string) => {
    // Update the configuredStatus state to show not configured
    setConfiguredStatus(false);

    // If the connector was enabled, disable it
    if (connectorStatus) {
      try {
        const response = await axios.post(`/api/v1/connectors/disable`, null, {
          params: {
            service: connectorId,
          },
        });

        setConnectorStatus(false);
        // Show success message for disabling
        setSuccessMessage(`${getConnectorTitle()} disabled successfully`);
        setSuccess(true);
      } catch (disableError) {
        console.error(`Failed to disable ${getConnectorTitle()}:`, disableError);
        setErrorMessage(`Failed to disable ${getConnectorTitle()}. Please try again.`);

        setConfiguredStatus(true);
        return;
      }
    }

    // Refresh connector statuses to get latest from server
    fetchConnectorStatuses();

    // Show success message for removal
    setSuccessMessage(`${getConnectorTitle()} configuration has been removed`);
    setSuccess(true);
  };

  // Check configurations separately
  const checkConnectorConfigurations = useCallback(async () => {
    setCheckingConfigs(true);
    try {
      // Check all configurations in parallel
      const results = await Promise.allSettled([fetchConnectorConfig(connectorID)]);

      // Check if each configuration has required fields
      // Ensure property names match what's returned by the API
      const googleConfigured = results[0].status === 'fulfilled' && results[0].value?.isConfigured;

      setConfiguredStatus(googleConfigured);
    } catch (err) {
      console.error('Error checking connector configurations:', err);
      setErrorMessage(`Failed to check connector config. ${err.message}`);
    } finally {
      setCheckingConfigs(false);
    }
  }, [fetchConnectorConfig]);

  // Fetch connectors from API
  const fetchConnectorStatuses = useCallback(async () => {
    setIsLoading(true);
    try {
      // API call to get current connectors status
      await checkConnectorConfigurations();
      const response = await axios.get('/api/v1/connectors/status');
      const data = response.data as Connector[];

      // Initialize status objects
      // Process data from API
      const googleWorkspace = data.find((connectorResult) => connectorResult.key === connectorID);
      setConnectorStatus(googleWorkspace ? googleWorkspace.isEnabled : false);

      // After setting the status, check configurations to ensure they're up to date
    } catch (err) {
      console.error('Failed to fetch connectors:', err);
      setErrorMessage(`Failed to load connector settings ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  }, [checkConnectorConfigurations]);

  useEffect(() => {
    // Fetch existing connector statuses from the backend
    fetchConnectorStatuses();
  }, [fetchConnectorStatuses]);

  // Check configurations when lastConfigured changes
  useEffect(() => {
    const checkConfigurations = async () => {
      setCheckingConfigs(true);
      try {
        // Check all configurations in parallel
        const results = await Promise.allSettled([fetchConnectorConfig(connectorID)]);
        const googleConfigured =
          results[0].status === 'fulfilled' && results[0].value?.isConfigured;

        setConfiguredStatus(googleConfigured);
      } catch (err) {
        console.error('Error checking connector configurations:', err);
        setErrorMessage(`Failed to check connector config ${err.message}`);
      } finally {
        setCheckingConfigs(false);
      }
    };

    // Call the function to check configurations
    checkConfigurations();
  }, [fetchConnectorConfig]);

  // Handle toggling connectors
  const handleToggleConnector = async (connectorId: string) => {
    // Don't allow enabling unconfigured connectors
    if (!configuredStatus && !connectorStatus) {
      setErrorMessage(`${getConnectorTitle()} needs to be configured before it can be enabled`);
      return;
    }

    const newStatus = !connectorStatus;
    setIsLoading(true);
    try {
      if (newStatus) {
        const response = await axios.post(`/api/v1/connectors/enable`, null, {
          params: {
            service: connectorId,
          },
        });
      } else {
        const response = await axios.post(`/api/v1/connectors/disable`, null, {
          params: {
            service: connectorId,
          },
        });
      }
      setSuccessMessage(
        `${getConnectorTitle()} ${newStatus ? 'enabled' : 'disabled'} successfully`
      );
      setSuccess(true);
      setConnectorStatus(newStatus);
    } catch (err) {
      console.error('Failed to update connector status:', err);
      setErrorMessage(`Failed to update connector status. Please try again. ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  // Handle opening the configure dialog
  const handleConfigureConnector = () => {
    // Track which connector is being configured
    setConfigDialogOpen(true);
  };

  // Handle save in configure dialog
  const handleSaveConfiguration = () => {
    // Display appropriate success message
    const connectorTitle = 'Google Workspace';
    setSuccessMessage(`${connectorTitle} configured successfully`);
    setConfigDialogOpen(false);
    setSuccess(true);

    setConfiguredStatus(true);

    // Refresh connector statuses to get latest from server
    fetchConnectorStatuses();
  };

  // Helper to get connector title from ID
  const getConnectorTitle = (): string => connector?.title || 'Connector';

  // Handle close for success message
  const handleCloseSuccess = () => {
    setSuccess(false);
  };

  // Determine status color and text
  const getStatusColor = () => {
    if (connectorStatus) return connector?.color;
    if (configuredStatus) return theme.palette.warning.main;
    return theme.palette.text.disabled;
  };

  const getStatusText = () => {
    if (connectorStatus) return 'Active';
    if (configuredStatus) return 'Configured';
    return 'Not Configured';
  };

  const isEnabled = connectorStatus || false;
  const isConfigured = configuredStatus || false;
  const isDisabled = !isConfigured && !isEnabled;

  const getTooltipMessage = () => {
    if (!connectorStatus && !configuredStatus) {
      return `${connector?.title} needs to be configured before it can be enabled`;
    }
    return '';
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
          borderColor: theme.palette.divider,
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

        {/* Grid for connectors */}
        <Grid container spacing={3}>
          {connector && (
            <Grid item xs={12} key={connector.id}>
              <Paper
                sx={{
                  p: 2.5,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  borderRadius: 2,
                  border: '1px solid',
                  borderColor: isEnabled ? alpha(connector.color, 0.3) : 'divider',
                  bgcolor: isEnabled ? alpha(connector.color, 0.03) : 'background.paper',
                  transition: 'all 0.2s ease-in-out',
                  '&:hover': {
                    transform: 'translateY(-2px)',
                    boxShadow: 2,
                    borderColor: alpha(connector.color, 0.5),
                  },
                }}
              >
                {/* Connector info */}
                <Box sx={{ display: 'flex', alignItems: 'center', flexGrow: 1 }}>
                  <Box
                    sx={{
                      width: 48,
                      height: 48,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mr: 2,
                      bgcolor: alpha(connector.color, 0.1),
                      color: connector.color,
                      borderRadius: 1.5,
                    }}
                  >
                    <Iconify icon={connector.icon} width={26} height={26} />
                  </Box>

                  <Box>
                    <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                      {connector.title}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {connector.description}
                    </Typography>
                  </Box>
                </Box>

                {/* Status badge */}
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    mr: 2,
                    px: 1,
                    py: 0.5,
                    borderRadius: 1,
                    bgcolor: alpha(getStatusColor() || '#808080', 0.08),
                    color: getStatusColor(),
                  }}
                >
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      bgcolor: 'currentColor',
                      mr: 0.5,
                    }}
                  />
                  <Typography
                    variant="caption"
                    sx={{
                      fontWeight: 600,
                    }}
                  >
                    {getStatusText()}
                  </Typography>
                </Box>

                <IconButton
                  size="small"
                  onClick={() => handleConfigureConnector()}
                  sx={{
                    mr: 1,
                    color: theme.palette.text.secondary,
                    '&:hover': {
                      bgcolor: isEnabled ? 'transparent' : alpha(theme.palette.primary.main, 0.08),
                      color: isEnabled ? theme.palette.text.disabled : theme.palette.primary.main,
                    },
                  }}
                  aria-label={`Configure ${connector.title}`}
                >
                  <Iconify icon={settingsIcon} width={20} height={20} />
                </IconButton>

                <Tooltip
                  title={getTooltipMessage()}
                  placement="top"
                  arrow
                  disableHoverListener={!isDisabled}
                >
                  <div>
                    {' '}
                    {/* Wrapper div needed for disabled elements */}
                    <Switch
                      checked={isEnabled}
                      onChange={() => handleToggleConnector(connector.id)}
                      disabled={isDisabled}
                      color="primary"
                      sx={{
                        '& .MuiSwitch-switchBase.Mui-checked': {
                          color: connector.color,
                          '&:hover': {
                            backgroundColor: alpha(connector.color, 0.1),
                          },
                        },
                        '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                          backgroundColor: connector.color,
                        },
                      }}
                    />
                  </div>
                </Tooltip>
              </Paper>
            </Grid>
          )}
        </Grid>

        {/* Info box */}
        <Box
          sx={{
            mt: 3,
            p: 2.5,
            borderRadius: 1,
            bgcolor:
              theme.palette.mode === 'dark'
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
                fontSize: '0.875rem',
              }}
            >
              Google Workspace Configuration
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                fontSize: '0.8125rem',
                lineHeight: 1.5,
                mb: 1,
              }}
            >
              Connectors must be properly configured before they can be enabled. Click the settings
              icon to set up the necessary credentials and authentication for each service. Once
              configured, you can enable or disable the connector as needed.
            </Typography>
            <Typography variant="body2" color="primary.main" sx={{ mt: 1, fontWeight: 500 }}>
              Important: To configure Google Workspace integration, you need to upload your Service
              credentials JSON file from the{' '}
              <Link
                href="https://console.cloud.google.com/iam-admin/serviceaccounts/"
                target="_blank"
                rel="noopener"
                sx={{ fontWeight: 500 }}
              >
                Google Cloud Console
              </Link>
              .
            </Typography>
          </Box>
        </Box>
        <ConnectorStatistics connectorNames={connectorNames} />
      </Paper>

      {/* Configure Connector Dialog */}
      <ConfigureConnectorDialog
        open={configDialogOpen}
        onClose={() => setConfigDialogOpen(false)}
        onSave={handleSaveConfiguration}
        onFileRemoved={handleFileRemoved}
        connectorType={connectorID}
        isEnabled={connectorStatus || false}
      />

      {/* Success snackbar */}
      <Snackbar
        open={success}
        autoHideDuration={4000}
        onClose={handleCloseSuccess}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseSuccess}
          severity="success"
          variant="filled"
          sx={{
            width: '100%',
            boxShadow:
              theme.palette.mode === 'dark'
                ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
                : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {successMessage}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default GoogleWorkspaceBusinessPage;
