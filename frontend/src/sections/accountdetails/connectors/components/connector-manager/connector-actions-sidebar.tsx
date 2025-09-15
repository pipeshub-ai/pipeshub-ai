import React from 'react';
import {
  Paper,
  Typography,
  Button,
  Stack,
  Box,
  alpha,
  useTheme,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';
import refreshIcon from '@iconify-icons/mdi/refresh';
import pauseIcon from '@iconify-icons/mdi/pause';
import playIcon from '@iconify-icons/mdi/play';
import keyIcon from '@iconify-icons/mdi/key';
import { Connector } from '../../types/types';

interface ConnectorActionsSidebarProps {
  connector: Connector;
  isAuthenticated: boolean;
  loading: boolean;
  onAuthenticate: () => void;
  onConfigure: () => void;
  onRefresh: () => void;
  onToggle: (enabled: boolean) => void;
  hideAuthenticate?: boolean;
}

const ConnectorActionsSidebar: React.FC<ConnectorActionsSidebarProps> = ({
  connector,
  isAuthenticated,
  loading,
  onAuthenticate,
  onConfigure,
  onRefresh,
  onToggle,
  hideAuthenticate,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const isConfigured = connector.isConfigured || false;
  const isActive = connector.isActive || false;
  const authType = (connector.authType || '').toUpperCase();
  const isOauth = authType === 'OAUTH';
  // If authenticate is hidden (admin consent or business service-account flow), enabling should rely on configuration
  const canEnable = isActive
    ? true
    : (isOauth
        ? (hideAuthenticate ? isConfigured : isAuthenticated)
        : isConfigured);

  return (
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
          {(connector.authType || '').toUpperCase() === 'OAUTH' && !hideAuthenticate && (
            <Button
              variant="contained"
              fullWidth
              size="small"
              startIcon={<Iconify icon={keyIcon} width={14} height={14} />}
              onClick={onAuthenticate}
              disabled={loading || isAuthenticated}
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                justifyContent: 'flex-start',
                borderRadius: 1,
                backgroundColor: isAuthenticated ? theme.palette.success.main : theme.palette.secondary.main,
                '&:hover': {
                  backgroundColor: isAuthenticated 
                    ? alpha(theme.palette.success.main, 0.8)
                    : alpha(theme.palette.secondary.main, 0.8),
                },
              }}
            >
              {isAuthenticated ? 'Authenticated' : 'Authenticate'}
            </Button>
          )}

          <Button
            variant={!isConfigured ? 'contained' : 'outlined'}
            fullWidth
            size="small"
            startIcon={<Iconify icon={settingsIcon} width={14} height={14} />}
            onClick={onConfigure}
            sx={{
              textTransform: 'none',
              fontWeight: 500,
              justifyContent: 'flex-start',
              borderRadius: 1,
              ...(!isConfigured && {
                backgroundColor: theme.palette.secondary.main,
                '&:hover': {
                  backgroundColor: isDark
                    ? alpha(theme.palette.secondary.main, 0.8)
                    : alpha(theme.palette.secondary.main, 0.8),
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
            onClick={onRefresh}
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
                  icon={isActive ? pauseIcon : playIcon}
                  width={14}
                  height={14}
                />
              }
              onClick={() => onToggle(!isActive)}
              disabled={!isActive && !canEnable}
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
  );
};

export default ConnectorActionsSidebar;
