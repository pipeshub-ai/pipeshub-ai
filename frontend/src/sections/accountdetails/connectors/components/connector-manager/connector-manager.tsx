import React from 'react';
import { useNavigate } from 'react-router-dom';
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
  Button,
  Paper,
  IconButton,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import infoIcon from '@iconify-icons/eva/info-outline';
import lockIcon from '@iconify-icons/mdi/lock-outline';
import errorOutlineIcon from '@iconify-icons/mdi/error-outline';
import settingsIcon from '@iconify-icons/mdi/settings';
import refreshIcon from '@iconify-icons/mdi/refresh';
import arrowBackIcon from '@iconify-icons/mdi/arrow-left';
import closeIcon from '@iconify-icons/eva/close-outline';
import editIcon from '@iconify-icons/eva/edit-outline';
import warningIcon from '@iconify-icons/eva/alert-triangle-outline';
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
    renameError,

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
    setRenameError,
  } = useConnectorManager();

  const { isBusiness } = useAccountType();
  const navigate = useNavigate();
  const [renameOpen, setRenameOpen] = React.useState(false);
  const [renameValue, setRenameValue] = React.useState('');
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [renameLoading, setRenameLoading] = React.useState(false);
  const [deleteLoading, setDeleteLoading] = React.useState(false);

  // Handle rename dialog close - extracted to avoid duplication
  const handleRenameClose = React.useCallback(() => {
    if (!renameLoading) {
      setRenameOpen(false);
      setRenameValue('');
      setRenameError(null);
    }
  }, [renameLoading, setRenameError]);

  // Handle rename submit - extracted for reuse
  const handleRenameSubmit = React.useCallback(async () => {
    if (!connector) return;

    const newName = renameValue.trim();
    setRenameLoading(true);
    try {
      const result = await handleRenameInstance(newName, connector.name || '');

      if (result.success) {
        // Only close dialog on actual API success
        handleRenameClose();
      } else {
        // Error - show error in field and keep dialog open
        setRenameError(result.error || 'Failed to rename connector instance');
      }
    } finally {
      setRenameLoading(false);
    }
  }, [connector, renameValue, handleRenameInstance, handleRenameClose, setRenameError]);


  // Loading state with skeleton
  if (loading) {
    return <ConnectorLoadingSkeleton showStats={showStats} />;
  }

  // Error state - Unified with connector manager design
  if (error || !connector) {
    // Determine error type
    const isBetaAccessError = 
      error?.includes('Beta connectors are not enabled') || 
      error?.includes('beta connector') ||
      error?.toLowerCase()?.includes('beta');
    
    const isNotFoundError = !connector || error?.toLowerCase()?.includes('not found');

    // Navigation helpers
    const handleNavigate = (path: string) => {
      navigate(path);
    };

    const getPlatformSettingsPath = () => {
      const isBusinessAccount = window.location.pathname.includes('/company-settings');
      const basePath = isBusinessAccount ? '/account/company-settings' : '/account/individual';
      return `${basePath}/settings/platform`;
    };

    const getConnectorsPath = () => {
      const isBusinessAccount = window.location.pathname.includes('/company-settings');
      const basePath = isBusinessAccount ? '/account/company-settings' : '/account/individual';
      return `${basePath}/settings/connector`;
    };

    return (
      <Container maxWidth="xl" sx={{ py: 2 }}>
        <Box
          sx={{
            borderRadius: 2,
            backgroundColor: theme.palette.background.paper,
            border: `1px solid ${theme.palette.divider}`,
            overflow: 'hidden',
          }}
        >
          <Box sx={{ p: 3 }}>
            <Stack spacing={3}>
              {/* Error Icon and Title */}
              <Stack direction="row" spacing={2} alignItems="center">
                <Box
                  sx={{
                    width: 48,
                    height: 48,
                    borderRadius: 1.5,
                    bgcolor: isBetaAccessError 
                      ? alpha(theme.palette.warning.main, 0.08)
                      : alpha(theme.palette.error.main, 0.08),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Iconify 
                    icon={isBetaAccessError ? lockIcon : errorOutlineIcon}
                    width={28}
                    sx={{ 
                      color: isBetaAccessError 
                        ? theme.palette.warning.main 
                        : theme.palette.error.main 
                    }}
                  />
                </Box>
                <Box>
                  <Typography variant="h6" fontWeight={600}>
                    {isBetaAccessError
                      ? 'Beta Connector Access Required'
                      : isNotFoundError
                      ? 'Connector Not Found'
                      : 'Unable to Load Connector'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {isBetaAccessError
                      ? 'Enable beta features to access this connector'
                      : isNotFoundError
                      ? 'This connector is not available'
                      : 'An error occurred while loading'}
                  </Typography>
                </Box>
              </Stack>

              {/* Main Alert */}
              <Alert
                severity={isBetaAccessError ? 'warning' : 'error'}
                variant="outlined"
                icon={<Iconify icon={isBetaAccessError ? infoIcon : errorOutlineIcon} width={20} />}
                sx={{
                  borderRadius: 1,
                  borderColor: isBetaAccessError
                    ? alpha(theme.palette.warning.main, 0.2)
                    : alpha(theme.palette.error.main, 0.2),
                  backgroundColor: isBetaAccessError
                    ? alpha(theme.palette.warning.main, 0.04)
                    : alpha(theme.palette.error.main, 0.04),
                }}
              >
                <AlertTitle sx={{ fontWeight: 600, fontSize: '0.875rem', mb: 0.5 }}>
                  {isBetaAccessError ? 'Beta Access Required' : 'Error Details'}
                </AlertTitle>
                <Typography variant="body2" sx={{ mb: 1 }}>
                  {isBetaAccessError
                    ? 'This connector is currently in beta and requires special access. Enable beta connectors in your platform settings to use this feature.'
                    : isNotFoundError
                    ? 'The requested connector could not be found. It may have been removed or you may not have access to it.'
                    : error || 'An unexpected error occurred while loading the connector configuration. Please try again or contact support if the issue persists.'}
                </Typography>

                {/* Technical Error Details (only for non-beta errors) */}
                {error && !isBetaAccessError && !isNotFoundError && (
                  <Box
                    sx={{
                      mt: 1.5,
                      p: 1.5,
                      bgcolor: isDark
                        ? alpha(theme.palette.common.black, 0.2)
                        : alpha(theme.palette.common.black, 0.03),
                      borderRadius: 1,
                      border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
                    }}
                  >
                    <Typography 
                      variant="caption" 
                      sx={{ 
                        fontFamily: 'monospace',
                        fontSize: '0.75rem',
                        wordBreak: 'break-word',
                        display: 'block',
                        color: 'text.secondary',
                      }}
                    >
          {error}
                    </Typography>
                  </Box>
                )}
        </Alert>

              {/* Beta Information Box */}
              {isBetaAccessError && (
                <Paper
                  variant="outlined"
                  sx={{
                    p: 2,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.info.main, 0.04),
                    borderColor: alpha(theme.palette.info.main, 0.2),
                  }}
                >
                  <Stack direction="row" spacing={1.5} alignItems="flex-start">
                    <Iconify 
                      icon={infoIcon} 
                      width={20} 
                      sx={{ 
                        color: theme.palette.info.main,
                        mt: 0.25,
                      }} 
                    />
                    <Box>
                      <Typography 
                        variant="body2" 
                        fontWeight={600}
                        sx={{ mb: 0.5 }}
                      >
                        About Beta Connectors
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                        Beta connectors are new integrations currently being tested and refined. 
                        They may have limited features or occasional issues. Enable them in platform 
                        settings to access early features and help us improve them.
                      </Typography>
                    </Box>
                  </Stack>
                </Paper>
              )}

              {/* Action Buttons */}
              <Stack direction="row" spacing={1.5} sx={{ pt: 1 }}>
                {isBetaAccessError ? (
                  <>
                    <Button
                      variant="contained"
                      size="medium"
                      startIcon={<Iconify icon={settingsIcon} width={20} />}
                      onClick={() => handleNavigate(getPlatformSettingsPath())}
                      sx={{ fontWeight: 600 }}
                    >
                      Enable Beta Connectors
                    </Button>
                    <Button
                      variant="outlined"
                      size="medium"
                      startIcon={<Iconify icon={arrowBackIcon} width={20} />}
                      onClick={() => handleNavigate(getConnectorsPath())}
                    >
                      Back to Connectors
                    </Button>
                  </>
                ) : (
                  <>
                    <Button
                      variant="contained"
                      size="medium"
                      startIcon={<Iconify icon={arrowBackIcon} width={20} />}
                      onClick={() => handleNavigate(getConnectorsPath())}
                      sx={{ fontWeight: 600 }}
                    >
                      Back to Connectors
                    </Button>
                    <Button
                      variant="outlined"
                      size="medium"
                      startIcon={<Iconify icon={refreshIcon} width={20} />}
                      onClick={() => window.location.reload()}
                    >
                      Retry
                    </Button>
                  </>
                )}
              </Stack>
            </Stack>
          </Box>
        </Box>
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
                  onToggle={handleToggleConnector}
                  onDelete={() => setDeleteOpen(true)}
                  onRename={() => {
                    setRenameValue(connector.name || '');
                    setRenameError(null);
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
                alignItems: 'center',
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
      <Dialog
        open={renameOpen}
        onClose={handleRenameClose}
        maxWidth="sm"
        fullWidth
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            overflow: 'hidden',
          },
        }}
      >
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            p: 2.5,
            pl: 3,
            color: theme.palette.text.primary,
            borderBottom: '1px solid',
            borderColor: theme.palette.divider,
            fontWeight: 500,
            fontSize: '1rem',
            m: 0,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                color: theme.palette.primary.main,
              }}
            >
              <Iconify icon={editIcon} width={18} height={18} />
            </Box>
            Rename Connector Instance
          </Box>

          <IconButton
            onClick={handleRenameClose}
            size="small"
            sx={{ color: theme.palette.text.secondary }}
            aria-label="close"
            disabled={renameLoading}
          >
            <Iconify icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        <DialogContent
          sx={{
            p: 0,
            '&.MuiDialogContent-root': {
              pt: 3,
              px: 3,
              pb: 0,
            },
          }}
        >
          <Box sx={{ mb: 3 }}>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mb: 2 }}
            >
              Enter a new name for this connector instance. The name will be updated immediately after saving.
            </Typography>

            <TextField
              autoFocus
              fullWidth
              label="Instance Name"
              placeholder="Enter instance name..."
              value={renameValue}
              onChange={(e) => {
                setRenameValue(e.target.value);
                // Clear error when user starts typing
                if (renameError) {
                  setRenameError(null);
                }
              }}
              error={!!renameError}
              helperText={renameError || 'Enter a new name for this connector instance'}
              disabled={renameLoading}
              onKeyPress={(e) => {
                if (e.key === 'Enter' && !renameLoading) {
                  e.preventDefault();
                  handleRenameSubmit();
                }
              }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1,
                  '&:hover .MuiOutlinedInput-notchedOutline': {
                    borderColor: theme.palette.primary.main,
                  },
                  '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                    borderWidth: 1,
                  },
                },
                '& .MuiInputLabel-root': {
                  fontWeight: 500,
                },
              }}
            />
          </Box>
        </DialogContent>

        <DialogActions
          sx={{
            p: 2.5,
            borderTop: '1px solid',
            borderColor: theme.palette.divider,
            bgcolor: alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Button
            variant="text"
            onClick={handleRenameClose}
            disabled={renameLoading}
            sx={{
              color: theme.palette.text.secondary,
              fontWeight: 500,
              '&:hover': {
                backgroundColor: alpha(theme.palette.divider, 0.8),
              },
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleRenameSubmit}
            disabled={renameLoading || !renameValue.trim()}
            sx={{
              bgcolor: theme.palette.primary.main,
              boxShadow: 'none',
              fontWeight: 500,
              '&:hover': {
                bgcolor: theme.palette.primary.dark,
                boxShadow: 'none',
              },
              px: 3,
            }}
          >
            {renameLoading ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteOpen}
        onClose={() => {
          if (!deleteLoading) {
            setDeleteOpen(false);
          }
        }}
        maxWidth="sm"
        fullWidth
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            overflow: 'hidden',
          },
        }}
      >
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            p: 2.5,
            pl: 3,
            color: theme.palette.text.primary,
            borderBottom: '1px solid',
            borderColor: theme.palette.divider,
            fontWeight: 500,
            fontSize: '1rem',
            m: 0,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.error.main, 0.1),
                color: theme.palette.error.main,
              }}
            >
              <Iconify icon={warningIcon} width={18} height={18} />
            </Box>
            Delete Connector Instance
          </Box>

          <IconButton
            onClick={() => {
              if (!deleteLoading) {
                setDeleteOpen(false);
              }
            }}
            size="small"
            sx={{ color: theme.palette.text.secondary }}
            aria-label="close"
            disabled={deleteLoading}
          >
            <Iconify icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        <DialogContent
          sx={{
            p: 0,
            '&.MuiDialogContent-root': {
              pt: 3,
              px: 3,
              pb: 0,
            },
          }}
        >
          <Box sx={{ mb: 3 }}>
            {connector?.isActive ? (
              <Stack spacing={2}>
                <Alert
                  severity="warning"
                  icon={<Iconify icon={warningIcon} width={20} height={20} />}
                  sx={{
                    borderRadius: 1.25,
                    bgcolor: isDark
                      ? alpha(theme.palette.warning.main, 0.15)
                      : alpha(theme.palette.warning.main, 0.08),
                    border: `1px solid ${alpha(theme.palette.warning.main, isDark ? 0.3 : 0.2)}`,
                    '& .MuiAlert-icon': {
                      color: theme.palette.warning.main,
                      fontSize: '1.25rem',
                    },
                  }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.75, fontSize: '0.875rem' }}>
                    Sync Must Be Disabled First
                  </Typography>
                  <Typography variant="body2" sx={{ fontSize: '0.8125rem', lineHeight: 1.5, mb: 1.5 }}>
                    The connector <strong>&quot;{connector?.name}&quot;</strong> has sync enabled. Please disable sync before deleting this connector instance.
                  </Typography>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={async () => {
                      try {
                        await handleToggleConnector(false, 'sync');
                        // Dialog will automatically update when connector state changes
                        // User can then proceed with deletion
                      } catch (err) {
                        console.error('Error disabling sync:', err);
                      }
                    }}
                    sx={{
                      mt: 1,
                      textTransform: 'none',
                      borderColor: theme.palette.warning.main,
                      color: theme.palette.warning.main,
                      '&:hover': {
                        borderColor: theme.palette.warning.dark,
                        bgcolor: alpha(theme.palette.warning.main, 0.08),
                      },
                    }}
                  >
                    Disable Sync Now
                  </Button>
                </Alert>

                <Typography
                  variant="body1"
                  color="text.primary"
                  sx={{
                    lineHeight: 1.6,
                    '& strong': {
                      fontWeight: 600,
                      color: theme.palette.text.primary,
                    },
                  }}
                >
                  Once sync is disabled, you can delete <strong>&quot;{connector?.name}&quot;</strong>. This action cannot be undone.
                </Typography>

                <Box
                  sx={{
                    p: 2,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                    border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
                  }}
                >
                  <Typography variant="body2" color="error.main" sx={{ fontWeight: 500 }}>
                    ⚠️ This action cannot be undone
                  </Typography>
                </Box>
              </Stack>
            ) : (
              <>
                <Typography
                  variant="body1"
                  color="text.primary"
                  sx={{
                    lineHeight: 1.6,
                    '& strong': {
                      fontWeight: 600,
                      color: theme.palette.text.primary,
                    },
                  }}
                >
                  Are you sure you want to delete <strong>&quot;{connector?.name}&quot;</strong>? This action cannot be undone.
                </Typography>

                <Box
                  sx={{
                    mt: 2,
                    p: 2,
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                    border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
                  }}
                >
                  <Typography variant="body2" color="error.main" sx={{ fontWeight: 500 }}>
                    ⚠️ This action cannot be undone
                  </Typography>
                </Box>
              </>
            )}
          </Box>
        </DialogContent>

        <DialogActions
          sx={{
            p: 2.5,
            borderTop: '1px solid',
            borderColor: theme.palette.divider,
            bgcolor: alpha(theme.palette.background.default, 0.5),
          }}
        >
          <Button
            variant="text"
            onClick={() => {
              if (!deleteLoading) {
                setDeleteOpen(false);
              }
            }}
            disabled={deleteLoading}
            sx={{
              color: theme.palette.text.secondary,
              fontWeight: 500,
              '&:hover': {
                backgroundColor: alpha(theme.palette.divider, 0.8),
              },
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            onClick={async () => {
              setDeleteLoading(true);
              try {
                await handleDeleteInstance();
                setDeleteOpen(false);
              } catch (err) {
                // Error is handled by handleDeleteInstance
                console.error('Error deleting connector instance:', err);
              } finally {
                setDeleteLoading(false);
              }
            }}
            disabled={deleteLoading || connector?.isActive}
            sx={{
              bgcolor: theme.palette.error.main,
              boxShadow: 'none',
              fontWeight: 500,
              '&:hover': {
                bgcolor: theme.palette.error.dark,
                boxShadow: 'none',
              },
              '&.Mui-disabled': {
                bgcolor: alpha(theme.palette.error.main, 0.3),
                color: alpha(theme.palette.error.contrastText, 0.5),
              },
              px: 3,
            }}
          >
            {deleteLoading ? 'Deleting...' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
};

export default ConnectorManager;