import React from 'react';
import {
  Container,
  Box,
  Stack,
  Skeleton,
  Grid,
  Paper,
  alpha,
  useTheme,
} from '@mui/material';

interface ConnectorLoadingSkeletonProps {
  showStats?: boolean;
}

const ConnectorLoadingSkeleton: React.FC<ConnectorLoadingSkeletonProps> = ({ 
  showStats = true 
}) => {
  const theme = useTheme();

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* Header Skeleton */}
        <Box
          sx={{
            p: 2,
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: theme.palette.background.paper,
          }}
        >
          <Stack spacing={2}>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Skeleton variant="circular" width={40} height={40} />
              <Skeleton variant="circular" width={40} height={40} />
              <Box sx={{ flex: 1 }}>
                <Skeleton variant="text" width="40%" height={32} />
                <Skeleton variant="text" width="60%" height={20} sx={{ mt: 0.5 }} />
              </Box>
              <Skeleton variant="rectangular" width={80} height={36} sx={{ borderRadius: 1 }} />
            </Stack>
          </Stack>
        </Box>

        {/* Content Skeleton */}
        <Box sx={{ p: 2 }}>
          <Stack spacing={2}>
            {/* Main Content Grid Skeleton */}
            <Grid container spacing={2}>
              {/* Main Connector Card Skeleton */}
              <Grid item xs={12} md={8}>
                <Paper
                  elevation={0}
                  sx={{
                    p: 2.5,
                    borderRadius: 1.5,
                    border: '1px solid',
                    borderColor: theme.palette.divider,
                    bgcolor: 'transparent',
                  }}
                >
                  {/* Connector Info Skeleton */}
                  <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
                    <Skeleton variant="circular" width={48} height={48} />
                    <Box sx={{ flex: 1 }}>
                      <Skeleton variant="text" width="30%" height={28} />
                      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 0.5 }}>
                        <Skeleton variant="text" width="25%" height={20} />
                        <Skeleton variant="circular" width={3} height={3} />
                        <Skeleton variant="rectangular" width={60} height={20} sx={{ borderRadius: 1 }} />
                        <Skeleton variant="circular" width={3} height={3} />
                        <Skeleton variant="rectangular" width={70} height={20} sx={{ borderRadius: 1 }} />
                      </Stack>
                    </Box>
                  </Stack>

                  {/* Status Control Skeleton */}
                  <Box
                    sx={{
                      p: 2,
                      borderRadius: 1,
                      bgcolor: alpha(theme.palette.grey[50], 0.5),
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    <Stack direction="row" alignItems="center" justifyContent="space-between">
                      <Box>
                        <Skeleton variant="text" width="40%" height={20} sx={{ mb: 0.5 }} />
                        <Skeleton variant="text" width="60%" height={16} />
                      </Box>
                      <Skeleton variant="rectangular" width={58} height={34} sx={{ borderRadius: 17 }} />
                    </Stack>
                  </Box>
                </Paper>
              </Grid>

              {/* Actions Sidebar Skeleton */}
              <Grid item xs={12} md={4}>
                <Stack spacing={1.5}>
                  {/* Quick Actions Skeleton */}
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
                    <Skeleton variant="text" width="50%" height={20} sx={{ mb: 1.5 }} />
                    <Stack spacing={1}>
                      <Skeleton variant="rectangular" width="100%" height={32} sx={{ borderRadius: 1 }} />
                      <Skeleton variant="rectangular" width="100%" height={32} sx={{ borderRadius: 1 }} />
                      <Skeleton variant="rectangular" width="100%" height={32} sx={{ borderRadius: 1 }} />
                    </Stack>
                  </Paper>

                  {/* Connection Status Skeleton */}
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
                    <Skeleton variant="text" width="60%" height={20} sx={{ mb: 1.5 }} />
                    <Stack spacing={1.5}>
                      <Stack direction="row" alignItems="center" justifyContent="space-between">
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Skeleton variant="circular" width={6} height={6} />
                          <Skeleton variant="text" width="40%" height={16} />
                        </Stack>
                        <Skeleton variant="text" width="30%" height={16} />
                      </Stack>
                      <Stack direction="row" alignItems="center" justifyContent="space-between">
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Skeleton variant="circular" width={6} height={6} />
                          <Skeleton variant="text" width="35%" height={16} />
                        </Stack>
                        <Skeleton variant="text" width="25%" height={16} />
                      </Stack>
                    </Stack>
                  </Paper>
                </Stack>
              </Grid>
            </Grid>

            {/* Info Alert Skeleton */}
            <Skeleton variant="rectangular" height={60} sx={{ borderRadius: 1 }} />

            {/* Statistics Section Skeleton */}
            {showStats && (
              <Box>
                <Skeleton variant="text" width="40%" height={24} sx={{ mb: 2 }} />
                <Grid container spacing={2}>
                  {[1, 2, 3, 4].map((_, index) => (
                    <Grid item xs={12} sm={6} md={3} key={index}>
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
                        <Skeleton variant="text" width="60%" height={16} sx={{ mb: 1 }} />
                        <Skeleton variant="text" width="40%" height={24} />
                        <Skeleton variant="text" width="80%" height={14} sx={{ mt: 0.5 }} />
                      </Paper>
                    </Grid>
                  ))}
                </Grid>
              </Box>
            )}
          </Stack>
        </Box>
      </Box>
    </Container>
  );
};

export default ConnectorLoadingSkeleton;
