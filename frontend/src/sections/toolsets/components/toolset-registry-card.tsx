/**
 * Toolset Registry Card
 *
 * Card component for displaying toolset types from the registry.
 * Shows toolset information and allows creating new configurations.
 * Matches connector registry card design.
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
import { RegistryToolset } from 'src/types/agent';
import ToolsetConfigDialog from './toolset-config-dialog';

interface ToolsetRegistryCardProps {
  toolset: RegistryToolset;
  isConfigured?: boolean;
  isAdmin?: boolean;
  onRefresh?: (showLoader?: boolean, forceRefreshBoth?: boolean) => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

const ToolsetRegistryCard = ({ toolset, isConfigured = false, isAdmin = false, onRefresh, onShowToast }: ToolsetRegistryCardProps) => {
  const theme = useTheme();
  const [configOpen, setConfigOpen] = useState(false);
  const isDark = theme.palette.mode === 'dark';
  const toolsetImage = toolset.iconPath || '/assets/icons/toolsets/default.svg';

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
            '& .toolset-avatar': {
              transform: 'scale(1.05)',
            },
          },
        }}
        onClick={handleCreateClick}
      >
        {/* Category Badge - matches connector registry card */}
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
          }}
        >
          Agent-only
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
                  target.src = '/assets/icons/toolsets/default.svg';
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
                {toolset.displayName}
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  color: theme.palette.text.secondary,
                  fontSize: '0.8125rem',
                }}
              >
                {toolset.appGroup || toolset.category?.toUpperCase()}
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
            {toolset.description || 'No description available'}
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
              {(toolset.supportedAuthTypes?.[0] || 'NONE').split('_').join(' ')}
            </Typography>

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
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: '0.6875rem',
                      fontWeight: 500,
                      color: 'inherit',
                    }}
                  >
                    {toolset.toolCount} tools
                  </Typography>
                </Box>
              </Tooltip>
            )}
          </Stack>

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
            Configure Toolset
          </Button>
        </CardContent>
      </Card>

      {configOpen && (
        <ToolsetConfigDialog
          toolset={toolset}
          isAdmin={isAdmin}
          onClose={() => setConfigOpen(false)}
          onSuccess={() => {
            setConfigOpen(false);
            // Refresh both tabs since creating a toolset affects "My Toolsets" tab
            if (onRefresh) {
              onRefresh(false, true); // showLoader=false, forceRefreshBoth=true
            }
          }}
          onShowToast={onShowToast}
        />
      )}
    </>
  );
};

export default ToolsetRegistryCard;
