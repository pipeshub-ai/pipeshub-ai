/**
 * OAuth Registry Card Component
 * 
 * Displays a single OAuth-enabled connector/tool type from the registry
 * Matches the styling of ConnectorRegistryCard
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
  Button,
  Chip,
  Stack,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import plusCircleIcon from '@iconify-icons/mdi/plus-circle';

interface OAuthRegistryCardProps {
  connector: {
    connectorType: string;
    name: string;
    appGroup?: string;
    appDescription?: string;
    appCategories?: string[];
    iconPath?: string;
  };
  onCreateClick: () => void;
}

const OAuthRegistryCard: React.FC<OAuthRegistryCardProps> = ({
  connector,
  onCreateClick,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const connectorImage = connector.iconPath;

  const handleCreateClick = () => {
    onCreateClick();
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
                alt={connector.name}
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
                icon="mdi:link-variant"
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
              {(connector.name)[0].toUpperCase() + (connector.name).slice(1).toLowerCase()}
            </Typography>
            <Typography
              variant="caption"
              sx={{
                color: theme.palette.text.secondary,
                fontSize: '0.8125rem',
              }}
            >
              {connector.appGroup || 'OAuth Connector'}
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
          {connector.appDescription || 'OAuth-enabled connector'}
        </Typography>

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
          Create OAuth App
        </Button>
      </CardContent>
    </Card>
  );
};

export default OAuthRegistryCard;

