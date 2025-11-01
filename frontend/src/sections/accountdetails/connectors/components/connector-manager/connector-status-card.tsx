import React from 'react';
import {
  Paper,
  Box,
  Typography,
  Switch,
  Tooltip,
  Stack,
  Chip,
  alpha,
  useTheme,
} from '@mui/material';
import { Connector } from '../../types/types';
import { isNoneAuthType } from '../../utils/auth';

interface ConnectorStatusCardProps {
  connector: Connector;
  isAuthenticated: boolean;
  isEnablingWithFilters: boolean;
  onToggle: (enabled: boolean) => void;
  hideAuthenticate?: boolean;
}

const ConnectorStatusCard: React.FC<ConnectorStatusCardProps> = ({
  connector,
  isAuthenticated,
  isEnablingWithFilters,
  onToggle,
  hideAuthenticate,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const isConfigured = connector.isConfigured || false;
  const isActive = connector.isActive || false;
  const authType = (connector.authType || '').toUpperCase();
  const isOauth = authType === 'OAUTH';
  const canEnable = isActive
    ? true
    : (isOauth
        ? (hideAuthenticate ? isConfigured : isAuthenticated)
        : isConfigured);
  const enableBlocked = !isActive && !canEnable;

  const getTooltipMessage = () => {
    if (!isActive && !canEnable) {
      if (isOauth) {
        return hideAuthenticate
          ? `${connector.name} needs to be configured before it can be enabled`
          : `Authenticate ${connector.name} before enabling`;
      }
      return `${connector.name} needs to be configured before it can be enabled`;
    }
    return '';
  };

  return (
    <Paper
      elevation={0}
      sx={{
        p: 2.5,
        borderRadius: 1.5,
        border: '1px solid',
        borderColor: isActive ? alpha(theme.palette.primary.main, 0.3) : theme.palette.divider,
        bgcolor: isActive ? alpha(theme.palette.primary.main, 0.02) : 'transparent',
        transition: theme.transitions.create(['border-color', 'box-shadow']),
        position: 'relative',
        '&:hover': {
          borderColor: alpha(theme.palette.primary.main, 0.4),
          boxShadow: `0 4px 16px ${alpha(theme.palette.primary.main, 0.08)}`,
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
            bgcolor: alpha(theme.palette.primary.main, 0.1),
            border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
            borderRadius: 1.5,
          }}
        >
          <img
            src={connector.iconPath}
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

            {!isNoneAuthType(connector.authType) && (
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
            )}

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
                : isEnablingWithFilters
                  ? 'Setting up filters...'
                  : isConfigured
                    ? 'Configured but inactive'
                    : 'Needs configuration'}
            </Typography>
          </Box>

          <Tooltip
            title={getTooltipMessage()}
            placement="top"
            arrow
            disableHoverListener={!enableBlocked}
          >
            <div>
              <Switch
                checked={isActive}
                onChange={(e) => {
                  const next = e.target.checked;
                  if (next && !canEnable) {
                    // Block enabling if prerequisites not met
                    return;
                  }
                  onToggle(next);
                }}
                disabled={!isActive && !canEnable}
                color="primary"
                size="medium"
                sx={{
                  '& .MuiSwitch-switchBase.Mui-checked': {
                    color: theme.palette.primary.main,
                    '&:hover': {
                      backgroundColor: isDark
                        ? alpha(theme.palette.primary.main, 0.9)
                        : alpha(theme.palette.primary.main, 0.1),
                    },
                  },
                  '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                    backgroundColor: isDark ? theme.palette.primary.main : theme.palette.primary.main,
                  },
                }}
              />
            </div>
          </Tooltip>
        </Stack>
      </Box>
    </Paper>
  );
};

export default ConnectorStatusCard;
