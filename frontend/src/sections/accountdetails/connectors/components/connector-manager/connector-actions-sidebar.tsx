import React, { useState } from 'react';
import {
  Paper,
  Typography,
  Button,
  Stack,
  Box,
  alpha,
  useTheme,
  IconButton,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import settingsIcon from '@iconify-icons/eva/settings-2-outline';
import pauseIcon from '@iconify-icons/mdi/pause';
import playIcon from '@iconify-icons/mdi/play';
import keyIcon from '@iconify-icons/mdi/key';
import deleteIcon from '@iconify-icons/mdi/delete';
import editIcon from '@iconify-icons/mdi/pencil';
import filterIcon from '@iconify-icons/mdi/filter';
import { Connector, ConnectorToggleType } from '../../types/types';

interface ConnectorActionsSidebarProps {
  connector: Connector;
  isAuthenticated: boolean;
  loading: boolean;
  onAuthenticate: () => void;
  onConfigureAuth: () => void;
  onConfigureSync: () => void;
  onToggle: (enabled: boolean, type: ConnectorToggleType) => void;
  onDelete: () => void;
  onRename: () => void;
  hideAuthenticate?: boolean;
}

const ConnectorActionsSidebar: React.FC<ConnectorActionsSidebarProps> = ({
  connector,
  isAuthenticated,
  loading,
  onAuthenticate,
  onConfigureAuth,
  onConfigureSync,
  onToggle,
  onDelete,
  onRename,
  hideAuthenticate,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const menuOpen = Boolean(menuAnchor);

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>) => {
    setMenuAnchor(event.currentTarget);
  };

  const handleMenuClose = () => {
    setMenuAnchor(null);
  };

  const handleRenameClick = () => {
    handleMenuClose();
    onRename();
  };

  const handleDeleteClick = () => {
    handleMenuClose();
    onDelete();
  };

  const isConfigured = connector.isConfigured || false;
  const isActive = connector.isActive || false;
  const authType = (connector.authType || '').toUpperCase();
  const isOauth = authType === 'OAUTH';
  // If authenticate is hidden (admin consent or business service-account flow), enabling should rely on configuration
  const canEnable = isActive
    ? true
    : isOauth
      ? hideAuthenticate
        ? isConfigured
        : isAuthenticated
      : isConfigured;
  const supportsSync = connector.supportsSync || false;
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
        <Box
          sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}
        >
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Quick Actions
          </Typography>
          <Box>
            <IconButton
              size="small"
              onClick={handleMenuOpen}
              sx={{
                color: 'text.secondary',
                '&:hover': {
                  bgcolor: alpha(theme.palette.primary.main, 0.08),
                  color: theme.palette.primary.main,
                },
              }}
            >
              <Iconify icon={settingsIcon} width={20} height={20} />
            </IconButton>
            <Menu
              anchorEl={menuAnchor}
              open={menuOpen}
              onClose={handleMenuClose}
              anchorOrigin={{
                vertical: 'bottom',
                horizontal: 'right',
              }}
              transformOrigin={{
                vertical: 'top',
                horizontal: 'right',
              }}
              PaperProps={{
                sx: {
                  mt: 0.5,
                  minWidth: 180,
                  borderRadius: 1.5,
                  boxShadow: isDark ? '0 4px 20px rgba(0,0,0,0.3)' : '0 4px 20px rgba(0,0,0,0.1)',
                },
              }}
            >
              <MenuItem
                onClick={handleRenameClick}
                sx={{
                  '&:hover': {
                    bgcolor: alpha(theme.palette.primary.main, 0.08),
                  },
                }}
              >
                <ListItemIcon>
                  <Iconify icon={editIcon} width={18} height={18} />
                </ListItemIcon>
                <ListItemText
                  primary="Rename Instance"
                  primaryTypographyProps={{ fontSize: '0.875rem' }}
                />
              </MenuItem>
              <Divider sx={{ my: 0.5 }} />
              <MenuItem
                onClick={handleDeleteClick}
                sx={{
                  color: 'error.main',
                  '&:hover': {
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                  },
                }}
              >
                <ListItemIcon>
                  <Iconify icon={deleteIcon} width={18} height={18} sx={{ color: 'error.main' }} />
                </ListItemIcon>
                <ListItemText
                  primary="Delete Instance"
                  primaryTypographyProps={{ fontSize: '0.875rem', color: 'error.main' }}
                />
              </MenuItem>
            </Menu>
          </Box>
        </Box>

        <Stack spacing={1}>
          {(connector.authType || '').toUpperCase() === 'OAUTH' && !hideAuthenticate && (
            <Button
              variant={isAuthenticated ? 'outlined' : 'contained'}
              fullWidth
              size="small"
              startIcon={<Iconify icon={keyIcon} width={14} height={14} />}
              onClick={onAuthenticate}
              disabled={loading}
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                justifyContent: 'flex-start',
                borderRadius: 1,
                ...(isAuthenticated
                  ? {
                      color: theme.palette.success.main,
                      borderColor: theme.palette.success.main,
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.success.main, 0.08),
                        borderColor: theme.palette.success.main,
                      },
                    }
                  : {
                      backgroundColor: theme.palette.secondary.main,
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.secondary.main, 0.8),
                      },
                    }),
              }}
            >
              {isAuthenticated ? 'Reauthenticate' : 'Authenticate'}
            </Button>
          )}

          <Button
            variant={!isConfigured ? 'contained' : 'outlined'}
            fullWidth
            size="small"
            startIcon={<Iconify icon={keyIcon} width={14} height={14} />}
            onClick={onConfigureAuth}
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
            Auth Settings
          </Button>

          {isConfigured && (
            <Button
              variant="outlined"
              fullWidth
              size="small"
              startIcon={<Iconify icon={filterIcon} width={14} height={14} />}
              onClick={onConfigureSync}
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                justifyContent: 'flex-start',
                borderRadius: 1,
              }}
            >
              Filter Settings
            </Button>
          )}

          {isConfigured && (
            <Button
              variant="outlined"
              fullWidth
              size="small"
              startIcon={<Iconify icon={isActive ? pauseIcon : playIcon} width={14} height={14} />}
              onClick={() => onToggle(!isActive, 'sync')}
              disabled={!isActive && !canEnable}
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                justifyContent: 'flex-start',
                borderRadius: 1,
                color: isActive ? theme.palette.warning.main : theme.palette.success.main,
                borderColor: isActive ? theme.palette.warning.main : theme.palette.success.main,
                '&:hover': {
                  backgroundColor: isActive
                    ? isDark
                      ? alpha(theme.palette.warning.main, 0.08)
                      : alpha(theme.palette.warning.main, 0.08)
                    : alpha(theme.palette.success.main, 0.08),
                },
              }}
            >
              {isActive ? 'Disable Sync' : 'Enable Sync'}
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
                  bgcolor: isConfigured ? theme.palette.warning.main : theme.palette.text.disabled,
                }}
              />
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
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

          {supportsSync && (
            <Stack direction="row" alignItems="center" justifyContent="space-between">
              <Stack direction="row" alignItems="center" spacing={1}>
                <Box
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    bgcolor: isActive ? theme.palette.success.main : theme.palette.text.disabled,
                  }}
                />
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
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
          )}
        </Stack>
      </Paper>
    </Stack>
  );
};

export default ConnectorActionsSidebar;
