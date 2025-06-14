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

import { CONNECTORS_LIST } from './components/connectors-list';
import ConfigureConnectorDialog from './components/configure-connector-individual-dialog';

interface Connector {
  key: string;
  isEnabled: boolean;
}

const NotionConfigPage = () => {
  const theme = useTheme();
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState('Connector settings updated successfully');
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [checkingConfigs, setCheckingConfigs] = useState(true);
  const [connectorStatus, setConnectorStatus] = useState<boolean>(false);
  const [configuredStatus, setConfiguredStatus] = useState<boolean>(false);
  const connector = CONNECTORS_LIST.find((current) => current.id === 'notion');
  const connectorID = 'notion';
  // Fetch connector config
  const fetchConnectorConfig = useCallback(async (connectorId: string) => {
    try {
      const response = await axios.get(`/api/v1/connectors/config`, {
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

  // Check configurations separately
  const checkConnectorConfigurations = useCallback(async () => {
    setCheckingConfigs(true);
    try {
      // Check all configurations in parallel
      const results = await Promise.allSettled([fetchConnectorConfig(connectorID)]);

      // Check if each configuration has required fields
      // Ensure property names match what's returned by the API
      const notionConfigured = results[0].status === 'fulfilled' && results[0].value;

      setConfiguredStatus(notionConfigured);
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
      const notion = data.find((connectorResult) => connectorResult.key === connectorID);
      setConnectorStatus(notion ? notion.isEnabled : false);

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
        const notionConfigured = results[0].status === 'fulfilled' && results[0].value;

        setConfiguredStatus(notionConfigured);
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
    const connectorTitle = 'Notion';
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
    <Container maxWidth="lg">
      <Paper
        sx={{
          overflow: 'hidden',
          position: 'relative',
          p: 3,
          borderRadius: 2,
          boxShadow: (themeShadow) => `0 2px 20px ${alpha(themeShadow.palette.grey[500], 0.15)}`,
          border: '1px solid',
          borderColor: 'divider',
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

        {/* Error message */}
        {errorMessage && (
          <Alert
            severity="error"
            onClose={() => setErrorMessage(null)}
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
            {errorMessage}
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
            mt: 4,
            p: 3,
            borderRadius: 2,
            bgcolor: alpha(theme.palette.info.main, 0.04),
            border: `1px solid ${alpha(theme.palette.info.main, 0.1)}`,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 2,
          }}
        >
          <Box sx={{ color: theme.palette.info.main, mt: 0.5 }}>
            <Iconify icon={infoIcon} width={20} height={20} />
          </Box>
          <Box>
            <Typography variant="subtitle2" color="text.primary" sx={{ mb: 0.5, fontWeight: 500 }}>
              Notion Configuration
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Connectors must be properly configured before they can be enabled. Click the settings
              icon to set up the necessary credentials and authentication for each service. Once
              configured, you can enable or disable the connector as needed.
            </Typography>
            <Typography variant="body2" color="primary.main" sx={{ mt: 1, fontWeight: 500 }}>
              Important: To configure Notion integration, you need to add integration secrets
              connected to thre respective notion workspaces.
              <Link
                href="https://www.notion.so/profile/integrations"
                target="_blank"
                rel="noopener"
                sx={{ fontWeight: 500 }}
              >
                Notion integrations
              </Link>
              .
            </Typography>
          </Box>
        </Box>
      </Paper>

      {/* Configure Connector Dialog */}
      <ConfigureConnectorDialog
        open={configDialogOpen}
        onClose={() => setConfigDialogOpen(false)}
        onSave={handleSaveConfiguration}
        connectorType={connectorID}
        isEnabled={connectorStatus || false}
      />

      {/* Success snackbar */}
      <Snackbar
        open={success}
        autoHideDuration={5000}
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
            boxShadow: '0px 3px 8px rgba(0, 0, 0, 0.12)',
          }}
        >
          {successMessage}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default NotionConfigPage;
