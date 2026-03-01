/**
 * Toolset Card
 *
 * Card component for displaying a user's toolset instance from /my-toolsets.
 * Shows instance auth status and provides a "Manage" button to authenticate.
 */

import React, { useState } from 'react';
import {
  useTheme,
  alpha,
  Box,
  Typography,
  Card,
  CardContent,
  Avatar,
  Button,
  Chip,
  Stack,
  Tooltip,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import clockCircleIcon from '@iconify-icons/mdi/clock-outline';
import settingsIcon from '@iconify-icons/mdi/settings';
import boltIcon from '@iconify-icons/mdi/bolt';
import eyeIcon from '@iconify-icons/mdi/eye';
import { MyToolset } from 'src/services/toolset-api';
import ToolsetConfigDialog from './toolset-config-dialog';

interface ToolsetCardProps {
  toolset: MyToolset;
  isAdmin?: boolean;
  onRefresh?: (showLoader?: boolean) => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

const ToolsetCard = ({ toolset, isAdmin = false, onRefresh, onShowToast }: ToolsetCardProps) => {
  const theme = useTheme();
  const [configOpen, setConfigOpen] = useState(false);
  const isDark = theme.palette.mode === 'dark';
  const toolsetImage = toolset.iconPath || '/assets/icons/toolsets/default.svg';

  const isAuthenticated = toolset.isAuthenticated || false;
  const isConfigured = toolset.isConfigured || true; // Admin-created instances are always configured

  const getStatusConfig = () => {
    if (isAuthenticated) {
      return {
        label: 'Authenticated',
        color: theme.palette.success.main,
        bgColor: isDark
          ? alpha(theme.palette.success.main, 0.8)
          : alpha(theme.palette.success.main, 0.1),
        icon: checkCircleIcon,
      };
    }
    if (isConfigured) {
      return {
        label: 'Not Authenticated',
        color: theme.palette.warning.main,
        bgColor: isDark
          ? alpha(theme.palette.warning.main, 0.8)
          : alpha(theme.palette.warning.main, 0.1),
        icon: clockCircleIcon,
      };
    }
    return {
      label: 'Setup Required',
      color: theme.palette.text.secondary,
      bgColor: isDark
        ? alpha(theme.palette.text.secondary, 0.8)
        : alpha(theme.palette.text.secondary, 0.08),
      icon: settingsIcon,
    };
  };

  const statusConfig = getStatusConfig();

  const handleManageClick = () => {
    setConfigOpen(true);
  };

  return (
    <>
      <Card
        elevation={0}
        sx={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 2,
          border: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.background.paper,
          cursor: 'pointer',
          transition: theme.transitions.create(
            ['transform', 'box-shadow', 'border-color'],
            {
              duration: theme.transitions.duration.shorter,
              easing: theme.transitions.easing.easeOut,
            }
          ),
          position: 'relative',
          '&:hover': {
            transform: 'translateY(-2px)',
            borderColor: alpha(theme.palette.primary.main, 0.5),
            boxShadow: isDark
              ? `0 8px 32px ${alpha('#000', 0.3)}`
              : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`,
            '& .toolset-avatar': {
              transform: 'scale(1.05)',
            },
          },
        }}
        onClick={handleManageClick}
      >
        {/* Category Badge */}
        <Box
          sx={{
            position: 'absolute',
            top: 8,
            left: 8,
            px: 0.75,
            py: 0.25,
            borderRadius: 0.75,
            fontSize: '0.6875rem',
            fontWeight: 600,
            color: theme.palette.text.secondary,
            backgroundColor: alpha(theme.palette.text.secondary, 0.08),
            border: `1px solid ${alpha(theme.palette.text.secondary, 0.12)}`,
            textTransform: 'capitalize',
          }}
        >
          {toolset.category?.toUpperCase() || 'Tool'}
        </Box>

        {/* Auth Status Dot */}
        {isAuthenticated && (
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

        <CardContent
          sx={{
            p: 2,
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            gap: 1.5,
            '&:last-child': { pb: 2 },
          }}
        >
          {/* Header */}
          <Stack spacing={1.5} alignItems="center">
            <Avatar
              className="toolset-avatar"
              sx={{
                width: 48,
                height: 48,
                backgroundColor: isDark
                  ? alpha(theme.palette.common.white, 0.9)
                  : alpha(theme.palette.grey[100], 0.8),
                border: `1px solid ${theme.palette.divider}`,
                transition: theme.transitions.create('transform'),
              }}
            >
              <img
                src={toolsetImage}
                alt={toolset.displayName}
                width={24}
                height={24}
                style={{ objectFit: 'contain' }}
                onError={(e) => {
                  const target = e.target as HTMLImageElement;
                  target.src = '/assets/icons/connectors/default.svg';
                }}
              />
            </Avatar>

            <Box sx={{ textAlign: 'center', width: '100%' }}>
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  color: theme.palette.text.primary,
                  mb: 0.25,
                  lineHeight: 1.2,
                }}
              >
                {toolset.instanceName || toolset.displayName}
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  color: theme.palette.text.secondary,
                  fontSize: '0.75rem',
                }}
              >
                {toolset.displayName}
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  display: 'block',
                  color: theme.palette.text.disabled,
                  fontSize: '0.6875rem',
                }}
              >
                {toolset.toolsetType}
              </Typography>
            </Box>
          </Stack>

          {/* Auth Status Chip */}
          <Box sx={{ display: 'flex', justifyContent: 'center' }}>
            <Chip
              icon={<Iconify icon={statusConfig.icon} width={14} height={14} />}
              label={statusConfig.label}
              size="small"
              sx={{
                height: 24,
                fontSize: '0.75rem',
                fontWeight: 500,
                backgroundColor: statusConfig.bgColor,
                color: statusConfig.color,
                border: `1px solid ${alpha(statusConfig.color, 0.2)}`,
                '& .MuiChip-icon': { color: statusConfig.color },
              }}
            />
          </Box>

          {/* Features */}
          <Stack
            direction="row"
            spacing={0.5}
            justifyContent="center"
            alignItems="center"
            sx={{ minHeight: 20 }}
          >
            {toolset.authType && (
              <Typography
                variant="caption"
                sx={{
                  px: 1,
                  py: 0.25,
                  borderRadius: 0.5,
                  fontSize: '0.6875rem',
                  fontWeight: 500,
                  color: theme.palette.text.secondary,
                  backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                  border: `1px solid ${alpha(theme.palette.text.secondary, 0.12)}`,
                }}
              >
                {toolset.authType.split('_').join(' ')}
              </Typography>
            )}

            {toolset.toolCount > 0 && (
              <Tooltip title={`${toolset.toolCount} tools available`} arrow>
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 0.5,
                    px: 1,
                    py: 0.25,
                    borderRadius: 0.5,
                    fontSize: '0.6875rem',
                    fontWeight: 500,
                    color: theme.palette.info.main,
                    backgroundColor: alpha(theme.palette.info.main, 0.08),
                    border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                  }}
                >
                  <Iconify icon={boltIcon} width={10} height={10} />
                  <Typography variant="caption" sx={{ fontSize: '0.6875rem', fontWeight: 500, color: 'inherit' }}>
                    {toolset.toolCount} tools
                  </Typography>
                </Box>
              </Tooltip>
            )}
          </Stack>

          {/* Manage Button */}
          <Button
            fullWidth
            variant="outlined"
            size="medium"
            startIcon={<Iconify icon={eyeIcon} width={16} height={16} />}
            onClick={(e) => {
              e.stopPropagation();
              handleManageClick();
            }}
            sx={{
              mt: 'auto',
              height: 38,
              borderRadius: 1.5,
              textTransform: 'none',
              fontWeight: 600,
              fontSize: '0.8125rem',
              borderColor: alpha(theme.palette.primary.main, 0.3),
              '&:hover': {
                borderColor: theme.palette.primary.main,
                backgroundColor: alpha(theme.palette.primary.main, 0.04),
              },
            }}
          >
            {isAuthenticated ? 'Manage' : 'Authenticate'}
          </Button>
        </CardContent>
      </Card>

      {configOpen && (
        <ToolsetConfigDialog
          toolset={toolset}
          toolsetId={toolset.instanceId}
          isAdmin={isAdmin}
          onClose={() => setConfigOpen(false)}
          onSuccess={() => {
            setConfigOpen(false);
            if (onRefresh) {
              onRefresh(false);
            }
          }}
          onShowToast={onShowToast}
        />
      )}
    </>
  );
};

export default ToolsetCard;
