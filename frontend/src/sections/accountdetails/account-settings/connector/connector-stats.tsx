import type { IconifyIcon } from '@iconify/react';

import boxIcon from '@iconify-icons/mdi/box';
import jiraIcon from '@iconify-icons/mdi/jira';
import gmailIcon from '@iconify-icons/mdi/gmail';
import slackIcon from '@iconify-icons/mdi/slack';
import dropboxIcon from '@iconify-icons/mdi/dropbox';
import refreshIcon from '@iconify-icons/mdi/refresh';
import databaseIcon from '@iconify-icons/mdi/database';
import googleDriveIcon from '@iconify-icons/mdi/google-drive';
import cloudUploadIcon from '@iconify-icons/mdi/cloud-upload';
import clockOutlineIcon from '@iconify-icons/mdi/clock-outline';
import { useRef, useState, useEffect, useCallback } from 'react';
import progressClockIcon from '@iconify-icons/mdi/progress-clock';
import microsoftTeamsIcon from '@iconify-icons/mdi/microsoft-teams';
import microsoftOutlookIcon from '@iconify-icons/mdi/microsoft-outlook';
import microsoftOnedriveIcon from '@iconify-icons/mdi/microsoft-onedrive';
import fileCancelOutlineIcon from '@iconify-icons/mdi/file-cancel-outline';
import checkCircleOutlineIcon from '@iconify-icons/mdi/check-circle-outline';
import alertCircleOutlineIcon from '@iconify-icons/mdi/alert-circle-outline';
import microsoftSharepointIcon from '@iconify-icons/mdi/microsoft-sharepoint';
import syncIcon from '@iconify-icons/mdi/sync';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Card,
  Grid,
  Paper,
  Alert,
  Avatar,
  Button,
  Tooltip,
  Snackbar,
  Typography,
  AlertTitle,
  CardContent,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

// Enhanced types to match backend response structure
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

interface ConnectorData {
  connector: string; // Connector name (e.g., ONEDRIVE, GMAIL, etc.)
  total: number;
  indexing_status: IndexingStatusStats;
  by_record_type: RecordTypeStats[]; // Record types within this connector
}

interface ConnectorStatsResponse {
  success: boolean;
  data: {
    org_id: string;
    total: BasicStats;
    overall_connector: BasicStats;
    upload: BasicStats;
    by_connector: ConnectorData[];
    by_record_type: any[];
  };
}

interface ConnectorStatisticsProps {
  title?: string;
  connectorNames?: string[] | string; // Can be a single connector name or an array of names
  showUploadTab?: boolean; // Control whether to show the upload tab
  refreshInterval?: number; // Interval in milliseconds for auto-refresh
}

// Connector to app mapping
// This maps backend connector names to frontend display names
const CONNECTOR_DISPLAY_NAMES: Record<string, string> = {
  ONEDRIVE: 'OneDrive',
  DRIVE: 'Google Drive',
  GMAIL: 'Gmail',
  SLACK: 'Slack',
  JIRA: 'Jira',
  TEAMS: 'Microsoft Teams',
  SHAREPOINT: 'SharePoint',
  OUTLOOK: 'Outlook',
  DROPBOX: 'Dropbox',
  BOX: 'Box',
  UPLOAD: 'Uploaded Files',
};

// Connector icon mapping (updated to match backend connector names)
const CONNECTOR_ICONS: Record<string, IconifyIcon> = {
  DRIVE: googleDriveIcon,
  GMAIL: gmailIcon,
  SLACK: slackIcon,
  JIRA: jiraIcon,
  TEAMS: microsoftTeamsIcon,
  ONEDRIVE: microsoftOnedriveIcon,
  SHAREPOINT: microsoftSharepointIcon,
  OUTLOOK: microsoftOutlookIcon,
  DROPBOX: dropboxIcon,
  BOX: boxIcon,
  UPLOAD: cloudUploadIcon,
};

// Connector color mapping
const CONNECTOR_COLORS: Record<string, string> = {
  DRIVE: '#4285F4',
  GMAIL: '#EA4335',
  SLACK: '#4A154B',
  JIRA: '#0052CC',
  TEAMS: '#6264A7',
  ONEDRIVE: '#0078D4',
  SHAREPOINT: '#036C70',
  OUTLOOK: '#0078D4',
  DROPBOX: '#0061FF',
  BOX: '#0061D5',
  UPLOAD: '#34A853',
};

/**
 * Component that displays statistics for a single connector in a square card
 */
const ConnectorCard = ({ connector }: { connector: ConnectorData }): JSX.Element => {
  const theme = useTheme();
  const [isReindexing, setIsReindexing] = useState(false);
  const [isResyncing, setIsResyncing] = useState(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning' | 'info',
  });

  // Extract connector data
  const { connector: connectorName, total, indexing_status } = connector;

  // Get display name, icon, and color
  const displayName = CONNECTOR_DISPLAY_NAMES[connectorName] || connectorName;
  const iconName = CONNECTOR_ICONS[connectorName] || databaseIcon;
  const iconColor = CONNECTOR_COLORS[connectorName] || theme.palette.primary.main;

  // Calculate percentage completed
  const percentComplete =
    total > 0 ? Math.round(((indexing_status.COMPLETED || 0) / total) * 100) : 0;

  // Function to handle reindexing of failed documents
  const handleReindex = async () => {
    try {
      setIsReindexing(true);

      // API call to reindex failed documents for this connector
      await axios.post('/api/v1/knowledgeBase/reindex-all/connector', {
        app: connectorName,
      });

      setSnackbar({
        open: true,
        message: `Reindexing started for ${displayName}`,
        severity: 'success',
      });
    } catch (error) {
      console.error('Failed to reindex documents:', error);
      setSnackbar({
        open: true,
        message: `Failed to reindex documents for ${displayName}`,
        severity: 'error',
      });
    } finally {
      setTimeout(() => {
        setIsReindexing(false);
      }, 1000);
    }
  };

  const handleResync = async () => {
    try {
      setIsResyncing(true);

      // API call to resync connector
      await axios.post('/api/v1/knowledgeBase/resync/connector', {
        connectorName,
      });

      setSnackbar({
        open: true,
        message: `Resync started for ${displayName}`,
        severity: 'success',
      });
    } catch (error) {
      console.error('Failed to resync connector:', error);
      setSnackbar({
        open: true,
        message: `Failed to resync ${displayName}`,
        severity: 'error',
      });
    } finally {
      setTimeout(() => {
        setIsResyncing(false);
      }, 1000);
    }
  };

  const handleCloseSnackbar = (): void => {
    setSnackbar({ open: false, message: '', severity: 'success' });
  };

  // Create status counts array for uniform display
  const statusItems = [
    {
      label: 'Indexed',
      count: indexing_status.COMPLETED || 0,
      icon: checkCircleOutlineIcon,
      color: theme.palette.success.main,
      tooltip: 'Indexed Records',
    },
    {
      label: 'Failed',
      count: indexing_status.FAILED || 0,
      icon: alertCircleOutlineIcon,
      color: theme.palette.error.main,
      tooltip: 'Failed Records',
    },
    {
      label: 'In Progress',
      count: indexing_status.IN_PROGRESS || 0,
      icon: progressClockIcon,
      color: theme.palette.warning.main,
      tooltip: 'In Progress Records',
    },
    {
      label: 'Not Started',
      count: indexing_status.NOT_STARTED || 0,
      icon: clockOutlineIcon,
      color: theme.palette.grey[500],
      tooltip: 'Not Started Records',
    },
  ];

  // Add optional status items only if count > 0
  if (indexing_status.FILE_TYPE_NOT_SUPPORTED > 0) {
    statusItems.push({
      label: 'Unsupported',
      count: indexing_status.FILE_TYPE_NOT_SUPPORTED,
      icon: fileCancelOutlineIcon,
      color: theme.palette.info.main,
      tooltip: 'Unsupported File Types',
    });
  }

  if (indexing_status.AUTO_INDEX_OFF > 0) {
    statusItems.push({
      label: 'Manual Sync',
      count: indexing_status.AUTO_INDEX_OFF,
      icon: fileCancelOutlineIcon,
      color: theme.palette.grey[600],
      tooltip: 'Auto Index Off',
    });
  }

  // Determine if we should show the reindex button (only when failed docs exist)
  const showReindexButton = indexing_status.FAILED > 0;

  return (
    <>
      <Paper
        elevation={0}
        sx={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 2,
          overflow: 'hidden',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.04)',
          border: '1px solid',
          borderColor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.divider, 0.6)
              : alpha(theme.palette.grey[200], 0.8),
          transition: 'box-shadow 0.2s ease',
          '&:hover': {
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.06)',
          },
        }}
      >
        {/* Connector Icon */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            py: 2,
            bgcolor: alpha(iconColor, 0.06),
          }}
        >
          <Avatar
            sx={{
              width: 48,
              height: 48,
              bgcolor: alpha(iconColor, 0.14),
              color: iconColor,
              borderRadius: '20%',
            }}
          >
            <Iconify icon={iconName} width={26} height={26} />
          </Avatar>
        </Box>

        {/* Stats */}
        <Box
          sx={{
            p: 2,
            pt: 1.5,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            flex: 1,
          }}
        >
          {/* Connector Name and Total */}
          <Typography variant="subtitle1" sx={{ mb: 0.25, fontWeight: 600, textAlign: 'center' }}>
            {displayName}
          </Typography>

          <Typography
            variant="caption"
            sx={{
              mb: 1.5,
              color: theme.palette.text.secondary,
              fontWeight: 500,
            }}
          >
            {total.toLocaleString()} records
          </Typography>

          {/* Progress Circle */}
          <Box
            sx={{
              position: 'relative',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              mb: 2,
            }}
          >
            <CircularProgress
              variant="determinate"
              value={percentComplete}
              size={60}
              thickness={4}
              sx={{
                color: iconColor,
                '& .MuiCircularProgress-circle': {
                  strokeLinecap: 'round',
                },
              }}
            />
            <Box
              sx={{
                position: 'absolute',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Typography variant="body2" fontWeight="bold" color={iconColor}>
                {`${percentComplete}%`}
              </Typography>
            </Box>
          </Box>

          {/* Stats Grid - Rendered dynamically for uniformity */}
          <Grid container spacing={1.5} sx={{ mb: 2 }}>
            {statusItems.map((item, index) => (
              <Grid item xs={statusItems.length <= 4 ? 6 : 4} key={`status-${index}`}>
                <Tooltip title={item.tooltip}>
                  <Box
                    sx={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      backgroundColor: alpha(item.color, 0.05),
                      borderRadius: 1,
                      py: 1,
                    }}
                  >
                    <Iconify
                      icon={item.icon}
                      width={18}
                      height={18}
                      sx={{ color: item.color, mb: 0.5 }}
                    />
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {item.count.toLocaleString()}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {item.label}
                    </Typography>
                  </Box>
                </Tooltip>
              </Grid>
            ))}
          </Grid>

          {/* Action Buttons - Adaptive layout based on button count */}
          <Box
            sx={{
              mt: 'auto',
              display: 'flex',
              justifyContent: showReindexButton ? 'space-between' : 'center',
              width: '100%',
              gap: 1.5,
            }}
          >
            {/* Resync button */}
            <Button
              size="small"
              color="primary"
              variant="outlined"
              startIcon={<Iconify icon={syncIcon} />}
              onClick={handleResync}
              disabled={isResyncing}
              fullWidth={showReindexButton}
              sx={{
                borderRadius: '8px',
                textTransform: 'none',
                minHeight: '36px',
                fontSize: '0.8125rem',
                fontWeight: 500,
                border: `1px solid ${alpha(theme.palette.primary.main, 0.5)}`,
                '&:hover': {
                  borderColor: theme.palette.primary.main,
                  backgroundColor: alpha(theme.palette.primary.main, 0.04),
                },
                '&:disabled': {
                  color: alpha(theme.palette.primary.main, 0.4),
                  borderColor: alpha(theme.palette.primary.main, 0.2),
                },
                maxWidth: showReindexButton ? '48%' : '180px',
              }}
            >
              {isResyncing ? (
                <>
                  <CircularProgress size={16} color="primary" sx={{ mr: 1 }} />
                  Syncing...
                </>
              ) : (
                'Resync'
              )}
            </Button>

            {/* Reindex button - Only show if there are failed records */}
            {showReindexButton && (
              <Button
                size="small"
                color="error"
                variant="outlined"
                startIcon={<Iconify icon={refreshIcon} />}
                onClick={handleReindex}
                disabled={isReindexing}
                fullWidth
                sx={{
                  borderRadius: '8px',
                  textTransform: 'none',
                  minHeight: '36px',
                  fontSize: '0.8125rem',
                  fontWeight: 500,
                  border: `1px solid ${alpha(theme.palette.error.main, 0.5)}`,
                  '&:hover': {
                    borderColor: theme.palette.error.main,
                    backgroundColor: alpha(theme.palette.error.main, 0.04),
                  },
                  '&:disabled': {
                    color: alpha(theme.palette.error.main, 0.4),
                    borderColor: alpha(theme.palette.error.main, 0.2),
                  },
                  maxWidth: '48%',
                }}
              >
                {isReindexing ? (
                  <>
                    <CircularProgress size={16} color="error" sx={{ mr: 1 }} />
                    Indexing...
                  </>
                ) : (
                  'Reindex Failed'
                )}
              </Button>
            )}
          </Box>
        </Box>
      </Paper>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{
            width: '100%',
            borderRadius: 0.75,
            boxShadow: theme.shadows[3],
            '& .MuiAlert-icon': {
              fontSize: '1.2rem',
            },
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </>
  );
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
}: ConnectorStatisticsProps): JSX.Element => {
  const theme = useTheme();
  const [loading, setLoading] = useState<boolean>(true);
  const [initialLoading, setInitialLoading] = useState<boolean>(true);
  const [connectorStats, setConnectorStats] = useState<ConnectorData[]>([]);
  const [uploadStats, setUploadStats] = useState<ConnectorData | null>(null);
  const [overallStats, setOverallStats] = useState<BasicStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState<boolean>(false);

  // Create a ref to track if component is mounted
  const isMounted = useRef(true);

  // Create a ref for the interval ID
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  // Convert connectorNames to array if it's a string
  const normalizedConnectorNames = Array.isArray(connectorNames)
    ? connectorNames
    : connectorNames
      ? [connectorNames]
      : [];

  // Function to fetch connector statistics
  const fetchConnectorStats = useCallback(async (isManualRefresh = false): Promise<void> => {
    if (!isMounted.current) return;

    try {
      setLoading(true);
      if (isManualRefresh) setRefreshing(true);

      // Fetch API data without filters since we're doing client-side filtering
      const apiUrl = '/api/v1/knowledgeBase/stats/connector';

      const response = await axios.get<ConnectorStatsResponse>(apiUrl);

      if (!isMounted.current) return;

      if (!response.data.success) {
        throw new Error('Failed to fetch connector statistics');
      }

      const { data } = response.data;

      // Set overall stats
      setOverallStats(data.total);

      // Get all connectors from the response
      const availableConnectors = data.by_connector || [];

      // Filter connectors based on the connectorNames prop
      // If connectorNames is empty, show all available connectors
      let filteredConnectors: ConnectorData[] = [];

      if (normalizedConnectorNames.length > 0) {
        // Convert names to uppercase for comparison
        const upperCaseNames = normalizedConnectorNames.map((name) => name.toUpperCase());

        // Filter only connectors that match the provided names
        filteredConnectors = availableConnectors.filter((connector) =>
          upperCaseNames.includes(connector.connector.toUpperCase())
        );
      } else {
        // If no names provided, show all available connectors
        filteredConnectors = availableConnectors;
      }

      // Sort connectors by total records
      const sortedConnectors = [...filteredConnectors].sort((a, b) => b.total - a.total);
      setConnectorStats(sortedConnectors);

      // Handle upload stats if enabled
      if (showUploadTab && data.upload) {
        // Check if upload should be included based on connectorNames
        const includeUpload =
          normalizedConnectorNames.length === 0 ||
          normalizedConnectorNames.some((name) => name.toUpperCase() === 'UPLOAD');

        if (includeUpload && data.upload.total > 0) {
          const uploadConnector: ConnectorData = {
            connector: 'UPLOAD',
            total: data.upload.total,
            indexing_status: data.upload.indexing_status,
            by_record_type: [], // Upload stats may not have record type breakdown
          };
          setUploadStats(uploadConnector);
        } else {
          setUploadStats(null);
        }
      } else {
        setUploadStats(null);
      }
    } catch (err) {
      if (!isMounted.current) return;

      console.error('Error fetching connector statistics:', err);
      setError(err instanceof Error ? err.message : 'Unknown error occurred');

      // For development: Create mock data (keeping existing mock data logic)
      if (normalizedConnectorNames.length > 0) {
        // Create mock data for specified connectors
        const mockConnectors = normalizedConnectorNames.map((name, index) => ({
          connector: name.toUpperCase(),
          total: 100 * (index + 1),
          indexing_status: {
            NOT_STARTED: 10,
            IN_PROGRESS: 10,
            COMPLETED: 70,
            FAILED: 10,
            FILE_TYPE_NOT_SUPPORTED: index === 0 ? 5 : 0, // Add some unsupported files to first connector for testing
            AUTO_INDEX_OFF: index === 1 ? 3 : 0,
          },
          by_record_type: [],
        }));

        setConnectorStats(mockConnectors);

        // Set mock overall stats based on the mock connectors
        const totalRecords = mockConnectors.reduce((sum, c) => sum + c.total, 0);
        setOverallStats({
          total: totalRecords,
          indexing_status: {
            NOT_STARTED: Math.round(totalRecords * 0.1),
            IN_PROGRESS: Math.round(totalRecords * 0.1),
            COMPLETED: Math.round(totalRecords * 0.7),
            FAILED: Math.round(totalRecords * 0.1),
            FILE_TYPE_NOT_SUPPORTED: 5, // Add some unsupported files for testing
            AUTO_INDEX_OFF: 3, 
          },
        });
      } else {
        // Default mock data for upload only
        const mockUploadStats: ConnectorData = {
          connector: 'UPLOAD',
          total: 3,
          indexing_status: {
            NOT_STARTED: 0,
            IN_PROGRESS: 0,
            COMPLETED: 3,
            FAILED: 0,
            FILE_TYPE_NOT_SUPPORTED: 1, // Add an unsupported file for testing
            AUTO_INDEX_OFF: 0, 
          },
          by_record_type: [],
        };

        if (showUploadTab) {
          setUploadStats(mockUploadStats);
        }

        setConnectorStats([]);
        setOverallStats({
          total: 3,
          indexing_status: {
            NOT_STARTED: 0,
            IN_PROGRESS: 0,
            COMPLETED: 3,
            FAILED: 0,
            FILE_TYPE_NOT_SUPPORTED: 1,
            AUTO_INDEX_OFF: 0,
          },
        });
      }
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
    }
    // eslint-disable-next-line
  }, []);

  // Function to handle manual refresh
  const handleRefresh = () => {
    fetchConnectorStats(true);
  };

  // Set up initial fetch and auto-refresh
  useEffect(() => {
    // Make sure isMounted is true at the start
    isMounted.current = true;

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
    };
  }, [fetchConnectorStats, refreshInterval]);

  // Prepare all stats to display (connectors + upload if enabled)
  const allStats = [...connectorStats];
  if (showUploadTab && uploadStats) {
    allStats.push(uploadStats);
  }

  // If initial loading and no data yet, show centered spinner
  if (initialLoading && !allStats.length) {
    return (
      <Card
        sx={{
          overflow: 'hidden',
          position: 'relative',
          borderRadius: 2,
          boxShadow: theme.shadows[1],
          border: '1px solid',
          borderColor: 'divider',
          minHeight: 200,
          mt: 4,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: 200,
            width: '100%',
            py: 4,
          }}
        >
          <CircularProgress size={36} />
          {/* <Typography>
            Loading indexing statics
          </Typography> */}
        </Box>
      </Card>
    );
  }

  return (
    <CardContent>
      {/* Card Header with Title and Refresh Button */}
      {/* <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          mb: 3,
          pb: 2,
          borderBottom: '1px solid',
          borderColor: alpha(theme.palette.divider, 0.1),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <Typography variant="h6" sx={{ fontWeight: 500 }}>
            {title}
          </Typography>
        </Box>

        <Tooltip title="Refresh statistics" arrow placement="left">
          <Box>
            <IconButton
              size="small"
              onClick={handleRefresh}
              disabled={refreshing}
              sx={{
                color: theme.palette.primary.main,
                backgroundColor: alpha(theme.palette.primary.main, 0.04),
                width: 36,
                height: 36,
                border: `1px solid ${alpha(theme.palette.primary.main, 0.12)}`,
                '&:hover': {
                  backgroundColor: alpha(theme.palette.primary.main, 0.08),
                },
                transition: 'all 0.2s ease',
              }}
            >
              {refreshing ? (
                <CircularProgress size={18} color="inherit" />
              ) : (
                <Iconify icon={refreshIcon} width={20} height={20} />
              )}
            </IconButton>
          </Box>
        </Tooltip>
      </Box> */}

      {/* Grid of Connector Cards */}
      {error ? (
        <Alert
          severity="error"
          sx={{
            borderRadius: 2,
          }}
        >
          <AlertTitle>Error Loading Data</AlertTitle>
          <Typography variant="body2">{error}</Typography>
        </Alert>
      ) : allStats.length === 0 ? (
        <Alert
          severity="info"
          sx={{
            borderRadius: 2,
          }}
        >
          <AlertTitle>No records of Google Workspace indexed yet</AlertTitle>
          <Typography variant="body2">
            {normalizedConnectorNames.length > 0
              ? `No data found for the specified connector${normalizedConnectorNames.length > 1 ? 's' : ''}: ${normalizedConnectorNames.join(', ')}`
              : 'No connectors connected. Add a connector or upload files to get started.'}
          </Typography>
        </Alert>
      ) : (
        <Grid container spacing={2}>
          {allStats.map((stat, index) => (
            <Grid item xs={12} sm={6} md={4} lg={4} key={`${stat.connector}-${index}`}>
              <ConnectorCard connector={stat} />
            </Grid>
          ))}
        </Grid>
      )}

      {/* Loading Indicator for Refreshes */}
      {loading && !initialLoading && !refreshing && (
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            mt: 2,
          }}
        >
          <CircularProgress size={24} />
        </Box>
      )}
    </CardContent>
  );
};

export default ConnectorStatistics;
