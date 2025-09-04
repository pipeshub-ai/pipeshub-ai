import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Container,
  Paper,
  Box,
  Typography,
  Switch,
  Tooltip,
  Alert,
  AlertTitle,
  IconButton,
  CircularProgress,
  Snackbar,
  alpha,
  useTheme,
  Stack,
  Chip,
  Button,
  Grid,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';
import infoIcon from '@iconify-icons/eva/info-outline';
import refreshIcon from '@iconify-icons/mdi/refresh';
import arrowBackIcon from '@iconify-icons/mdi/arrow-left';
import { Connector, ConnectorConfig } from '../types/types';
import { ConnectorApiService } from '../services/api';
import ConnectorConfigForm from './connector-config-form';
import ConnectorStatistics from '../../account-settings/connector/connector-stats';
import { CONNECTOR_IMAGES } from '../utils/images';

interface ConnectorManagerProps {
  showStats?: boolean;
}

const ConnectorManager: React.FC<ConnectorManagerProps> = ({ showStats = true }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { connectorName } = useParams<{ connectorName: string }>();
  const navigate = useNavigate();

  // State
  const [connector, setConnector] = useState<Connector | null>(null);
  const [connectorConfig, setConnectorConfig] = useState<ConnectorConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');

  // Fetch connector data
  const fetchConnectorData = useCallback(async () => {
    if (!connectorName) return;

    try {
      setLoading(true);
      setError(null);

      // Fetch connector info
      const connectors = await ConnectorApiService.getConnectors();
      const foundConnector = connectors.find(
        (c) => c.name.toLowerCase() === connectorName.toLowerCase()
      );

      if (!foundConnector) {
        setError(`Connector "${connectorName}" not found`);
        return;
      }

      setConnector(foundConnector);

      // Fetch connector configuration
      try {
        const config = await ConnectorApiService.getConnectorConfig(connectorName.toUpperCase());
        setConnectorConfig(config);
      } catch (configError) {
        console.warn('Could not fetch connector config:', configError);
        // Continue without config - connector might not be configured yet
      }
    } catch (err) {
      console.error('Error fetching connector data:', err);
      setError('Failed to load connector information');
    } finally {
      setLoading(false);
    }
  }, [connectorName]);

  // Handle connector toggle (enable/disable)
  const handleToggleConnector = useCallback(
    async (enabled: boolean) => {
      if (!connector) return;

      const successResponse = await ConnectorApiService.toggleConnector(connector.name);

      if (successResponse) {
        // Update local state
        setConnector((prev) => (prev ? { ...prev, isActive: enabled } : null));

        const action = enabled ? 'enabled' : 'disabled';
        setSuccessMessage(`${connector.name} ${action} successfully`);
        setSuccess(true);

        // Clear success message after 4 seconds
        setTimeout(() => setSuccess(false), 4000);
      } else {
        setError(`Failed to ${enabled ? 'enable' : 'disable'} connector`);
      }
    },
    [connector]
  );

  // Handle configuration dialog
  const handleConfigureClick = useCallback(() => {
    setConfigDialogOpen(true);
  }, []);

  const handleConfigClose = useCallback(() => {
    setConfigDialogOpen(false);
  }, []);

  const handleConfigSuccess = useCallback(() => {
    setConfigDialogOpen(false);
    setSuccessMessage(`${connector?.name} configured successfully`);
    setSuccess(true);

    // Refresh connector data
    fetchConnectorData();

    // Clear success message after 4 seconds
    setTimeout(() => setSuccess(false), 4000);
  }, [connector, fetchConnectorData]);

  // Handle refresh
  const handleRefresh = useCallback(() => {
    fetchConnectorData();
  }, [fetchConnectorData]);

  // Initialize
  useEffect(() => {
    fetchConnectorData();
  }, [fetchConnectorData]);

  // Loading state
  if (loading) {
    return (
      <Container maxWidth="lg" sx={{ py: 3 }}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: 400,
          }}
        >
          <CircularProgress />
        </Box>
      </Container>
    );
  }

  // Error state
  if (error || !connector) {
    return (
      <Container maxWidth="lg" sx={{ py: 3 }}>
        <Alert severity="error">
          <AlertTitle>Error</AlertTitle>
          {error || 'Connector not found'}
        </Alert>
      </Container>
    );
  }

  const isConfigured = connector.isConfigured || false;
  const isActive = connector.isActive || false;

  // Get connector color for theming
  const getConnectorColor = (name: string): string => {
    const colorMap: Record<string, string> = {
      slack: '#4A154B',
      drive: '#4285F4',
      gmail: '#DB4437',
      onedrive: '#0078D4',
      sharepoint: '#036C70',
      teams: '#6264A7',
      jira: '#0052CC',
      confluence: '#172B4D',
      dropbox: '#0061FF',
      box: '#0061D5',
    };
    return colorMap[name.toLowerCase()] || theme.palette.primary.main;
  };

  const connectorColor = getConnectorColor(connector.name);
  const isDisabled = !isConfigured && !isActive;

  const getTooltipMessage = () => {
    if (isDisabled) {
      return `${connector.name} needs to be configured before it can be enabled`;
    }
    return '';
  };

  const getStatusColor = () => {
    if (isActive) return connectorColor;
    if (isConfigured) return theme.palette.warning.main;
    return theme.palette.text.disabled;
  };

  const getStatusText = () => {
    if (isActive) return 'Active';
    if (isConfigured) return 'Configured';
    return 'Not Configured';
  };

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* Loading overlay */}
        {loading && (
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

        {/* Compact Header */}
        <Box
          sx={{
            p: 2,
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: theme.palette.background.paper,
          }}
        >
          <Stack spacing={2}>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <IconButton
                onClick={() => navigate('/account/company-settings/settings/connector')}
                sx={{
                  color: theme.palette.text.secondary,
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                  },
                }}
              >
                <Iconify icon={arrowBackIcon} width={20} height={20} />
              </IconButton>

              <Box
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: 1.5,
                  backgroundColor: alpha(connectorColor, 0.1),
                  border: `1px solid ${alpha(connectorColor, 0.2)}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <img
                  src={
                    CONNECTOR_IMAGES[connector.name.toUpperCase() as keyof typeof CONNECTOR_IMAGES]
                  }
                  alt={connector.name}
                  width={20}
                  height={20}
                />
              </Box>
              <Box>
                <Typography
                  variant="h5"
                  sx={{
                    fontWeight: 700,
                    fontSize: '1.5rem',
                    color: theme.palette.text.primary,
                    mb: 0.5,
                  }}
                >
                  Connector Management
                </Typography>
                <Typography
                  variant="body2"
                  sx={{
                    color: theme.palette.text.secondary,
                    fontSize: '0.875rem',
                  }}
                >
                  Manage your {connector.appGroup} integrations
                  {isActive && (
                    <Chip
                      label="Active"
                      size="small"
                      sx={{
                        ml: 1,
                        height: 20,
                        fontSize: '0.6875rem',
                        fontWeight: 600,
                        backgroundColor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.success.main, 0.8)
                            : alpha(theme.palette.success.main, 0.1),
                        color:
                          theme.palette.mode === 'dark'
                            ? theme.palette.success.contrastText
                            : theme.palette.success.main,
                        border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                      }}
                    />
                  )}
                </Typography>
              </Box>

              <Box sx={{ flex: 1 }} />

              <Button
                variant="outlined"
                startIcon={<Iconify icon={refreshIcon} width={16} height={16} />}
                onClick={handleRefresh}
                disabled={loading}
                sx={{ textTransform: 'none' }}
              >
                Refresh
              </Button>
            </Stack>
          </Stack>
        </Box>

        {/* Content */}
        <Box sx={{ p: 2 }}>
          {/* Error message */}
          {error && (
            <Alert
              severity="error"
              onClose={() => setError(null)}
              sx={{
                mb: 2,
                borderRadius: 1,
                border: 'none',
                '& .MuiAlert-icon': {
                  color: theme.palette.error.main,
                },
              }}
            >
              <AlertTitle sx={{ fontWeight: 500, fontSize: '0.875rem' }}>Error</AlertTitle>
              <Typography variant="body2">{error}</Typography>
            </Alert>
          )}

          <Stack spacing={2}>
            {/* Main Content Grid */}
            <Grid container spacing={2}>
              {/* Main Connector Card */}
              <Grid item xs={12} md={8}>
                <Paper
                  elevation={0}
                  sx={{
                    p: 2.5,
                    borderRadius: 1.5,
                    border: '1px solid',
                    borderColor: isActive ? alpha(connectorColor, 0.3) : theme.palette.divider,
                    bgcolor: isActive ? alpha(connectorColor, 0.02) : 'transparent',
                    transition: theme.transitions.create(['border-color', 'box-shadow']),
                    position: 'relative',
                    '&:hover': {
                      borderColor: alpha(connectorColor, 0.4),
                      boxShadow: `0 4px 16px ${alpha(connectorColor, 0.08)}`,
                    },
                  }}
                >
                  {/* Status Dot */}
                  {isActive && (
                    <Box
                      sx={{
                        position: 'absolute',
                        top: 12,
                        right: 12,
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        backgroundColor: theme.palette.success.main,
                        boxShadow: `0 0 0 2px ${theme.palette.background.paper}`,
                      }}
                    />
                  )}

                  {/* Connector Info */}
                  <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
                    <Box
                      sx={{
                        width: 48,
                        height: 48,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: alpha(connectorColor, 0.1),
                        border: `1px solid ${alpha(connectorColor, 0.2)}`,
                        borderRadius: 1.5,
                      }}
                    >
                      <img
                        src={
                          CONNECTOR_IMAGES[
                            connector.name.toUpperCase() as keyof typeof CONNECTOR_IMAGES
                          ]
                        }
                        alt={connector.name}
                        width={24}
                        height={24}
                        style={{ objectFit: 'contain' }}
                        onError={(e) => {
                          const target = e.target as HTMLImageElement;
                          target.src = '/assets/icons/connectors/default.svg';
                        }}
                      />
                    </Box>

                    <Box sx={{ flex: 1 }}>
                      <Typography
                        variant="h6"
                        sx={{
                          fontWeight: 600,
                          color: theme.palette.text.primary,
                          mb: 0.25,
                        }}
                      >
                        {connector.name}
                      </Typography>

                      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
                        <Typography
                          variant="body2"
                          sx={{
                            color: theme.palette.text.secondary,
                            fontSize: '0.8125rem',
                          }}
                        >
                          {connector.appGroup}
                        </Typography>

                        <Box
                          sx={{
                            width: 3,
                            height: 3,
                            borderRadius: '50%',
                            bgcolor: theme.palette.text.disabled,
                          }}
                        />

                        <Chip
                          label={connector.authType.split('_').join(' ')}
                          size="small"
                          sx={{
                            height: 20,
                            fontSize: '0.6875rem',
                            fontWeight: 500,
                            backgroundColor: isDark
                              ? alpha(theme.palette.grey[500], 0.9)
                              : alpha(theme.palette.grey[500], 0.1),
                            color: theme.palette.text.secondary,
                            border: `1px solid ${alpha(theme.palette.grey[500], 0.2)}`,
                          }}
                        />

                        {connector.supportsRealtime && (
                          <>
                            <Box
                              sx={{
                                width: 3,
                                height: 3,
                                borderRadius: '50%',
                                bgcolor: theme.palette.text.disabled,
                              }}
                            />
                            <Chip
                              label="Real-time"
                              size="small"
                              sx={{
                                height: 20,
                                fontSize: '0.6875rem',
                                fontWeight: 500,
                                backgroundColor: isDark
                                  ? alpha(theme.palette.info.main, 0.9)
                                  : alpha(theme.palette.info.main, 0.1),
                                color: theme.palette.info.main,
                                border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                              }}
                            />
                          </>
                        )}
                      </Stack>
                    </Box>
                  </Stack>

                  {/* Status Control */}
                  <Box
                    sx={{
                      p: 2,
                      borderRadius: 1,
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? isDark
                            ? alpha(theme.palette.background.default, 0.3)
                            : alpha(theme.palette.background.default, 0.3)
                          : alpha(theme.palette.grey[50], 0.5),
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    <Stack direction="row" alignItems="center" justifyContent="space-between">
                      <Box>
                        <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.5 }}>
                          Connector Status
                        </Typography>
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ fontSize: '0.8125rem' }}
                        >
                          {isActive
                            ? 'Active and syncing data'
                            : isConfigured
                              ? 'Configured but inactive'
                              : 'Needs configuration'}
                        </Typography>
                      </Box>

                      <Tooltip
                        title={getTooltipMessage()}
                        placement="top"
                        arrow
                        disableHoverListener={!isDisabled}
                      >
                        <div>
                          <Switch
                            checked={isActive}
                            onChange={(e) => handleToggleConnector(e.target.checked)}
                            disabled={isDisabled}
                            color="primary"
                            size="medium"
                            sx={{
                              '& .MuiSwitch-switchBase.Mui-checked': {
                                color: connectorColor,
                                '&:hover': {
                                  backgroundColor: isDark
                                    ? alpha(connectorColor, 0.9)
                                    : alpha(connectorColor, 0.1),
                                },
                              },
                              '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                                backgroundColor: isDark ? connectorColor : connectorColor,
                              },
                            }}
                          />
                        </div>
                      </Tooltip>
                    </Stack>
                  </Box>
                </Paper>
              </Grid>

              {/* Actions Sidebar */}
              <Grid item xs={12} md={4}>
                <Stack spacing={1.5}>
                  {/* Quick Actions */}
                  <Paper
                    elevation={0}
                    sx={{
                      p: 2,
                      borderRadius: 1.5,
                      border: '1px solid',
                      borderColor: theme.palette.divider,
                      bgcolor: theme.palette.background.paper,
                    }}
                  >
                    <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1.5 }}>
                      Quick Actions
                    </Typography>

                    <Stack spacing={1}>
                      <Button
                        variant={!isConfigured ? 'contained' : 'outlined'}
                        fullWidth
                        size="small"
                        startIcon={<Iconify icon={settingsIcon} width={14} height={14} />}
                        onClick={handleConfigureClick}
                        sx={{
                          textTransform: 'none',
                          fontWeight: 500,
                          justifyContent: 'flex-start',
                          borderRadius: 1,
                          ...(!isConfigured && {
                            backgroundColor: connectorColor,
                            '&:hover': {
                              backgroundColor: isDark
                                ? alpha(connectorColor, 0.8)
                                : alpha(connectorColor, 0.8),
                            },
                          }),
                        }}
                      >
                        {!isConfigured ? 'Configure Now' : 'Configure Settings'}
                      </Button>

                      <Button
                        variant="outlined"
                        fullWidth
                        size="small"
                        startIcon={<Iconify icon={refreshIcon} width={14} height={14} />}
                        onClick={handleRefresh}
                        disabled={loading}
                        sx={{
                          textTransform: 'none',
                          fontWeight: 500,
                          justifyContent: 'flex-start',
                          borderRadius: 1,
                        }}
                      >
                        {loading ? 'Refreshing...' : 'Refresh Status'}
                      </Button>

                      {isConfigured && (
                        <Button
                          variant="outlined"
                          fullWidth
                          size="small"
                          startIcon={
                            <Iconify
                              icon={isActive ? 'solar:pause-circle-bold' : 'solar:play-circle-bold'}
                              width={14}
                              height={14}
                            />
                          }
                          onClick={() => handleToggleConnector(!isActive)}
                          disabled={isDisabled}
                          sx={{
                            textTransform: 'none',
                            fontWeight: 500,
                            justifyContent: 'flex-start',
                            borderRadius: 1,
                            color: isActive
                              ? theme.palette.warning.main
                              : theme.palette.success.main,
                            borderColor: isActive
                              ? theme.palette.warning.main
                              : theme.palette.success.main,
                            '&:hover': {
                              backgroundColor: isActive
                                ? isDark
                                  ? alpha(theme.palette.warning.main, 0.08)
                                  : alpha(theme.palette.warning.main, 0.08)
                                : alpha(theme.palette.success.main, 0.08),
                            },
                          }}
                        >
                          {isActive ? 'Disable' : 'Enable'}
                        </Button>
                      )}
                    </Stack>
                  </Paper>

                  {/* Connection Status */}
                  <Paper
                    elevation={0}
                    sx={{
                      p: 2,
                      borderRadius: 1.5,
                      border: '1px solid',
                      borderColor: theme.palette.divider,
                      bgcolor: theme.palette.background.paper,
                    }}
                  >
                    <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1.5 }}>
                      Connection Status
                    </Typography>

                    <Stack spacing={1.5}>
                      <Stack direction="row" alignItems="center" justifyContent="space-between">
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Box
                            sx={{
                              width: 6,
                              height: 6,
                              borderRadius: '50%',
                              bgcolor: isConfigured
                                ? theme.palette.warning.main
                                : theme.palette.text.disabled,
                            }}
                          />
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ fontSize: '0.8125rem' }}
                          >
                            Configuration
                          </Typography>
                        </Stack>
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 500,
                            fontSize: '0.8125rem',
                            color: isConfigured
                              ? isDark
                                ? theme.palette.warning.main
                                : theme.palette.warning.main
                              : theme.palette.text.disabled,
                          }}
                        >
                          {isConfigured ? 'Complete' : 'Required'}
                        </Typography>
                      </Stack>

                      <Stack direction="row" alignItems="center" justifyContent="space-between">
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Box
                            sx={{
                              width: 6,
                              height: 6,
                              borderRadius: '50%',
                              bgcolor: isActive
                                ? theme.palette.success.main
                                : theme.palette.text.disabled,
                            }}
                          />
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{ fontSize: '0.8125rem' }}
                          >
                            Connection
                          </Typography>
                        </Stack>
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 500,
                            fontSize: '0.8125rem',
                            color: isActive
                              ? isDark
                                ? theme.palette.success.main
                                : theme.palette.success.main
                              : theme.palette.text.disabled,
                          }}
                        >
                          {isActive ? 'Active' : 'Inactive'}
                        </Typography>
                      </Stack>
                    </Stack>
                  </Paper>
                </Stack>
              </Grid>
            </Grid>

            {/* Compact Info Alert */}
            <Alert
              variant="outlined"
              severity="info"
              icon={<Iconify icon={infoIcon} width={16} height={16} />}
              sx={{
                borderRadius: 1,
                borderColor: isDark
                  ? alpha(theme.palette.info.main, 0.2)
                  : alpha(theme.palette.info.main, 0.2),
                backgroundColor: alpha(theme.palette.info.main, 0.04),
                '& .MuiAlert-message': {
                  width: '100%',
                },
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.8125rem' }}>
                {!isConfigured
                  ? `Configure this connector to set up authentication and sync preferences.`
                  : isActive
                    ? `This connector is active and syncing data. Use the toggle to disable it.`
                    : `This connector is configured but inactive. Use the toggle to enable it.`}
              </Typography>
            </Alert>

            {/* Statistics Section */}
            {showStats && (
              <Box>
                <ConnectorStatistics
                  title="Performance Statistics"
                  connectorNames={[connector.name.toUpperCase()]}
                  showUploadTab={false}
                />
              </Box>
            )}
          </Stack>
        </Box>

        {/* Configuration Dialog */}
        {configDialogOpen && (
          <ConnectorConfigForm
            connector={connector}
            onClose={handleConfigClose}
            onSuccess={handleConfigSuccess}
          />
        )}

        {/* Success Snackbar */}
        <Snackbar
          open={success}
          autoHideDuration={4000}
          onClose={() => setSuccess(false)}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          sx={{ mt: 8 }}
        >
          <Alert
            onClose={() => setSuccess(false)}
            severity="success"
            variant="filled"
            sx={{
              borderRadius: 1.5,
              fontWeight: 600,
            }}
          >
            {successMessage}
          </Alert>
        </Snackbar>
      </Box>
    </Container>
  );
};

export default ConnectorManager;
