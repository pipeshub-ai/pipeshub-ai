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
import { useNavigate } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import clockCircleIcon from '@iconify-icons/mdi/clock-outline';
import settingsIcon from '@iconify-icons/mdi/settings';
import plusCircleIcon from '@iconify-icons/mdi/plus-circle';
import boltIcon from '@iconify-icons/mdi/bolt';
import { Connector } from '../types/types';
import ConnectorConfigForm from './connector-config/connector-config-form';
import { isNoneAuthType } from '../utils/auth';

interface ConnectorCardProps {
  connector: Connector;
}

const ConnectorCard = ({ connector }: ConnectorCardProps) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [isConfigFormOpen, setIsConfigFormOpen] = useState(false);
  const isDark = theme.palette.mode === 'dark';

  const connectorImage = connector.iconPath;
  

  const isActive = connector.isActive;
  const isConfigured = connector.isConfigured;

  const getStatusConfig = () => {
    if (isActive) {
      return {
        label: 'Active',
        color: theme.palette.success.main,
        bgColor:  isDark ? alpha(theme.palette.success.main, 0.8) : alpha(theme.palette.success.main, 0.1),
        icon: checkCircleIcon,
      };
    }
    if (isConfigured) {
      return {
        label: 'Configured',
        color: theme.palette.warning.main,
        bgColor: isDark ? alpha(theme.palette.warning.main, 0.8) : alpha(theme.palette.warning.main, 0.1),
        icon: clockCircleIcon,
      };
    }
    return {
      label: 'Setup Required',
      color: theme.palette.text.secondary,
      bgColor: isDark ? alpha(theme.palette.text.secondary, 0.8) : alpha(theme.palette.text.secondary, 0.08),
      icon: settingsIcon,
    };
  };

  const statusConfig = getStatusConfig();

  const getActionConfig = () => {
    if (isActive) {
      return {
        text: 'Manage',
        icon: settingsIcon,
        variant: 'outlined' as const,
      };
    }
    if (isConfigured) {
      return {
        text: 'Manage',
        icon: settingsIcon,
        variant: 'outlined' as const,
      };
    }
    return {
      text: 'Configure',
      icon: plusCircleIcon,
      variant: 'outlined' as const,
      color: 'primary' as const,
    };
  };

  const actionConfig = getActionConfig();

  const configureConnector = () => {
    if (!connector.isConfigured) {
      setIsConfigFormOpen(true);
      return;
    }
    // If already configured, navigate to the management page
    navigate(`${connector.name}`);
  };

  const handleConfigFormClose = () => {
    setIsConfigFormOpen(false);
  };

  const handleConfigSuccess = () => {
    setIsConfigFormOpen(false);
    // Optionally refresh the connector data or show success message
    navigate(`${connector.name}`);
  };

  return (
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
          '& .connector-avatar': {
            transform: 'scale(1.05)',
          },
        },
      }}
      onClick={() => configureConnector()}
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
            className="connector-avatar"
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
              src={connectorImage} 
              alt={connector.name} 
              width={24} 
              height={24}
              style={{ objectFit: 'contain' }}
              onError={(e) => {
                const target = e.target as HTMLImageElement;
                target.src = connector.iconPath || '/assets/icons/connectors/default.svg';
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
              {connector.name}
            </Typography>
            <Typography
              variant="caption"
              sx={{
                color: theme.palette.text.secondary,
                fontSize: '0.8125rem',
              }}
            >
              {connector.appGroup}
            </Typography>
          </Box>
        </Stack>

        {/* Status */}
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
              '& .MuiChip-icon': {
                color: statusConfig.color,
              },
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
          {!isNoneAuthType(connector.authType) && (
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
              {connector.authType.split('_').join(' ')}
            </Typography>
          )}
          
          {connector.supportsRealtime && (
            <Tooltip title="Real-time sync supported" arrow>
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
                <Iconify 
                  icon={boltIcon} 
                  width={10} 
                  height={10}
                />
                <Typography
                  variant="caption"
                  sx={{
                    fontSize: '0.6875rem',
                    fontWeight: 500,
                    color: 'inherit',
                  }}
                >
                  Real-time
                </Typography>
              </Box>
            </Tooltip>
          )}
        </Stack>

        {/* Connection Status */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            px: 1.5,
            py: 1,
            borderRadius: 1,
            backgroundColor: isDark 
              ? alpha(theme.palette.background.default, 0.3) 
              : alpha(theme.palette.grey[50], 0.8),
            border: `1px solid ${theme.palette.divider}`,
          }}
        >
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Box
              sx={{
                width: 4,
                height: 4,
                borderRadius: '50%',
                backgroundColor: isConfigured 
                  ? theme.palette.warning.main 
                  : theme.palette.text.disabled,
              }}
            />
            <Typography
              variant="caption"
              sx={{
                fontSize: '0.75rem',
                fontWeight: 500,
                color: theme.palette.text.secondary,
              }}
            >
              {isConfigured ? 'Configured' : 'Not configured'}
            </Typography>
          </Stack>
          
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Box
              sx={{
                width: 4,
                height: 4,
                borderRadius: '50%',
                backgroundColor: isActive 
                  ? theme.palette.success.main 
                  : theme.palette.text.disabled,
              }}
            />
            <Typography
              variant="caption"
              sx={{
                fontSize: '0.75rem',
                fontWeight: 500,
                color: theme.palette.text.secondary,
              }}
            >
              {isActive ? 'Active' : 'Inactive'}
            </Typography>
          </Stack>
        </Box>

        {/* Action Button */}
        {isConfigFormOpen && (
          <Box
            // Prevent clicks inside the dialog from bubbling to the Card
            onClick={(e) => e.stopPropagation()}
          >
            <ConnectorConfigForm 
              connector={connector} 
              onClose={handleConfigFormClose}
              onSuccess={handleConfigSuccess}
            />
          </Box>
        )}
        <Button
          fullWidth 
          variant={actionConfig.variant}
          color={actionConfig.color}
          size="medium"
          startIcon={
            <Iconify
              icon={actionConfig.icon}
              width={16}
              height={16}
            />
          }
          onClick={(e) => {
            // Avoid re-triggering Card's onClick when clicking the button
            e.stopPropagation();
            configureConnector();
          }}
          sx={{
            mt: 'auto',
            height: 38,
            borderRadius: 1.5,
            textTransform: 'none',
            fontWeight: 600,
            fontSize: '0.8125rem',
            ...(actionConfig.variant === 'outlined' && {
              borderColor: alpha(theme.palette.primary.main, 0.3),
              '&:hover': {
                borderColor: theme.palette.primary.main,
                backgroundColor: alpha(theme.palette.primary.main, 0.04),
              },
            }),
          }}
        >
          {actionConfig.text}
        </Button>
      </CardContent>
    </Card>
  );
};

export default ConnectorCard;