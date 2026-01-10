/**
 * OAuth App Card Component
 *
 * Displays a single OAuth app configuration
 * Matches the styling of OAuthRegistryCard
 */

import React from 'react';
import {
  useTheme,
  alpha,
  Box,
  Typography,
  Card,
  CardContent,
  Avatar,
  Chip,
  Stack,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';

interface OAuthAppCardProps {
  app: {
    _id: string;
    oauthInstanceName: string;
    connectorType: string;
    iconPath?: string;
    appGroup?: string;
    appDescription?: string;
    appCategories?: string[];
    createdAtTimestamp?: number;
    updatedAtTimestamp?: number;
  };
  onClick?: () => void;
}

const OAuthAppCard: React.FC<OAuthAppCardProps> = ({ app, onClick }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const connectorImage = app.iconPath;

  return (
    <Card
      elevation={0}
      onClick={onClick}
      sx={{
        height: '100%',
        minHeight: 250,
        display: 'flex',
        flexDirection: 'column',
        borderRadius: 2,
        border: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
        cursor: onClick ? 'pointer' : 'default',
        transition: theme.transitions.create(['transform', 'box-shadow', 'border-color'], {
          duration: theme.transitions.duration.shorter,
          easing: theme.transitions.easing.easeOut,
        }),
        position: 'relative',
        '&:hover': {
          transform: onClick ? 'translateY(-2px)' : 'none',
          borderColor: onClick ? alpha(theme.palette.primary.main, 0.5) : theme.palette.divider,
          boxShadow: onClick
            ? isDark
              ? `0 8px 32px ${alpha('#000', 0.3)}`
              : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`
            : 'none',
          '& .connector-avatar': {
            transform: onClick ? 'scale(1.05)' : 'none',
          },
        },
      }}
    >
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
            {connectorImage ? (
              <img
                src={connectorImage}
                alt={app.oauthInstanceName}
                width={24}
                height={24}
                style={{ objectFit: 'contain' }}
                onError={(e) => {
                  const target = e.target as HTMLImageElement;
                  target.src = '/assets/icons/connectors/default.svg';
                }}
              />
            ) : (
              <Iconify
                icon="mdi:key"
                width={24}
                height={24}
                sx={{ color: theme.palette.primary.main }}
              />
            )}
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
              {app.oauthInstanceName}
            </Typography>
            <Typography
              variant="caption"
              sx={{
                color: theme.palette.text.secondary,
                fontSize: '0.8125rem',
              }}
            >
              {app.appGroup || 'OAuth App'}
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
          {app.appDescription || 'OAuth app configuration'}
        </Typography>

        {/* Categories */}
        {app.appCategories && app.appCategories.length > 0 && (
          <Stack direction="row" spacing={0.5} justifyContent="center" flexWrap="wrap">
            {app.appCategories.slice(0, 3).map((category) => (
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
      </CardContent>
    </Card>
  );
};

export default OAuthAppCard;
