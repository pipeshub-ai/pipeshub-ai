import { useState, useEffect, useRef, useCallback } from 'react';
import { alpha, useTheme } from '@mui/material/styles';
import axios from 'src/utils/axios';
import {
  Box,
  Card,
  Grid,
  Paper,
  Avatar,
  Typography,
  CardContent,
  CircularProgress,
  Tooltip,
  Alert,
  AlertTitle,
  Button,
  Snackbar,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import googleDriveIcon from '@iconify-icons/mdi/google-drive';
import gmailIcon from '@iconify-icons/mdi/gmail';
import slackIcon from '@iconify-icons/mdi/slack';
import jiraIcon from '@iconify-icons/mdi/jira';
import microsoftTeamsIcon from '@iconify-icons/mdi/microsoft-teams';
import microsoftOnedriveIcon from '@iconify-icons/mdi/microsoft-onedrive';
import microsoftSharepointIcon from '@iconify-icons/mdi/microsoft-sharepoint';
import microsoftOutlookIcon from '@iconify-icons/mdi/microsoft-outlook';
import dropboxIcon from '@iconify-icons/mdi/dropbox';
import boxIcon from '@iconify-icons/mdi/box';
import cloudUploadIcon from '@iconify-icons/mdi/cloud-upload';
import fileDocumentIcon from '@iconify-icons/mdi/file-document';
import checkCircleOutlineIcon from '@iconify-icons/mdi/check-circle-outline';
import alertCircleOutlineIcon from '@iconify-icons/mdi/alert-circle-outline';
import progressClockIcon from '@iconify-icons/mdi/progress-clock';
import clockOutlineIcon from '@iconify-icons/mdi/clock-outline';
import fileCancelOutlineIcon from '@iconify-icons/mdi/file-cancel-outline';
import refreshIcon from '@iconify-icons/mdi/refresh';
import databaseIcon from '@iconify-icons/mdi/database';
import { IconifyIcon } from '@iconify/react';

// Enhanced types to match backend response structure
interface IndexingStatusStats {
  NOT_STARTED: number;
  IN_PROGRESS: number;
  COMPLETED: number;
  FAILED: number;
  FILE_TYPE_NOT_SUPPORTED: number;
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
    if (indexing_status.FAILED === 0) return;

    try {
      setIsReindexing(true);

      // API call to reindex failed documents for this connector
      await axios.post('/api/v1/knowledgeBase/reindex-all/connector', {
        app: connectorName,
        // status: 'FAILED',
      });
      setSnackbar({
        open: true,
        message: `Started reindexing for  ${connectorName}`,
        severity: 'success',
      });

      // Success notification could be added here
    } catch (error) {
      console.error('Failed to reindex documents:', error);
      // Error notification could be added her
    } finally {
      // In a real app, you might want to refresh the stats after a delay
      setTimeout(() => {
        setIsReindexing(false);
      }, 1000);
    }
  };

  const handleCloseSnackbar = (): void => {
    setSnackbar({ open: false, message: '', severity: 'success' });
  };

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
              width: 44,
              height: 44,
              bgcolor: alpha(iconColor, 0.14),
              color: iconColor,
              borderRadius: '20%',
            }}
          >
            <Iconify icon={iconName} width={24} height={24} />
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
              mb: 1,
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
              mb: 1,
            }}
          >
            <CircularProgress
              variant="determinate"
              value={percentComplete}
              size={54}
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

          {/* Stats Grid */}
          <Grid container spacing={1} sx={{ mb: 1 }}>
            {/* Indexed Records */}
            <Grid item xs={6}>
              <Tooltip title="Indexed Records">
                <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <Iconify
                    icon={checkCircleOutlineIcon}
                    width={18}
                    height={18}
                    sx={{ color: theme.palette.success.main, mb: 0.5 }}
                  />
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {indexing_status.COMPLETED?.toLocaleString() || 0}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Indexed
                  </Typography>
                </Box>
              </Tooltip>
            </Grid>

            {/* Failed Records */}
            <Grid item xs={6}>
              <Tooltip title="Failed Records">
                <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <Iconify
                    icon={alertCircleOutlineIcon}
                    width={18}
                    height={18}
                    sx={{ color: theme.palette.error.main, mb: 0.5 }}
                  />
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {indexing_status.FAILED?.toLocaleString() || 0}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Failed
                  </Typography>
                </Box>
              </Tooltip>
            </Grid>

            {/* In Progress Records */}
            <Grid item xs={6}>
              <Tooltip title="In Progress Records">
                <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <Iconify
                    icon={progressClockIcon}
                    width={18}
                    height={18}
                    sx={{ color: theme.palette.warning.main, mb: 0.5 }}
                  />
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {indexing_status.IN_PROGRESS?.toLocaleString() || 0}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    In Progress
                  </Typography>
                </Box>
              </Tooltip>
            </Grid>

            {/* Not Started Records */}
            <Grid item xs={6}>
              <Tooltip title="Not Started Records">
                <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <Iconify
                    icon={clockOutlineIcon}
                    width={18}
                    height={18}
                    sx={{ color: theme.palette.grey[500], mb: 0.5 }}
                  />
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {indexing_status.NOT_STARTED?.toLocaleString() || 0}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Not Started
                  </Typography>
                </Box>
              </Tooltip>
            </Grid>

            {/* Unsupported File Types - only show if value is greater than 0 */}
            {indexing_status.FILE_TYPE_NOT_SUPPORTED > 0 && (
              <Grid item xs={12} sx={{ mt: 0.5 }}>
                <Tooltip title="Unsupported File Types">
                  <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                    <Iconify
                      icon={fileCancelOutlineIcon}
                      width={18}
                      height={18}
                      sx={{ color: theme.palette.info.main, mb: 0.5 }}
                    />
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {indexing_status.FILE_TYPE_NOT_SUPPORTED?.toLocaleString() || 0}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Unsupported Files
                    </Typography>
                  </Box>
                </Tooltip>
              </Grid>
            )}
          </Grid>

          {/* Action Buttons */}
          {/* <Box sx={{ mt: 0.5, display: 'flex', justifyContent: 'center', width: '100%' }}> */}
          {/* Reindex button for failed documents */}
          {/* {indexing_status.FAILED > 0 && (
              <Button
                size="small"
                color="error"
                variant="outlined"
                startIcon={<Iconify icon={refreshIcon} />}
                onClick={handleReindex}
                disabled={isReindexing}
                sx={{
                  borderRadius: '8px',
                  textTransform: 'none',
                  minHeight: '32px',
                  fontSize: '0.75rem',
                  border: `1px solid ${alpha(theme.palette.error.main, 0.5)}`,
                  '&:hover': {
                    borderColor: theme.palette.error.main,
                    backgroundColor: alpha(theme.palette.error.main, 0.04),
                  },
                }}
              >
                {isReindexing ? (
                  <>
                    <CircularProgress size={16} color="error" sx={{ mr: 1 }} />
                    Reindexing...
                  </>
                ) : (
                  'Reindex failed'
                )}
              </Button>
            )} */}
          {/* </Box> */}
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

      const data = response.data.data;

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
          <AlertTitle>No Connectors Available</AlertTitle>
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
