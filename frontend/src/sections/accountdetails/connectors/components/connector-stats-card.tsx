import { useState } from 'react';

import type { IconifyIcon } from '@iconify/react';

import syncIcon from '@iconify-icons/mdi/sync';
import refreshIcon from '@iconify-icons/mdi/refresh';
import clockOutlineIcon from '@iconify-icons/mdi/clock-outline';
import progressClockIcon from '@iconify-icons/mdi/progress-clock';
import fileCancelOutlineIcon from '@iconify-icons/mdi/file-cancel-outline';
import checkCircleOutlineIcon from '@iconify-icons/mdi/check-circle-outline';
import alertCircleOutlineIcon from '@iconify-icons/mdi/alert-circle-outline';

import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Paper,
  Avatar,
  Button,
  Tooltip,
  Snackbar,
  Typography,
  Grid,
  Alert,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';
import { Iconify } from 'src/components/iconify';
import { useConnectors } from '../context';

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

interface KnowledgeBaseStats {
  kb_id: string;
  kb_name: string;
  total: number;
  indexing_status: IndexingStatusStats;
  by_record_type: RecordTypeStats[];
}

export interface ConnectorStatsData {
  org_id: string;
  connector: string;
  origin: 'UPLOAD' | 'CONNECTOR';
  stats: BasicStats;
  by_record_type: RecordTypeStats[];
  knowledge_bases?: KnowledgeBaseStats[];
}

type SnackbarSeverity = 'success' | 'error' | 'warning' | 'info';

// Helper function to get connector data dynamically
const getConnectorData = (connectorName: string, allConnectors: any[]) => {
  const connector = allConnectors.find(c => 
    c.name.toUpperCase() === connectorName.toUpperCase() || 
    c.name === connectorName
  );
  
  return {
    displayName: connector?.name || connectorName,
    iconPath: connector?.iconPath || '/assets/icons/connectors/default.svg',
    appGroup: connector?.appGroup || ''
  };
};

export const ConnectorStatsCard = ({
  connector,
  showActions = true,
}: {
  connector: ConnectorStatsData;
  showActions?: boolean;
}): JSX.Element => {
  const theme = useTheme();
  const [isReindexing, setIsReindexing] = useState<boolean>(false);
  const [isResyncing, setIsResyncing] = useState<boolean>(false);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: SnackbarSeverity;
  }>({ open: false, message: '', severity: 'success' });

  // Get connector data from the hook
  const { activeConnectors, inactiveConnectors } = useConnectors();
  const allConnectors = [...activeConnectors, ...inactiveConnectors];

  const { connector: connectorName, stats } = connector;
  const { total, indexing_status } = stats;

  // Get dynamic connector data
  const connectorData = getConnectorData(connectorName, allConnectors);
  const displayName = connectorData.displayName;
  const iconName = connectorData.iconPath;

  const percentComplete =
    total > 0 ? Math.round(((indexing_status.COMPLETED || 0) / total) * 100) : 0;
  const isComplete = percentComplete === 100;
  const failedCount = indexing_status.FAILED || 0;
  const canShowSync = showActions;
  const canShowReindex = failedCount > 0;

  const handleReindex = async (): Promise<void> => {
    try {
      setIsReindexing(true);
      await axios.post('/api/v1/knowledgeBase/reindex-all/connector', { app: connectorName });
      setSnackbar({
        open: true,
        message: `Reindexing started for ${displayName}`,
        severity: 'success',
      });
    } catch (error) {
      setSnackbar({
        open: true,
        message: `Failed to reindex documents for ${displayName}`,
        severity: 'error',
      });
    } finally {
      setTimeout(() => setIsReindexing(false), 1000);
    }
  };

  const handleResync = async (): Promise<void> => {
    try {
      setIsResyncing(true);
      await axios.post('/api/v1/knowledgeBase/resync/connector', { connectorName });
      setSnackbar({
        open: true,
        message: `Resync started for ${displayName}`,
        severity: 'success',
      });
    } catch (error) {
      setSnackbar({ open: true, message: `Failed to resync ${displayName}`, severity: 'error' });
    } finally {
      setTimeout(() => setIsResyncing(false), 1000);
    }
  };

  const handleCloseSnackbar = (): void => {
    setSnackbar({ open: false, message: '', severity: 'success' });
  };

  const isDark = theme.palette.mode === 'dark';
  const white = '#ffffff';
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
  } as const;

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
  const progressFill = isDark ? gray[600] : gray[700];
  const primaryColor = theme.palette.primary.main;

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
  ] as const;

  const optionalStatusItems = [
    ...(indexing_status.FILE_TYPE_NOT_SUPPORTED > 0
      ? [
          {
            label: 'Unsupported',
            count: indexing_status.FILE_TYPE_NOT_SUPPORTED,
            icon: fileCancelOutlineIcon,
            tooltip: 'Unsupported File Types',
            key: 'unsupported' as const,
          },
        ]
      : []),
    ...(indexing_status.AUTO_INDEX_OFF > 0
      ? [
          {
            label: 'Manual Sync',
            count: indexing_status.AUTO_INDEX_OFF,
            icon: fileCancelOutlineIcon,
            tooltip: 'Auto Index Off',
            key: 'autoIndexOff' as const,
          },
        ]
      : []),
  ];

  const allStatusItems = [...statusItems, ...optionalStatusItems];

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
          '&:hover': { boxShadow: `0 4px 8px ${shadowColorHover}`, transform: 'translateY(-1px)' },
        }}
      >
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
              borderRadius: '6px',
              mr: 1.5,
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              boxShadow: isDark ? 'none' : '0 1px 2px rgba(0, 0, 0, 0.03)',
            }}
          >
            <img src={iconName} alt={displayName} width={18} height={18} />
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
              sx={{ color: textSecondary, fontWeight: 400, display: 'block', fontSize: '0.75rem' }}
            >
              {total.toLocaleString()} records
            </Typography>
          </Box>
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
            <Box sx={{ position: 'relative', display: 'flex', alignItems: 'center', mr: 2.5 }}>
              <CircularProgress
                variant="determinate"
                value={100}
                size={42}
                thickness={3}
                sx={{ color: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)' }}
              />
              <CircularProgress
                variant="determinate"
                value={percentComplete}
                size={42}
                thickness={3}
                sx={{
                  color: progressFill,
                  position: 'absolute',
                  '& .MuiCircularProgress-circle': { strokeLinecap: 'round' },
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
                >{`${percentComplete}%`}</Typography>
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
                  sx={{ color: textSecondary, fontSize: '0.7rem' }}
                >
                  {total.toLocaleString()}
                  {` records indexed`}
                </Typography>
              </Typography>
            </Box>
          </Box>

          <Grid container spacing={1.5} sx={{ mb: 2.5 }}>
            {allStatusItems.map((item) => (
              <Grid item xs={allStatusItems.length <= 4 ? 6 : 4} key={`status-${item.key}`}>
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
                      '&:hover': { backgroundColor: bgStatHover },
                    }}
                  >
                    <Iconify
                      icon={item.icon as IconifyIcon}
                      width={16}
                      height={16}
                      sx={{ color: iconColor, mb: 0.75, opacity: 0.9 }}
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

          {(canShowSync || canShowReindex) && (
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
              {canShowSync && (
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
                    color: primaryColor,
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
                    '&:focus': { boxShadow: `0 0 0 2px ${alpha(primaryColor, 0.2)}` },
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
              )}
              {canShowReindex && (
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
                    color: primaryColor,
                    borderColor: alpha(primaryColor, 0.5),
                    letterSpacing: '-0.01em',
                    minWidth: '120px',
                    px: 1.5,
                    '&:hover': {
                      backgroundColor: alpha(primaryColor, 0.04),
                      borderColor: primaryColor,
                    },
                    '&:focus': { boxShadow: `0 0 0 2px ${alpha(primaryColor, 0.2)}` },
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
              )}
            </Box>
          )}
        </Box>
      </Paper>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6, mr: 2, zIndex: 9999 }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </>
  );
};
