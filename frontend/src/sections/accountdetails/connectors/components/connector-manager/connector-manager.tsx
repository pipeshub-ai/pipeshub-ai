import React from 'react';
import {
  Container,
  Box,
  Alert,
  AlertTitle,
  Typography,
  Snackbar,
  alpha,
  useTheme,
  Stack,
  Grid,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import infoIcon from '@iconify-icons/eva/info-outline';
import { useAccountType } from 'src/hooks/use-account-type';
import ConnectorStatistics from '../connector-stats';
import ConnectorConfigForm from '../connector-config/connector-config-form';
import FilterSelectionDialog from '../filter-selection-dialog';
import { useConnectorManager } from '../../hooks/use-connector-manager';
import ConnectorHeader from './connector-header';
import ConnectorStatusCard from './connector-status-card';
import ConnectorActionsSidebar from './connector-actions-sidebar';
import ConnectorLoadingSkeleton from './connector-loading-skeleton';

interface ConnectorManagerProps {
  showStats?: boolean;
}

const ConnectorManager: React.FC<ConnectorManagerProps> = ({ 
  showStats = true 
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const {
    // State
    connector,
    connectorConfig,
    loading,
    error,
    success,
    successMessage,
    isAuthenticated,
    filterOptions,
    showFilterDialog,
    isEnablingWithFilters,
    configDialogOpen,

    // Actions
    handleToggleConnector,
    handleAuthenticate,
    handleConfigureClick,
    handleConfigClose,
    handleConfigSuccess,
    handleRefresh,
    handleFilterSelection,
    handleFilterDialogClose,
    setError,
    setSuccess,
  } = useConnectorManager();

  const { isBusiness } = useAccountType();

  // Loading state with skeleton
  if (loading) {
    return <ConnectorLoadingSkeleton showStats={showStats} />;
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
  const authType = (connector.authType || '').toUpperCase();
  const isOauth = authType === 'OAUTH';
  const canEnable = isActive ? true : (isOauth ? isAuthenticated : isConfigured);

  // Determine whether to show Authenticate button
  const isGoogleWorkspace = connector.appGroup === 'Google Workspace';
  const hideAuthenticate =
    authType === 'OAUTH_ADMIN_CONSENT' ||
    (isOauth && isBusiness && isGoogleWorkspace);

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
        {/* Header */}
        <ConnectorHeader
          connector={connector}
          loading={loading}
          onRefresh={handleRefresh}
        />

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
                <ConnectorStatusCard
                  connector={connector}
                  isAuthenticated={isAuthenticated}
                  isEnablingWithFilters={isEnablingWithFilters}
                  onToggle={handleToggleConnector}
                  hideAuthenticate={hideAuthenticate}
                />
              </Grid>

              {/* Actions Sidebar */}
              <Grid item xs={12} md={4}>
                <ConnectorActionsSidebar
                  connector={connector}
                  isAuthenticated={isAuthenticated}
                  loading={loading}
                  onAuthenticate={handleAuthenticate}
                  onConfigure={handleConfigureClick}
                  onRefresh={handleRefresh}
                  onToggle={handleToggleConnector}
                  hideAuthenticate={hideAuthenticate}
                />
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
                  connectorNames={[connector.name]}
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

        {/* Filter Selection Dialog */}
        {showFilterDialog && filterOptions && (
          <FilterSelectionDialog
            connector={connector}
            filterOptions={filterOptions}
            onClose={handleFilterDialogClose}
            onSave={handleFilterSelection}
            isEnabling={isEnablingWithFilters}
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
