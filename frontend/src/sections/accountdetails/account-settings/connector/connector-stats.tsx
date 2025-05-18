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

type SnackbarSeverity = 'success' | 'error' | 'warning' | 'info';

/**
 * Component that displays statistics for a single connector in a square card
 */
const ConnectorCard = ({ connector }: { connector: ConnectorData }): JSX.Element => {
  const theme = useTheme();
  const [isReindexing, setIsReindexing] = useState<boolean>(false);
  const [isResyncing, setIsResyncing] = useState<boolean>(false);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: SnackbarSeverity;
  }>({
    open: false,
    message: '',
    severity: 'success',
  });

  // Extract connector data
  const { connector: connectorName, total, indexing_status } = connector;

  // Get display name and icon
  const displayName = CONNECTOR_DISPLAY_NAMES[connectorName] || connectorName;
  const iconName = CONNECTOR_ICONS[connectorName] || databaseIcon;

  // Calculate percentage completed
  const percentComplete =
    total > 0 ? Math.round(((indexing_status.COMPLETED || 0) / total) * 100) : 0;

  // Calculate if status is complete
  const isComplete = percentComplete === 100;

  // Function to handle reindexing of failed documents
  const handleReindex = async (): Promise<void> => {
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

  const handleResync = async (): Promise<void> => {
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
      tooltip: 'Indexed Records',
      key: 'completed',
    },
    {
      label: 'Failed',
      count: indexing_status.FAILED || 0,
      icon: alertCircleOutlineIcon,
      tooltip: 'Failed Records',
      key: 'failed',
    },
    {
      label: 'In Progress',
      count: indexing_status.IN_PROGRESS || 0,
      icon: progressClockIcon,
      tooltip: 'In Progress Records',
      key: 'inProgress',
    },
    {
      label: 'Not Started',
      count: indexing_status.NOT_STARTED || 0,
      icon: clockOutlineIcon,
      tooltip: 'Not Started Records',
      key: 'notStarted',
    },
  ];

  // Add optional status items only if count > 0
  if (indexing_status.FILE_TYPE_NOT_SUPPORTED > 0) {
    statusItems.push({
      label: 'Unsupported',
      count: indexing_status.FILE_TYPE_NOT_SUPPORTED,
      icon: fileCancelOutlineIcon,
      tooltip: 'Unsupported File Types',
      key: 'unsupported',
    });
  }

  if (indexing_status.AUTO_INDEX_OFF > 0) {
    statusItems.push({
      label: 'Manual Sync',
      count: indexing_status.AUTO_INDEX_OFF,
      icon: fileCancelOutlineIcon,
      tooltip: 'Auto Index Off',
      key: 'autoIndexOff',
    });
  }

  // Determine if we should show the reindex button (only when failed docs exist)
  const showReindexButton = indexing_status.FAILED > 0;

  // Premium SaaS design system
  const isDark = theme.palette.mode === 'dark';

  // Base colors - more refined with subtle variations
  const white = '#ffffff';

  // Grayscale palette inspired by Vercel/Linear/Stripe design systems
  const gray = {
    50: isDark ? '#121212' : '#fafafa',
    100: isDark ? '#1e1e1e' : '#f5f5f5',
    150: isDark ? '#262626' : '#f0f0f0',
    200: isDark ? '#2c2c2c' : '#e9e9e9',
    300: isDark ? '#383838' : '#e2e2e2',
    400: isDark ? '#525252' : '#c2c2c2',
    500: isDark ? '#6e6e6e' : '#8f8f8f',
    600: isDark ? '#909090' : '#6e6e6e',
    700: isDark ? '#b3b3b3' : '#525252',
    800: isDark ? '#d6d6d6' : '#383838',
    900: isDark ? '#eeeeee' : '#2c2c2c',
  };

  // UI Specific colors
  const bgCard = isDark ? gray[50] : white;
  const bgHeader = isDark ? gray[100] : gray[50];
  const bgHover = isDark ? gray[100] : gray[100];
  const bgStats = isDark ? gray[100] : gray[50];
  const bgStatHover = isDark ? gray[150] : gray[100];
  const borderColor = isDark ? `rgba(255, 255, 255, 0.08)` : `rgba(0, 0, 0, 0.06)`;
  const shadowColorRaw = isDark ? 'rgba(0, 0, 0, 0.5)' : 'rgba(0, 0, 0, 0.08)';
  const shadowColorHover = isDark ? 'rgba(0, 0, 0, 0.7)' : 'rgba(0, 0, 0, 0.12)';
  const textPrimary = isDark ? gray[900] : gray[900];
  const textSecondary = isDark ? gray[600] : gray[600];
  const iconColor = isDark ? gray[600] : gray[500];

  // Progress ring colors
  const progressBg = isDark ? gray[200] : gray[200];
  const progressFill = isDark ? gray[600] : gray[700];

  // Theme primary color
  const primaryColor = theme.palette.primary.main;
  const primaryDark = theme.palette.primary.dark;
  const primaryLight = theme.palette.primary.light;

  // Function to get connector-specific color
  const getConnectorColor = (connectorType: string): string => {
    switch (connectorType) {
      case 'GMAIL':
        return '#DB4437'; // Gmail red
      case 'DRIVE':
        return '#0078D4'; // Google Drive green
      case 'ONEDRIVE':
        return '#0078D4'; // OneDrive blue
      case 'SHAREPOINT':
        return '#036C70'; // SharePoint teal
      case 'DROPBOX':
        return '#0061FF'; // Dropbox blue
      case 'SLACK':
        return '#4A154B'; // Slack purple
      case 'TEAMS':
        return '#6264A7'; // Teams purple
      case 'JIRA':
        return '#0052CC'; // Jira blue
      case 'BOX':
        return '#0061D5'; // Box blue
      default:
        return primaryColor; // Use theme primary color as fallback
    }
  };

  // Get connector-specific color
  const connectorColor = getConnectorColor(connectorName);

  return (
    <>
      <Paper
        elevation={0}
        sx={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: '8px',
          overflow: 'hidden',
          boxShadow: `0 1px 2px ${shadowColorRaw}`,
          border: '1px solid',
          borderColor,
          bgcolor: bgCard,
          transition: 'all 0.2s ease-out',
          position: 'relative',
          '&:hover': {
            boxShadow: `0 4px 8px ${shadowColorHover}`,
            transform: 'translateY(-1px)',
          },
        }}
      >
        {/* Connector Icon and Name - Clean header design */}
        <Box
          sx={{
            py: 2,
            px: 2.5,
            display: 'flex',
            alignItems: 'center',
            borderBottom: '1px solid',
            borderColor,
            background: bgHeader,
          }}
        >
          <Avatar
            sx={{
              width: 32,
              height: 32,
              bgcolor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.03)',
              color: connectorColor, // Use connector-specific color
              borderRadius: '6px',
              mr: 1.5,
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              boxShadow: isDark ? 'none' : '0 1px 2px rgba(0, 0, 0, 0.03)',
            }}
          >
            <Iconify icon={iconName} width={18} height={18} />
          </Avatar>
          <Box>
            <Typography
              variant="subtitle2"
              sx={{
                fontWeight: 600,
                color: textPrimary,
                lineHeight: 1.3,
                fontSize: '0.875rem',
                letterSpacing: '-0.01em',
              }}
            >
              {displayName}
            </Typography>
            <Typography
              variant="caption"
              sx={{
                color: textSecondary,
                fontWeight: 400,
                display: 'block',
                fontSize: '0.75rem',
              }}
            >
              {total.toLocaleString()} records
            </Typography>
          </Box>

          {/* Status indicator */}
          {isComplete ? (
            <Box
              sx={{
                ml: 'auto',
                px: 1.5,
                py: 0.5,
                borderRadius: '4px',
                fontSize: '0.65rem',
                fontWeight: 500,
                letterSpacing: '0.02em',
                textTransform: 'uppercase',
                color: isDark ? gray[600] : gray[600],
                border: '1px solid',
                borderColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.05)',
                backgroundColor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.02)',
              }}
            >
              Synced
            </Box>
          ) : (
            <Box
              sx={{
                ml: 'auto',
                width: 8,
                height: 8,
                borderRadius: '50%',
                backgroundColor: '#9E9E9E',
                boxShadow: isDark ? 'none' : '0 0 0 2px rgba(0, 0, 0, 0.03)',
              }}
            />
          )}
        </Box>

        {/* Stats Section - Premium SaaS layout */}
        <Box
          sx={{
            px: 2.5,
            pt: 2,
            pb: 2.5,
            display: 'flex',
            flexDirection: 'column',
            flex: 1,
            position: 'relative',
            zIndex: 1,
          }}
        >
          {/* Progress Circle - Cleaner, more refined visual */}
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              mb: 2.5,
              pb: 2.5,
              borderBottom: '1px solid',
              borderColor,
            }}
          >
            <Box
              sx={{
                position: 'relative',
                display: 'flex',
                alignItems: 'center',
                mr: 2.5,
              }}
            >
              <CircularProgress
                variant="determinate"
                value={100}
                size={42}
                thickness={3}
                sx={{
                  color: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)',
                }}
              />
              <CircularProgress
                variant="determinate"
                value={percentComplete}
                size={42}
                thickness={3}
                sx={{
                  color: progressFill,
                  position: 'absolute',
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
                  width: '100%',
                }}
              >
                <Typography
                  variant="caption"
                  fontWeight="600"
                  sx={{ color: textPrimary, fontSize: '0.75rem' }}
                >
                  {`${percentComplete}%`}
                </Typography>
              </Box>
            </Box>
            <Box>
              <Typography
                variant="body2"
                sx={{
                  color: textPrimary,
                  mb: 0.5,
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  letterSpacing: '-0.01em',
                }}
              >
                Indexing Progress
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  color: textSecondary,
                  fontWeight: 400,
                  fontSize: '0.7rem',
                  display: 'flex',
                  alignItems: 'center',
                }}
              >
                <Typography
                  component="span"
                  variant="caption"
                  sx={{
                    fontWeight: 600,
                    color: isDark ? gray[600] : gray[700],
                    mr: 0.5,
                    fontSize: '0.7rem',
                  }}
                >
                  {indexing_status.COMPLETED.toLocaleString()}
                </Typography>
                <Box component="span" sx={{ mx: 0.25, color: textSecondary }}>
                  /
                </Box>
                <Typography
                  component="span"
                  variant="caption"
                  sx={{
                    color: textSecondary,
                    fontSize: '0.7rem',
                  }}
                >
                  {total.toLocaleString()}
                  {` records indexed`}
                </Typography>
              </Typography>
            </Box>
          </Box>

          {/* Stats Grid - Linear/Vercel inspired layout */}
          <Grid container spacing={1.5} sx={{ mb: 2.5 }}>
            {statusItems.map((item) => (
              <Grid item xs={statusItems.length <= 4 ? 6 : 4} key={`status-${item.key}`}>
                <Tooltip title={item.tooltip} placement="top">
                  <Box
                    sx={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      backgroundColor: bgStats,
                      borderRadius: '6px',
                      py: 1.25,
                      px: 1,
                      border: '1px solid',
                      borderColor,
                      height: '100%',
                      justifyContent: 'center',
                      transition: 'all 0.15s ease-in-out',
                      '&:hover': {
                        backgroundColor: bgStatHover,
                      },
                    }}
                  >
                    <Iconify
                      icon={item.icon}
                      width={16}
                      height={16}
                      sx={{
                        color: iconColor,
                        mb: 0.75,
                        opacity: 0.9,
                      }}
                    />
                    <Typography
                      variant="subtitle2"
                      sx={{
                        fontWeight: 600,
                        color: textPrimary,
                        fontSize: '0.875rem',
                        mb: 0.25,
                        letterSpacing: '-0.01em',
                      }}
                    >
                      {item.count.toLocaleString()}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{
                        color: textSecondary,
                        fontSize: '0.675rem',
                        textAlign: 'center',
                        fontWeight: 400,
                      }}
                    >
                      {item.label}
                    </Typography>
                  </Box>
                </Tooltip>
              </Grid>
            ))}
          </Grid>

          {/* Action Buttons - Premium SaaS style */}
          <Box
            sx={{
              mt: 'auto',
              display: 'flex',
              justifyContent: 'center',
              width: '100%',
              gap: 1.5,
              pt: 1.5,
              borderTop: '1px solid',
              borderColor,
            }}
          >
            {/* Resync button */}
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon={syncIcon} width={14} height={14} />}
              onClick={handleResync}
              disabled={isResyncing}
              sx={{
                borderRadius: '6px',
                textTransform: 'none',
                height: '30px',
                fontSize: '0.75rem',
                fontWeight: 500,
                color: primaryColor, // Use theme primary color
                borderColor: alpha(primaryColor, 0.5),
                backgroundColor: 'transparent',
                letterSpacing: '-0.01em',
                boxShadow: 'none',
                minWidth: '90px',
                px: 1.5,
                '&:hover': {
                  backgroundColor: alpha(primaryColor, 0.04),
                  borderColor: primaryColor,
                },
                '&:focus': {
                  boxShadow: `0 0 0 2px ${alpha(primaryColor, 0.2)}`,
                },
                '&:disabled': {
                  color: isDark ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
                  borderColor: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)',
                },
              }}
            >
              {isResyncing ? (
                <>
                  <CircularProgress size={12} sx={{ mr: 1, color: 'inherit' }} />
                  Syncing
                </>
              ) : (
                'Sync'
              )}
            </Button>

            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon={refreshIcon} width={14} height={14} />}
              onClick={handleReindex}
              disabled={isReindexing}
              sx={{
                borderRadius: '6px',
                textTransform: 'none',
                height: '30px',
                fontSize: '0.75rem',
                fontWeight: 500,
                color: primaryColor, // Use theme primary color
                borderColor: alpha(primaryColor, 0.5),
                letterSpacing: '-0.01em',
                minWidth: '120px',
                px: 1.5,
                '&:hover': {
                  backgroundColor: alpha(primaryColor, 0.04),
                  borderColor: primaryColor,
                },
                '&:focus': {
                  boxShadow: `0 0 0 2px ${alpha(primaryColor, 0.2)}`,
                },
                '&:disabled': {
                  color: isDark ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)',
                  borderColor: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)',
                },
              }}
            >
              {isReindexing ? (
                <>
                  <CircularProgress size={12} sx={{ mr: 1, color: 'inherit' }} />
                  Indexing
                </>
              ) : (
                'Reindex Failed'
              )}
            </Button>
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
            borderRadius: '6px',
            boxShadow: `0 4px 12px ${shadowColorRaw}`,
            backgroundColor:
              snackbar.severity === 'success'
                ? primaryColor
                : snackbar.severity === 'error'
                  ? theme.palette.error.main
                  : snackbar.severity === 'warning'
                    ? theme.palette.warning.main
                    : theme.palette.info.main,
            fontSize: '0.8rem',
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
  const isMounted = useRef<boolean>(true);

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
  const handleRefresh = (): void => {
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

  // Dark mode aware styles
  const isDark = theme.palette.mode === 'dark';
  const bgPaper = isDark ? '#1F2937' : COLORS.backgrounds.paper;
  const borderColor = isDark ? alpha('#4B5563', 0.6) : COLORS.border;

  // If initial loading and no data yet, show centered spinner
  if (initialLoading && !allStats.length) {
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
      ) : allStats.length === 0 ? (
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
            {normalizedConnectorNames.length > 0
              ? `No data found for the specified connector${normalizedConnectorNames.length > 1 ? 's' : ''}: ${normalizedConnectorNames.join(', ')}`
              : 'No connectors connected. Add a connector or upload files to get started.'}
          </Typography>
        </Alert>
      ) : (
        <Grid container spacing={1.5}>
          {allStats.map((stat, index) => (
            <Grid item xs={12} sm={6} md={6} lg={4} key={`${stat.connector}-${index}`}>
              <ConnectorCard connector={stat} />
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
