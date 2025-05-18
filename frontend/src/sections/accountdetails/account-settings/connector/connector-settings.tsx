import { useNavigate } from 'react-router-dom';
import infoIcon from '@iconify-icons/eva/info-outline';
import { useState, useEffect, useCallback } from 'react';

import { alpha, useTheme } from '@mui/material/styles';
// MUI Components
import {
  Box,
  Grid,
  Link,
  Alert,
  Paper,
  Container,
  Typography,
  AlertTitle,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import { useAuthContext } from 'src/auth/hooks';

import { CONNECTORS_LIST } from './components/connectors-list';

// Define connector types and interfaces
interface ConnectorStatusMap {
  [connectorId: string]: boolean;
}

interface ConnectorEnabledMap {
  [connectorId: string]: boolean;
}

export interface ConfigStatus {
  googleWorkspace: boolean;
}

const ConnectorSettings = () => {
  const theme = useTheme();
  const { user } = useAuthContext();
  const accountType = user?.accountType || 'individual';
  const navigate = useNavigate();
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [currentConnector, setCurrentConnector] = useState<string | null>(null);

  const [checkingConfigs, setCheckingConfigs] = useState(true);
  const [configuredStatus, setConfiguredStatus] = useState<ConnectorStatusMap>({});
  const [enabledStatus, setEnabledStatus] = useState<ConnectorEnabledMap>({});

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
      return null;
    }
  }, []);

  // Fetch connector status (enabled/disabled)
  const fetchConnectorStatus = useCallback(async (connectorId: string) => {
    try {
      const response = await axios.get(`/api/v1/connectors/status`, {
        params: {
          service: connectorId,
        },
      });
      return response.data?.enabled || false;
    } catch (err) {
      console.error(`Error fetching ${connectorId} status:`, err);
      return false;
    }
  }, []);

  // Check configurations separately
  const checkConnectorConfigurations = useCallback(async () => {
    setCheckingConfigs(true);
    try {
      // Check all configurations in parallel
      const results = await Promise.allSettled([fetchConnectorConfig('googleWorkspace')]);

      // Check if the configuration is valid
      const googleConfigured = results[0].status === 'fulfilled' && results[0].value;

      const newConfigStatus = {
        googleWorkspace: googleConfigured,
      };

      setConfiguredStatus(newConfigStatus);
    } catch (err) {
      console.error('Error checking connector configurations:', err);
    } finally {
      setCheckingConfigs(false);
    }
  }, [fetchConnectorConfig]);

  // Fetch connectors from API
  const fetchConnectorStatuses = useCallback(async () => {
    setIsLoading(true);
    try {
      // API call to get current connectors status
      const response = await axios.get('/api/v1/connectors/status');
      const { data } = response;

      // Initialize status objects
      const enabledMap: ConnectorEnabledMap = {};

      // Process data from API
      if (data) {
        data.forEach((connector: any) => {
          enabledMap[connector.key] = Boolean(connector.isEnabled);
        });
      }

      setEnabledStatus(enabledMap);

      // After setting the status, check configurations
      await checkConnectorConfigurations();
    } catch (err) {
      console.error('Failed to fetch connectors:', err);
      setErrorMessage(`Failed to load connector settings ${err.message}`);
    } finally {
      setIsLoading(false);
    }
  }, [checkConnectorConfigurations]);

  // Initialize and fetch data when component mounts
  useEffect(() => {
    // Initialize connector statuses
    const initialStatus: ConnectorStatusMap = {};
    CONNECTORS_LIST.forEach((connector) => {
      initialStatus[connector.id] = false;
    });
    setConfiguredStatus(initialStatus);
    setEnabledStatus(initialStatus);

    // Fetch existing connector statuses and configurations
    fetchConnectorStatuses();
  }, [fetchConnectorStatuses]);

  const handleConfigureConnector = (connectorId: string) => {
    setCurrentConnector(connectorId);
    const currentPath = window.location.pathname;
    const basePath = currentPath.endsWith('/') ? currentPath : `${currentPath}/`;
    navigate(`${basePath}${connectorId}`);
  };

  // Helper to get connector title from ID
  const getConnectorTitle = (connectorId: string): string => {
    const connector = CONNECTORS_LIST.find((c) => c.id === connectorId);
    return connector?.title || 'Connector';
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
              Connectors
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: 500,
                lineHeight: 1.5,
              }}
            >
              Connect and manage integrations with external services and platforms
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

        {/* Connectors Grid */}
        <Grid container spacing={2}>
          {CONNECTORS_LIST.map((connector) => {
            const isEnabled = enabledStatus[connector.id] || false;
            const isConfigured = configuredStatus[connector.id] || false;

            return (
              <Grid item xs={12} key={connector.id}>
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
                      <Iconify icon={connector.icon} width={22} height={22} />
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
                        {isConfigured ? 'Configured' : 'Not Configured'}
                      </Typography>
                    </Box>

                    {/* Enabled Status badge - only show if configured */}
                    {isConfigured && (
                      <Box
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          px: 1,
                          py: 0.5,
                          borderRadius: 0.75,
                          bgcolor: alpha(
                            isEnabled ? connector.color : theme.palette.error.main,
                            theme.palette.mode === 'dark' ? 0.15 : 0.08
                          ),
                          color: isEnabled ? connector.color : theme.palette.error.main,
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
                    )}
                  </Box>

                  {/* Configure Button */}
                  <Box
                    onClick={() => handleConfigureConnector(connector.id)}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      px: 1,
                      py: 0.5,
                      borderRadius: 0.75,
                      cursor: 'pointer',
                      bgcolor: alpha(
                        theme.palette.primary.main,
                        theme.palette.mode === 'dark' ? 0.15 : 0.08
                      ),
                      color: theme.palette.primary.main,
                      transition: 'all 0.2s',
                      '&:hover': {
                        bgcolor: alpha(
                          theme.palette.primary.main,
                          theme.palette.mode === 'dark' ? 0.25 : 0.15
                        ),
                      },
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
                      View Details
                    </Typography>
                  </Box>
                </Paper>
              </Grid>
            );
          })}
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
              Connector Configuration
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                fontSize: '0.8125rem',
                lineHeight: 1.5,
              }}
            >
              Connectors must be properly configured before they can be enabled. Click &quot;View
              Details&quot; to set up the necessary credentials and authentication for each service.
              Configured connectors will display their status as Active when enabled.
            </Typography>
          </Box>
        </Box>
      </Paper>

      <Alert
        variant="outlined"
        severity="info"
        sx={{
          mt: 3,
          mb: 1,
          borderRadius: 1,
          borderColor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.3 : 0.2),
          '& .MuiAlert-icon': {
            color: theme.palette.info.main,
          },
        }}
      >
        <Typography variant="body2">
          Refer to{' '}
          {accountType === 'business' ? (
            <Link
              href="https://docs.pipeshub.com/enterprise/connectors/overview"
              target="_blank"
              rel="noopener"
              sx={{
                color: theme.palette.primary.main,
                textDecoration: 'none',
                fontWeight: 500,
                '&:hover': {
                  textDecoration: 'underline',
                },
              }}
            >
              the documentation
            </Link>
          ) : (
            <Link
              href="https://docs.pipeshub.com/individual/connectors/overview"
              target="_blank"
              rel="noopener"
              sx={{
                color: theme.palette.primary.main,
                textDecoration: 'none',
                fontWeight: 500,
                '&:hover': {
                  textDecoration: 'underline',
                },
              }}
            >
              the documentation
            </Link>
          )}{' '}
          for more information.
        </Typography>
      </Alert>
    </Container>
  );
};

export default ConnectorSettings;
