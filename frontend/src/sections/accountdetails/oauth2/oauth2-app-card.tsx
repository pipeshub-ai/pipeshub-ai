import {
  Box,
  Card,
  Chip,
  alpha,
  Stack,
  Avatar,
  useTheme,
  Typography,
  CardContent,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import type { OAuth2App } from './services/oauth2-api';

interface OAuth2AppCardProps {
  app: OAuth2App;
  onClick?: () => void;
}

export function OAuth2AppCard({ app, onClick }: OAuth2AppCardProps) {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const statusColor = app.status === 'active' ? theme.palette.success : theme.palette.warning;

  return (
    <Card
      elevation={0}
      onClick={onClick}
      sx={{
        height: '100%',
        minHeight: 200,
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
        '&:hover': onClick
          ? {
              transform: 'translateY(-2px)',
              borderColor: theme.palette.divider,
              boxShadow: isDark
                ? `0 8px 24px ${alpha('#000', 0.4)}`
                : `0 8px 24px ${alpha('#000', 0.1)}`,
            }
          : {},
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
        <Stack spacing={1.5} alignItems="center">
          <Avatar
            sx={{
              width: 48,
              height: 48,
              backgroundColor: alpha(theme.palette.primary.main, isDark ? 0.16 : 0.08),
              border: `1px solid ${alpha(theme.palette.primary.main, isDark ? 0.24 : 0.16)}`,
            }}
          >
            <Iconify
              icon="mdi:application-cog"
              width={24}
              height={24}
              sx={{ color: theme.palette.primary.main }}
            />
          </Avatar>
          <Box sx={{ textAlign: 'center', width: '100%' }}>
            <Typography
              sx={{
                fontWeight: 600,
                fontSize: '0.875rem',
                color: theme.palette.text.primary,
                mb: 0.25,
              }}
            >
              {app.name}
            </Typography>
            <Typography sx={{ color: theme.palette.text.secondary, fontSize: '0.75rem' }}>
              {app.clientId}
            </Typography>
          </Box>
        </Stack>

        {app.description && (
          <Typography
            variant="caption"
            sx={{
              color: theme.palette.text.secondary,
              fontSize: '0.75rem',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {app.description}
          </Typography>
        )}

        <Stack
          direction="row"
          spacing={0.5}
          justifyContent="center"
          flexWrap="wrap"
          sx={{ mt: 'auto' }}
        >
          <Chip
            size="small"
            label={app.status}
            sx={{
              height: 22,
              fontSize: '0.6875rem',
              fontWeight: 600,
              textTransform: 'capitalize',
              borderRadius: '100px',
              border: 'none',
              bgcolor: isDark ? alpha(statusColor.main, 0.95) : alpha(statusColor.main, 0.15),
              color: isDark ? statusColor.contrastText : statusColor.dark,
              '& .MuiChip-label': { px: 1 },
              '&:hover': { bgcolor: isDark ? alpha(statusColor.main, 0.95) : alpha(statusColor.main, 0.15) },
            }}
          />
          <Chip
            size="small"
            label={`${app.allowedScopes?.length ?? 0} scopes`}
            sx={{
              height: 22,
              fontSize: '0.6875rem',
              fontWeight: 600,
              borderRadius: '100px',
              border: 'none',
              bgcolor: isDark
                ? alpha(theme.palette.primary.main, 0.95)
                : alpha(theme.palette.primary.main, 0.15),
              color: isDark ? theme.palette.primary.contrastText : theme.palette.primary.dark,
              '& .MuiChip-label': { px: 1 },
              '&:hover': { bgcolor: isDark ? alpha(theme.palette.primary.main, 0.95) : alpha(theme.palette.primary.main, 0.15) },
            }}
          />
        </Stack>
      </CardContent>
    </Card>
  );
}
