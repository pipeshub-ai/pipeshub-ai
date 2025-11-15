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
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Button as MuiButton,
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

const ConnectorManager: React.FC<ConnectorManagerProps> = ({ showStats = true }) => {
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
    handleDeleteInstance,
    handleRenameInstance,
    handleFilterSelection,
    handleFilterDialogClose,
    setError,
    setSuccess,
  } = useConnectorManager();

  const { isBusiness } = useAccountType();
  const [renameOpen, setRenameOpen] = React.useState(false);
  const [renameValue, setRenameValue] = React.useState('');
  const [deleteOpen, setDeleteOpen] = React.useState(false);

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
          {error}
        </Alert>
      </Container>
    );
  }

  const isConfigured = connector.isConfigured || false;
  const isActive = connector.isActive || false;
  const authType = (connector.authType || '').toUpperCase();
  const isOauth = authType === 'OAUTH';
  const canEnable = isActive ? true : isOauth ? isAuthenticated : isConfigured;
  const supportsSync = connector.supportsSync || false;

  // Determine whether to show Authenticate button
  const isGoogleWorkspace = connector.appGroup === 'Google Workspace';
  const hideAuthenticate =
    authType === 'OAUTH_ADMIN_CONSENT' || (isOauth && isBusiness && isGoogleWorkspace && connector.scope === 'team');

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
        <ConnectorHeader connector={connector} loading={loading} onRefresh={handleRefresh} />

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
                  onDelete={() => setDeleteOpen(true)}
                  onRename={() => {
                    setRenameValue(connector.name);
                    setRenameOpen(true);
                  }}
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
                {!supportsSync
                  ? !isConfigured
                    ? `Configure this connector for agent use. Sync is not supported.`
                    : `This connector can be configured for agent use. Sync is not supported.`
                  : !isConfigured
                    ? `Configure this connector to set up authentication and sync preferences.`
                    : isActive
                      ? `This connector is active and syncing data. Use the toggle to disable it.`
                      : `This connector is configured but inactive. Use the toggle to enable it.`}
              </Typography>
            </Alert>

            {/* Statistics Section */}
            {showStats && supportsSync && (
              <Box>
                <ConnectorStatistics
                  title="Performance Statistics"
                  connector={connector}
                  showUploadTab={false}
                  showActions={isActive}
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

      {/* Rename Dialog */}
      <Dialog open={renameOpen} onClose={() => setRenameOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Rename Connector Instance</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Instance name"
            fullWidth
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <MuiButton onClick={() => setRenameOpen(false)}>Cancel</MuiButton>
          <MuiButton
            variant="contained"
            onClick={async () => {
              await handleRenameInstance(renameValue);
              setRenameOpen(false);
            }}
          >
            Save
          </MuiButton>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteOpen} onClose={() => setDeleteOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Delete Connector Instance</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            Are you sure you want to delete &quot;{connector.name}&quot;? This action cannot be
            undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <MuiButton onClick={() => setDeleteOpen(false)}>Cancel</MuiButton>
          <MuiButton
            color="error"
            variant="contained"
            onClick={async () => {
              await handleDeleteInstance();
              setDeleteOpen(false);
            }}
          >
            Delete
          </MuiButton>
        </DialogActions>
      </Dialog>
    </Container>
  );
};

export default ConnectorManager;