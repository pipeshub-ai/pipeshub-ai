// Premium loading skeleton components
import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Skeleton,
  Grid,
  useTheme,
  alpha,
  Stack,
} from '@mui/material';

// Agent Card Skeleton - Premium design
export const AgentCardSkeleton: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  
  const cardBg = isDark ? 'rgba(32, 30, 30, 0.5)' : '#ffffff';
  const cardBorder = isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)';
  const bgHeader = isDark ? 'rgba(255, 255, 255, 0.02)' : 'rgba(0, 0, 0, 0.02)';

  return (
    <Card
      sx={{
        height: '100%',
        minHeight: '280px',
        display: 'flex',
        flexDirection: 'column',
        borderRadius: '10px',
        bgcolor: cardBg,
        border: `1px solid ${cardBorder}`,
        overflow: 'hidden',
      }}
    >
      {/* Header Section */}
      <Box
        sx={{
          p: 2,
          borderBottom: `1px solid ${cardBorder}`,
          backgroundColor: bgHeader,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Skeleton
            variant="circular"
            width={40}
            height={40}
            sx={{
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              '&::after': {
                background: `linear-gradient(90deg, transparent, ${alpha(theme.palette.primary.main, 0.2)}, transparent)`,
              },
            }}
          />
          <Box sx={{ flex: 1 }}>
            <Skeleton
              variant="text"
              width="60%"
              height={20}
              sx={{
                bgcolor: alpha(theme.palette.text.primary, isDark ? 0.1 : 0.08),
                mb: 0.5,
              }}
            />
          </Box>
        </Box>
      </Box>

      {/* Content Section */}
      <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', flex: 1, gap: 1.5 }}>
        {/* Description */}
        <Box>
          <Skeleton
            variant="text"
            width="100%"
            height={14}
            sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
          />
          <Skeleton
            variant="text"
            width="80%"
            height={14}
            sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
          />
        </Box>

        {/* Tags */}
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <Skeleton
            variant="rounded"
            width={60}
            height={16}
            sx={{
              borderRadius: 0.5,
              bgcolor: alpha(theme.palette.secondary.main, 0.1),
            }}
          />
          <Skeleton
            variant="rounded"
            width={70}
            height={16}
            sx={{
              borderRadius: 0.5,
              bgcolor: alpha(theme.palette.secondary.main, 0.1),
            }}
          />
        </Box>

        {/* Stats & Actions */}
        <Box sx={{ mt: 'auto', pt: 1.5, borderTop: `1px solid ${cardBorder}` }}>
          <Box sx={{ mb: 1.5 }}>
            <Skeleton
              variant="rounded"
              width={100}
              height={18}
              sx={{
                borderRadius: 1,
                bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06),
              }}
            />
          </Box>

          {/* Action Buttons */}
          <Box sx={{ display: 'flex', gap: 0.75 }}>
            <Skeleton
              variant="rounded"
              height={28}
              sx={{
                flex: 1,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.primary.main, 0.1),
              }}
            />
            <Skeleton
              variant="rounded"
              height={28}
              sx={{
                flex: 1,
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.primary.main, 0.1),
              }}
            />
            <Skeleton
              variant="rounded"
              width={28}
              height={28}
              sx={{
                borderRadius: '6px',
                bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.1 : 0.08),
              }}
            />
          </Box>
        </Box>
      </CardContent>
    </Card>
  );
};

// Agent Grid Skeleton - Shows multiple agent cards
export const AgentGridSkeleton: React.FC<{ count?: number }> = ({ count = 8 }) => (
  <Grid container spacing={2.5}>
    {Array.from({ length: count }).map((_, index) => (
      <Grid item xs={12} sm={6} md={4} lg={3} key={index}>
        <AgentCardSkeleton />
      </Grid>
    ))}
  </Grid>
);

// Sidebar Skeleton - For agent builder sidebar
export const SidebarSkeleton: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  return (
    <Box sx={{ p: 2 }}>
      {/* Search Bar Skeleton */}
      <Skeleton
        variant="rounded"
        width="100%"
        height={40}
        sx={{
          mb: 3,
          borderRadius: 1.5,
          bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06),
        }}
      />

      {/* Category Sections */}
      {[1, 2, 3, 4].map((section) => (
        <Box key={section} sx={{ mb: 2 }}>
          {/* Category Header */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, px: 1 }}>
            <Skeleton
              variant="circular"
              width={16}
              height={16}
              sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.1 : 0.08) }}
            />
            <Skeleton
              variant="text"
              width={100}
              height={20}
              sx={{ bgcolor: alpha(theme.palette.text.primary, isDark ? 0.1 : 0.08) }}
            />
          </Box>

          {/* Category Items */}
          <Stack spacing={0.5} sx={{ pl: 2 }}>
            {[1, 2, 3].map((item) => (
              <Box
                key={item}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  p: 1,
                  borderRadius: 1,
                }}
              >
                <Skeleton
                  variant="rounded"
                  width={32}
                  height={32}
                  sx={{
                    borderRadius: 1,
                    bgcolor: alpha(theme.palette.primary.main, 0.1),
                  }}
                />
                <Box sx={{ flex: 1 }}>
                  <Skeleton
                    variant="text"
                    width="70%"
                    height={16}
                    sx={{ bgcolor: alpha(theme.palette.text.primary, isDark ? 0.1 : 0.08) }}
                  />
                  <Skeleton
                    variant="text"
                    width="90%"
                    height={12}
                    sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
                  />
                </Box>
              </Box>
            ))}
          </Stack>
        </Box>
      ))}
    </Box>
  );
};

// Header Skeleton - For page headers
export const HeaderSkeleton: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  return (
    <Box sx={{ px: 3, py: 2.5 }}>
      <Stack direction="row" alignItems="center" spacing={3}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Skeleton
            variant="circular"
            width={32}
            height={32}
            sx={{ bgcolor: alpha(theme.palette.primary.main, 0.1) }}
          />
          <Box>
            <Skeleton
              variant="text"
              width={200}
              height={32}
              sx={{ bgcolor: alpha(theme.palette.text.primary, isDark ? 0.1 : 0.08) }}
            />
            <Skeleton
              variant="text"
              width={160}
              height={20}
              sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
            />
          </Box>
        </Box>

        <Box sx={{ flexGrow: 1 }} />

        <Skeleton
          variant="rounded"
          width={300}
          height={36}
          sx={{
            borderRadius: 1.5,
            bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06),
          }}
        />

        <Stack direction="row" spacing={1}>
          <Skeleton
            variant="rounded"
            width={120}
            height={32}
            sx={{
              borderRadius: 1,
              bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06),
            }}
          />
          <Skeleton
            variant="rounded"
            width={100}
            height={32}
            sx={{
              borderRadius: 1,
              bgcolor: alpha(theme.palette.primary.main, 0.1),
            }}
          />
        </Stack>
      </Stack>
    </Box>
  );
};

