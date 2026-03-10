import { useRef, useState, useEffect, useCallback } from 'react';
import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Card,
  Grid,
  Alert,
  Typography,
  AlertTitle,
  CardContent,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { ConnectorStatsCard } from './connector-stats-card';
import { ConnectorStatsData, ConnectorStatsResponse, Connector } from '../types/types';

// Ultra-minimalistic SaaS color palette - monochromatic with a single accent
const COLORS = {
  primary: '#3E4DBA', // Single brand accent color
  text: {
    primary: '#1F2937',
    secondary: '#6B7280',
  },
  status: {
    success: '#4B5563', // Dark gray for success
    error: '#6B7280', // Medium gray for error
    warning: '#9CA3AF', // Light gray for warning
    accent: '#3E4DBA', // Accent color for primary actions
  },
  backgrounds: {
    paper: '#FFFFFF',
    hover: '#F9FAFB',
    stats: '#F5F7FA',
  },
  border: '#E5E7EB',
};

interface ConnectorStatisticsProps {
  title?: string;
  connector: Connector;
  showUploadTab?: boolean;
  refreshInterval?: number; // Interval in milliseconds for auto-refresh
  showActions?: boolean;
}

/**
 * ConnectorStatistics Component
 * Displays performance statistics for a connector in a grid layout
 */
const ConnectorStatistics = ({
  title = 'Stats per app',
  connector,
  showUploadTab = true,
  refreshInterval = 0, // Default to no auto-refresh
  showActions = true,
}: ConnectorStatisticsProps): JSX.Element => {
  const theme = useTheme();
  const [loading, setLoading] = useState<boolean>(true);
  const [initialLoading, setInitialLoading] = useState<boolean>(true);
  const [connectorStats, setConnectorStats] = useState<ConnectorStatsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  console.log('connecvfvfvftor', connector);
  // Create a ref to track if component is mounted
  const isMounted = useRef<boolean>(true);
  // Create a ref for the interval ID
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  // Function to fetch connector statistics for a single connector id
  const fetchConnectorStats = useCallback(
    async (isManualRefresh = false): Promise<void> => {
      if (!isMounted.current) return;

      try {
        setLoading(true);
        if (isManualRefresh) setRefreshing(true);

        const apiUrl = `/api/v1/knowledgeBase/stats/${connector._key}`;
        const response = await axios.get<ConnectorStatsResponse>(apiUrl);

        const data = response.data?.data || null;
        if (data) {
          // Normalize to array and inject connector name/id for downstream actions
          setConnectorStats(data);
        } else {
          setConnectorStats(null);
        }

        setError(null);
      } catch (err) {
        if (!isMounted.current) return;

        console.error('Error fetching connector statistics:', err);
        setError(err instanceof Error ? err.message : 'Unknown error occurred');
      } finally {
        if (isMounted.current) {
          setLoading(false);
          setInitialLoading(false);

          if (isManualRefresh) {
            setTimeout(() => setRefreshing(false), 500);
          }
        }
      }
    },
    [connector._key]
  );

  // Function to handle manual refresh
  const handleRefresh = () => {
    fetchConnectorStats(true);
  };

  // Set up initial fetch and auto-refresh
  useEffect(() => {
    // Initial fetch
    fetchConnectorStats();

    // Set up auto-refresh if interval is specified
    if (refreshInterval > 0) {
      intervalRef.current = setInterval(() => fetchConnectorStats(), refreshInterval);
    }

    // Cleanup function
    return () => {
      isMounted.current = false;
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [fetchConnectorStats, refreshInterval]);

  // Dark mode aware styles
  const isDark = theme.palette.mode === 'dark';
  const bgPaper = isDark ? '#1F2937' : COLORS.backgrounds.paper;
  const borderColor = isDark ? alpha('#4B5563', 0.6) : COLORS.border;

  // Shared card styles
  const cardStyles = {
    overflow: 'hidden',
    position: 'relative',
    borderRadius: 1,
    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.05)',
    border: '1px solid',
    borderColor,
    bgcolor: bgPaper,
    minHeight: 120,
  };

  // If initial loading and no data yet, show centered spinner
  if (initialLoading && connectorStats === undefined) {
    return (
      <Card sx={cardStyles}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: 120,
            width: '100%',
            py: 3,
          }}
        >
          <CircularProgress size={24} sx={{ color: COLORS.primary }} />
        </Box>
      </Card>
    );
  }

  const renderContent = () => {
    if (error) {
      return (
        <Alert
          severity="error"
          sx={{
            borderRadius: 1,
            border: '1px solid',
            borderColor: alpha(COLORS.status.error, 0.3),
          }}
        >
          <AlertTitle>Error Loading Data</AlertTitle>
          <Typography variant="body2">{error}</Typography>
        </Alert>
      );
    }

    if (connectorStats === undefined && !initialLoading) {
      return (
        <Alert
          severity="info"
          sx={{
            borderRadius: 1,
            border: '1px solid',
            borderColor: alpha(COLORS.primary, 0.2),
          }}
        >
          <AlertTitle>No Records Found</AlertTitle>
          <Typography variant="body2">{`No data found for connector "${connector.name}".`}</Typography>
        </Alert>
      );
    }

    return (
      <Grid container spacing={1.5}>
        {connectorStats && (
          <Grid
            item
            xs={12}
            sm={6}
            md={6}
            lg={4}
            key={`${connector._key}`}
          >
            <ConnectorStatsCard connectorStatsData={connectorStats} connector={connector} showActions={showActions} />
          </Grid>
        )}
      </Grid>
    );
  };

  return (
    <CardContent sx={{ p: { xs: 1, sm: 1.5 } }}>
      {renderContent()}

      {/* Loading Indicator for Refreshes */}
      {loading && !initialLoading && !refreshing && (
        <Box
          sx={{
            py: 2,
            px: 2.5,
            display: 'flex',
            justifyContent: 'center',
            mt: 2,
          }}
        >
          <CircularProgress size={22} sx={{ color: COLORS.primary }} />
        </Box>
      )}
    </CardContent>
  );
};

export default ConnectorStatistics;