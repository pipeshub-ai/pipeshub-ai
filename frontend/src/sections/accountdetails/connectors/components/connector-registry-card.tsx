/**
 * Connector Registry Card
 *
 * Card component for displaying connector types from the registry.
 * Shows connector information and allows creating new instances.
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
import plusCircleIcon from '@iconify-icons/mdi/plus-circle';
import boltIcon from '@iconify-icons/mdi/bolt';
import { ConnectorRegistry } from '../types/types';
import ConnectorConfigForm from './connector-config/connector-config-form';

interface ConnectorRegistryCardProps {
  connector: ConnectorRegistry;
  scope?: 'personal' | 'team';
}

const ConnectorRegistryCard = ({ connector, scope = 'personal' }: ConnectorRegistryCardProps) => {
  const theme = useTheme();
  const [configOpen, setConfigOpen] = useState(false);
  const isDark = theme.palette.mode === 'dark';
  const connectorImage = connector.iconPath;

  const handleCreateClick = () => {
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
          transition: theme.transitions.create(['transform', 'box-shadow', 'border-color'], {
            duration: theme.transitions.duration.shorter,
            easing: theme.transitions.easing.easeOut,
          }),
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
        onClick={handleCreateClick}
      >
        {/* Capability Badge */}
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
            color: connector.supportsSync ? theme.palette.success.main : theme.palette.text.secondary,
            backgroundColor: connector.supportsSync
              ? alpha(theme.palette.success.main, 0.08)
              : alpha(theme.palette.text.secondary, 0.08),
            border: `1px solid ${connector.supportsSync ? alpha(theme.palette.success.main, 0.2) : alpha(theme.palette.text.secondary, 0.12)}`,
          }}
        >
          {connector.supportsSync ? 'Sync + Agent' : 'Agent-only'}
        </Box>

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
                {(connector.name)[0].toUpperCase() + (connector.name).slice(1).toLowerCase()}
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

          {/* Description */}
          <Typography
            variant="caption"
            sx={{
              color: theme.palette.text.secondary,
              fontSize: '0.75rem',
              textAlign: 'center',
              minHeight: 32,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {connector.appDescription || 'No description available'}
          </Typography>

          {/* Features */}
          <Stack
            direction="row"
            spacing={0.5}
            justifyContent="center"
            alignItems="center"
            sx={{ minHeight: 20 }}
          >
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
                  <Iconify icon={boltIcon} width={10} height={10} />
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

          {/* Categories */}
          {connector.appCategories && connector.appCategories.length > 0 && (
            <Stack direction="row" spacing={0.5} justifyContent="center" flexWrap="wrap">
              {connector.appCategories.slice(0, 3).map((category) => (
                <Chip
                  key={category}
                  label={category}
                  size="small"
                  sx={{
                    height: 20,
                    fontSize: '0.6875rem',
                    fontWeight: 500,
                    textTransform: 'capitalize',
                    backgroundColor: isDark ? alpha(theme.palette.common.white, 0.48) : alpha(theme.palette.primary.light, 0.48),
                    color: isDark ? alpha(theme.palette.primary.main, 0.6) : theme.palette.grey[800],
                    border: `1px solid ${isDark ? alpha(theme.palette.common.white, 0.2) : alpha(theme.palette.grey[100], 0.2)}`,
                  }}
                />
              ))}
            </Stack>
          )}

          {/* Create Button */}
          <Button
            fullWidth
            variant="outlined"
            size="medium"
            startIcon={<Iconify icon={plusCircleIcon} width={16} height={16} />}
            onClick={(e) => {
              e.stopPropagation();
              handleCreateClick();
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
            Create Connector
          </Button>
        </CardContent>
      </Card>

      {configOpen && (
        <ConnectorConfigForm
          // Pass a minimal connector-like object for create mode
          connector={{
            _key: 'new',
            name: connector.name,
            type: connector.type,
            appGroup: connector.appGroup,
            appGroupId: (connector as any).appGroupId || '',
            authType: connector.authType,
            iconPath: connector.iconPath,
            appDescription: connector.appDescription || '',
            appCategories: connector.appCategories || [],
            isActive: false,
            isConfigured: false,
            isAgentActive: false,
            supportsAgent: !!connector.supportsAgent,
            supportsRealtime: !!connector.supportsRealtime,
            scope,
            supportsSync: !!connector.supportsSync,
            createdAtTimestamp: Date.now(),
            updatedAtTimestamp: Date.now(),
          }}
          initialInstanceName={`${(connector.name)[0].toUpperCase() + (connector.name).slice(1).toLowerCase()}`}
          onClose={() => setConfigOpen(false)}
          onSuccess={() => setConfigOpen(false)}
        />
      )}
    </>
  );
};

export default ConnectorRegistryCard;
