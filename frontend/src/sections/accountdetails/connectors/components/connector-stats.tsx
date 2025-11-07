import { useRef, useState, useEffect, useCallback, useMemo } from 'react';

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

interface IndexingStatusStats {
  NOT_STARTED: number;
  IN_PROGRESS: number;
  COMPLETED: number;
  FAILED: number;
  FILE_TYPE_NOT_SUPPORTED: number;
  AUTO_INDEX_OFF: number;
}

interface BasicStats {
  total: number;
  indexing_status: IndexingStatusStats;
}

interface RecordTypeStats {
  record_type: string;
  total: number;
  indexing_status: IndexingStatusStats;
}

// For individual Knowledge Base details
interface KnowledgeBaseStats {
  kb_id: string;
  kb_name: string;
  total: number;
  indexing_status: IndexingStatusStats;
  by_record_type: RecordTypeStats[];
}

// Main connector stats data structure
interface ConnectorStatsData {
  org_id: string;
  connector: string; // "KNOWLEDGE_BASE" or specific connector name like "GOOGLE_DRIVE"
  origin: 'UPLOAD' | 'CONNECTOR';
  stats: BasicStats;
  by_record_type: RecordTypeStats[];
  knowledge_bases?: KnowledgeBaseStats[]; // Only present for Knowledge Base queries
}

interface ConnectorStatsResponse {
  success: boolean;
  message?: string; // Present when success is false
  data: ConnectorStatsData | null;
}

interface ConnectorStatisticsProps {
  title?: string;
  connectorNames?: string[] | string; // Can be a single connector name or an array of names
  showUploadTab?: boolean; // Control whether to show the upload tab
  refreshInterval?: number; // Interval in milliseconds for auto-refresh
  showActions?: boolean; // Whether to show action buttons in cards
}

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

/**
 * ConnectorStatistics Component
 * Displays performance statistics for each connector in a grid layout
 */
const ConnectorStatistics = ({
  title = 'Stats per app',
  connectorNames = [],
  showUploadTab = true,
  refreshInterval = 0, // Default to no auto-refresh
  showActions = true,
}: ConnectorStatisticsProps): JSX.Element => {
  const theme = useTheme();
  const [loading, setLoading] = useState<boolean>(true);
  const [initialLoading, setInitialLoading] = useState<boolean>(true);
  const [connectorStats, setConnectorStats] = useState<ConnectorStatsData[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState<boolean>(false);

  // Create a ref to track if component is mounted
  const isMounted = useRef<boolean>(true);

  // Create a ref for the interval ID
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  // Track fetch state to prevent duplicate calls
  const fetchInProgressRef = useRef(false);
  const lastFetchedKeyRef = useRef<string | null>(null);

  // Normalize connector names with stable identity across renders
  const normalizedUpperNames = useMemo(() => {
    const list = Array.isArray(connectorNames)
      ? connectorNames
      : connectorNames
        ? [connectorNames]
        : [];
    return list
      .map((n) => String(n).trim())
      .filter((n) => n.length > 0)
      .map((n) => n);
  }, [connectorNames]);

  // Stable key built from content, not reference
  const namesKey = useMemo(
    () => Array.from(new Set(normalizedUpperNames)).sort().join(','),
    [normalizedUpperNames]
  );

  // Function to fetch connector statistics - Updated for new API structure
  const fetchConnectorStats = useCallback(
    async (isManualRefresh = false): Promise<void> => {
      if (!isMounted.current) return;

      // Prevent duplicate calls (unless manual refresh)
      if (!isManualRefresh) {
        if (fetchInProgressRef.current && lastFetchedKeyRef.current === namesKey) {
          return;
        }
        // Skip if already fetched for this key
        if (lastFetchedKeyRef.current === namesKey && !fetchInProgressRef.current) {
          return;
        }
      }

      fetchInProgressRef.current = true;
      lastFetchedKeyRef.current = namesKey;

      try {
        setLoading(true);
        if (isManualRefresh) setRefreshing(true);

        // Get list of connectors to fetch
        // Build the list of connectors to fetch. Only include KB by default
        // when no connectors are specified and showUploadTab is true.
        const connectorsToFetch = namesKey
          ? namesKey.split(',').filter((n) => n.length > 0)
          : showUploadTab
            ? ['KNOWLEDGE_BASE']
            : [];

        const responses = await Promise.all(
          connectorsToFetch.map(async (name) => {
            try {
              const key = name;
              const apiUrl =
                key === 'KNOWLEDGE_BASE' || key === 'UPLOAD'
                  ? '/api/v1/knowledgeBase/stats/KB'
                  : `/api/v1/knowledgeBase/stats/${key}`;
              const response = await axios.get<ConnectorStatsResponse>(apiUrl);
              if (!isMounted.current) return null;
              return response.data.success && response.data.data ? response.data.data : null;
            } catch (connectorError) {
              console.error(`Failed to fetch stats for ${name}:`, connectorError);
              return null;
            }
          })
        );

        const fetchedStats: ConnectorStatsData[] = responses.filter((r): r is ConnectorStatsData =>
          Boolean(r)
        );

        // Sort connectors by total records (descending)
        const sortedConnectors = fetchedStats.sort((a, b) => b.stats.total - a.stats.total);
        setConnectorStats(sortedConnectors);
        setError(null);
      } catch (err) {
        if (!isMounted.current) return;

        console.error('Error fetching connector statistics:', err);
        setError(err instanceof Error ? err.message : 'Unknown error occurred');

        // For development: Create mock data (keeping existing mock data logic)
        const connectorsToMock = namesKey
          ? namesKey.split(',').filter((n) => n.length > 0)
          : showUploadTab
            ? ['KNOWLEDGE_BASE']
            : [];

        const mockConnectors: ConnectorStatsData[] = connectorsToMock.map((name, index) => ({
          org_id: 'mock-org',
          connector: name.toUpperCase(),
          origin: name.toUpperCase() === 'KNOWLEDGE_BASE' ? 'UPLOAD' : 'CONNECTOR',
          stats: {
            total: 100 * (index + 1),
            indexing_status: {
              NOT_STARTED: 10,
              IN_PROGRESS: 10,
              COMPLETED: 70,
              FAILED: 10,
              FILE_TYPE_NOT_SUPPORTED: index === 0 ? 5 : 0,
              AUTO_INDEX_OFF: index === 1 ? 3 : 0,
            },
          },
          by_record_type: [],
          ...(name.toUpperCase() === 'KNOWLEDGE_BASE' && {
            knowledge_bases: [
              {
                kb_id: 'kb1',
                kb_name: 'Test Knowledge Base',
                total: 50,
                indexing_status: {
                  NOT_STARTED: 5,
                  IN_PROGRESS: 5,
                  COMPLETED: 35,
                  FAILED: 5,
                  FILE_TYPE_NOT_SUPPORTED: 0,
                  AUTO_INDEX_OFF: 0,
                },
                by_record_type: [],
              },
            ],
          }),
        }));

        setConnectorStats(mockConnectors);
      } finally {
        if (isMounted.current) {
          setLoading(false);
          setInitialLoading(false);
          if (isManualRefresh) {
            setTimeout(() => {
              setRefreshing(false);
            }, 500);
          }
        }
        fetchInProgressRef.current = false;
      }
    },
    [namesKey, showUploadTab]
  );

  // Function to handle manual refresh
  const handleRefresh = (): void => {
    fetchConnectorStats(true);
  };

  // Set up initial fetch and auto-refresh
  useEffect(() => {
    // Make sure isMounted is true at the start
    isMounted.current = true;

    // Reset fetch flag when key changes
    if (lastFetchedKeyRef.current !== namesKey) {
      fetchInProgressRef.current = false;
      lastFetchedKeyRef.current = null;
    }

    // Perform initial fetch
    fetchConnectorStats();

    // Set up auto-refresh if interval is specified
    if (refreshInterval > 0) {
      intervalRef.current = setInterval(() => fetchConnectorStats(), refreshInterval);
    }

    // Cleanup function to clear interval and prevent state updates after unmount
    return () => {
      isMounted.current = false;
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      fetchInProgressRef.current = false;
    };
  }, [fetchConnectorStats, refreshInterval, namesKey]);

  // Dark mode aware styles
  const isDark = theme.palette.mode === 'dark';
  const bgPaper = isDark ? '#1F2937' : COLORS.backgrounds.paper;
  const borderColor = isDark ? alpha('#4B5563', 0.6) : COLORS.border;

  // If initial loading and no data yet, show centered spinner
  if (initialLoading && !connectorStats.length) {
    return (
      <Card
        sx={{
          overflow: 'hidden',
          position: 'relative',
          borderRadius: 1,
          boxShadow: '0 1px 2px rgba(0, 0, 0, 0.05)',
          border: '1px solid',
          borderColor,
          bgcolor: bgPaper,
          minHeight: 120,
        }}
      >
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

  return (
    <CardContent sx={{ p: { xs: 1, sm: 1.5 } }}>
      {/* Grid of Connector Cards */}
      {error ? (
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
      ) : connectorStats.length === 0 ? (
        <Alert
          severity="info"
          sx={{
            borderRadius: 1,
            border: '1px solid',
            borderColor: alpha(COLORS.primary, 0.2),
          }}
        >
          <AlertTitle>No Records Found</AlertTitle>
          <Typography variant="body2">
            {namesKey
              ? `No data found for the specified connector${namesKey.includes(',') ? 's' : ''}: ${namesKey}`
              : 'No connectors connected. Add a connector or upload files to get started.'}
          </Typography>
        </Alert>
      ) : (
        <Grid container spacing={1.5}>
          {connectorStats.map((stat, index) => (
            <Grid item xs={12} sm={6} md={6} lg={4} key={`${stat.connector}-${index}`}>
              <ConnectorStatsCard connector={stat} showActions={showActions} />
            </Grid>
          ))}
        </Grid>
      )}

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
