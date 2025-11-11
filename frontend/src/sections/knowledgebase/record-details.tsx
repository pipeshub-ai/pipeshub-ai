// RecordDetails.js - Modified to display both file and mail records
import type { User } from 'src/context/UserContext';
import type { Icon as IconifyIcon } from '@iconify/react';

import { Icon } from '@iconify/react';
import robotIcon from '@iconify-icons/mdi/robot';
import closeIcon from '@iconify-icons/mdi/close';
import React, { useState, useEffect } from 'react';
import pencilIcon from '@iconify-icons/mdi/pencil';
import updateIcon from '@iconify-icons/mdi/update';
import accountIcon from '@iconify-icons/mdi/account';
import refreshIcon from '@iconify-icons/mdi/refresh';
import clockIcon from '@iconify-icons/mdi/clock-outline';
import emailIcon from '@iconify-icons/mdi/email-outline';
import { useParams, useNavigate } from 'react-router-dom';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import trashCanIcon from '@iconify-icons/mdi/trash-can-outline';
import fileAlertIcon from '@iconify-icons/mdi/file-alert-outline';
import connectorIcon from '@iconify-icons/mdi/cloud-sync-outline';
import fileDocumentBoxIcon from '@iconify-icons/mdi/file-document-box';
import descriptionIcon from '@iconify-icons/mdi/file-document-outline';
import linkIcon from '@iconify-icons/mdi/open-in-new';
import pdfIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import docIcon from '@iconify-icons/vscode-icons/file-type-word';
import xlsIcon from '@iconify-icons/vscode-icons/file-type-excel';
import pptIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import txtIcon from '@iconify-icons/vscode-icons/file-type-text';
import mdIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import htmlIcon from '@iconify-icons/vscode-icons/file-type-html';
import jsonIcon from '@iconify-icons/vscode-icons/file-type-json';
import zipIcon from '@iconify-icons/vscode-icons/file-type-zip';
import imageIcon from '@iconify-icons/vscode-icons/file-type-image';
import databaseIcon from '@iconify-icons/mdi/database';
import infoIcon from '@iconify-icons/mdi/information-outline';

import {
  Box,
  Chip,
  Grid,
  Card,
  Stack,
  alpha,
  Alert,
  Drawer,
  Button,
  Divider,
  Tooltip,
  useTheme,
  Snackbar,
  Container,
  Typography,
  IconButton,
  CardHeader,
  CardContent,
  useMediaQuery,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from '@mui/material';

import axios from 'src/utils/axios';
import ReactMarkdown from 'react-markdown';
import { CONFIG } from 'src/config-global';
import { useUsers } from 'src/context/UserContext';

import { KnowledgeBaseAPI } from './services/api';
import RecordSalesAgent from './ask-me-anything';
import RecordDocumentViewer from './show-documents';
import EditRecordDialog from './edit-record-dialog';
import DeleteRecordDialog from './delete-record-dialog';
import type { MetadataItem, Permissions, RecordDetailsResponse } from './types/record-details';

const getIndexingStatusColor = (
  status: string
): 'success' | 'info' | 'error' | 'warning' | 'default' => {
  switch (status) {
    case 'COMPLETED':
      return 'success';
    case 'IN_PROGRESS':
      return 'info';
    case 'FAILED':
      return 'error';
    case 'NOT_STARTED':
      return 'warning';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'default';
    case 'AUTO_INDEX_OFF':
      return 'default';
    default:
      return 'warning';
  }
};

const getReindexButtonText = (status: string): string => {
  switch (status) {
    case 'FAILED':
      return 'Retry Indexing';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'File Not Supported';
    case 'AUTO_INDEX_OFF':
      return 'Enable Indexing';
    case 'NOT_STARTED':
      return 'Start Indexing';
    default:
      return 'Reindex';
  }
};

const getReindexButtonColor = (status: string): 'warning' | 'error' | 'primary' | 'info' => {
  switch (status) {
    case 'FAILED':
      return 'warning';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'error';
    case 'NOT_STARTED':
      return 'info';
    default:
      return 'primary';
  }
};

const getReindexTooltip = (status: string): string => {
  switch (status) {
    case 'FAILED':
      return 'Document indexing failed. Click to retry.';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'This file type is not supported for indexing';
    case 'AUTO_INDEX_OFF':
      return 'Document indexing is turned off';
    case 'NOT_STARTED':
      return 'Document indexing has not started yet';
    case 'IN_PROGRESS':
      return 'Document is currently being indexed';
    case 'COMPLETED':
      return 'Document has been successfully indexed. Click to reindex.';
    default:
      return 'Reindex document to update search indexes';
  }
};

export default function RecordDetails() {
  const { recordId } = useParams<{ recordId: string }>();
  const navigate = useNavigate();
  const [recordData, setRecordData] = useState<RecordDetailsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [isChatOpen, setIsChatOpen] = useState<boolean>(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState<boolean>(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState<boolean>(false);
  const users = useUsers() as User[];
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const [isRecordConnector, setIsRecordConnector] = useState<boolean>(false);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning',
  });
  const [isSummaryDialogOpen, setSummaryDialogOpen] = useState<boolean>(false);
  const [summary, setSummary] = useState<string>('');
  const [summaryLoading, setSummaryLoading] = useState<boolean>(false);
  const [actionMenuAnchor, setActionMenuAnchor] = useState(null);
  const isActionMenuOpen = Boolean(actionMenuAnchor);

  const handleActionMenuOpen = (event: any) => {
    setActionMenuAnchor(event.currentTarget);
  };

  const handleActionMenuClose = () => {
    setActionMenuAnchor(null);
  };

  useEffect(() => {
    const fetchData = async () => {
      try {
        if (!recordId) return;
        const data = await KnowledgeBaseAPI.getRecordDetails(recordId);
        setRecordData(data);
        if (data.record.origin === 'CONNECTOR') {
          setIsRecordConnector(true);
        }
      } catch (error) {
        console.error('Error:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [recordId]);

  const toggleChat = () => {
    setIsChatOpen(!isChatOpen);
  };

  const refreshRecordData = async () => {
    setLoading(true);
    try {
      if (!recordId) return;
      const data = await KnowledgeBaseAPI.getRecordDetails(recordId);
      setRecordData(data);
    } catch (error) {
      console.error('Error refreshing record data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteRecord = () => {
    // Redirect to records list page after successful deletion
    navigate('/knowledge-base/details');
  };

  const handleRetryIndexing = async (recId: string) => {
    try {
      const response = await axios.post(
        `${CONFIG.backendUrl}/api/v1/knowledgeBase/reindex/record/${recId}`
      );
      setSnackbar({
        open: true,
        message: response.data.reindexResponse.success
          ? 'File indexing started'
          : 'Failed to start reindexing',
        severity: response.data.reindexResponse.success ? 'success' : 'error',
      });
    } catch (error) {
      console.log('error in re indexing', error);
    }
  };

  const handleShowSummary = async () => {
    if (!record.summaryDocumentId) return;

    setSummaryLoading(true);
    setSummaryDialogOpen(true);

    try {
      const response = await axios.get(
        `${CONFIG.backendUrl}/api/v1/document/${record.summaryDocumentId}/download`
      );

      if (response.data && response.data.summary) {
        setSummary(response.data.summary);
      } else {
        setSummary('No summary available for this document.');
      }
    } catch (error) {
      console.error('Error fetching document summary:', error);
      setSummary('Failed to load document summary. Please try again later.');
    } finally {
      setSummaryLoading(false);
    }
  };

  if (loading) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'center',
          width: '100%',
          height: '100vh',
          gap: 2,
        }}
      >
        <CircularProgress size={32} thickness={2.5} />
        <Typography variant="body2" color="text.secondary">
          Loading record details...
        </Typography>
      </Box>
    );
  }

  if (!recordData || !recordData.record) {
    return (
      <Container maxWidth="md" sx={{ py: 4 }}>
        <Card
          sx={{
            p: 4,
            textAlign: 'center',
            borderRadius: 1,
            boxShadow: '0 1px 3px rgba(0, 0, 0, 0.12)',
          }}
        >
          <Icon
            icon={fileAlertIcon}
            style={{ fontSize: '48px', color: '#e53935', marginBottom: '16px' }}
          />
          <Typography variant="h5" color="error" gutterBottom fontWeight={500}>
            Record not found
          </Typography>
          <Typography color="text.secondary" paragraph>
            The requested record could not be found or you don&apos;t have permission to view it.
          </Typography>
          <Button
            variant="contained"
            startIcon={<Icon icon={arrowLeftIcon} />}
            onClick={() => navigate(-1)}
            sx={{ mt: 2 }}
          >
            Go Back
          </Button>
        </Card>
      </Container>
    );
  }

  const { record, knowledgeBase, permissions, metadata } = recordData;
  const createdAt = new Date(record.sourceCreatedAtTimestamp).toLocaleString();
  const updatedAt = new Date(record.sourceLastModifiedTimestamp).toLocaleString();

  // Check record type
  const isFileRecord = record.recordType === 'FILE' && record.fileRecord;
  const isMailRecord = record.recordType === 'MAIL' && record.mailRecord;

  // Get file information if it's a file record
  let fileSize = 'N/A';
  let fileType = 'N/A';
  let fileIcon: any = fileDocumentBoxIcon;
  let fileIconColor = '#1976d2';

  if (isFileRecord && record.fileRecord) {
    fileSize = formatFileSize(record.fileRecord.sizeInBytes);
    fileType = record.fileRecord.extension ? record.fileRecord.extension.toUpperCase() : 'N/A';
    fileIcon = getFileIcon(record.fileRecord.extension || '');
    fileIconColor = getFileIconColor(record.fileRecord.extension || '');
  } else if (isMailRecord) {
    fileIcon = emailIcon;
    fileIconColor = '#2196f3';
    fileType = 'EMAIL';
    // We don't have a size for emails, so leave fileSize as N/A
  }
  // Check all possible sources for webUrl
  const webUrl = record.webUrl || record.fileRecord?.webUrl || record.mailRecord?.webUrl;

  const hasValidNames = (items: MetadataItem[]) => {
    if (!items || items.length === 0) return false;
    return items.some((item: MetadataItem) => item && item.name);
  };

  // Render chips function for metadata items
  const renderChips = (items: MetadataItem[]) => {
    if (!items || items.length === 0) return null;
    const validItems = items.filter((item: MetadataItem) => item && item.name);
    if (validItems.length === 0) return null;

    return (
      <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
        {validItems.map((item: MetadataItem) => (
          <Chip
            key={item.id}
            label={item.name}
            size="small"
            sx={{
              height: 22,
              fontSize: '0.75rem',
              fontWeight: 500,
              borderRadius: '4px',
              // Clean, professional styling for both modes
              bgcolor: (themeVal) =>
                themeVal.palette.mode !== 'dark'
                  ? alpha(themeVal.palette.grey[800], 0.1)
                  : alpha(themeVal.palette.grey[100], 0.8),
              color: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? themeVal.palette.grey[100]
                  : themeVal.palette.grey[800],
              border: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? `1px solid ${alpha(themeVal.palette.grey[700], 0.5)}`
                  : `1px solid ${alpha(themeVal.palette.grey[300], 1)}`,
              '& .MuiChip-label': {
                px: 1,
                py: 0.25,
              },
              '&:hover': {
                bgcolor: (themeVal) =>
                  themeVal.palette.mode !== 'dark'
                    ? alpha(themeVal.palette.grey[700], 0.1)
                    : alpha(themeVal.palette.grey[200], 0.1),
              },
            }}
          />
        ))}
      </Box>
    );
  };

  return (
    <>
      <Box sx={{ width: '100%' }}>
        <Container sx={{ py: 3 }}>
          {/* Header */}
          <Card
            elevation={0}
            sx={{
              mb: 3,
              borderRadius: 1,
              boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
              overflow: 'visible',
            }}
          >
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                p: 3,
                flexWrap: { xs: 'wrap', sm: 'nowrap' },
                gap: 2,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <IconButton
                  onClick={() => navigate('/knowledge-base/details')}
                  size="small"
                  sx={{
                    borderRadius: 1,
                    bgcolor: alpha('#000', 0.04),
                  }}
                >
                  <Icon icon={arrowLeftIcon} fontSize={20} />
                </IconButton>

                <Icon
                  icon={fileIcon}
                  style={{
                    fontSize: '44px',
                    color: fileIconColor,
                    marginRight: '8px',
                  }}
                />

                <Box sx={{ maxWidth: '600px' }}>
                  <Typography variant="h6" fontWeight={500} noWrap sx={{ mb: 0.5 }}>
                    {record.recordName}
                  </Typography>
                  <Stack direction="row" spacing={2} alignItems="center">
                    <Chip
                      size="small"
                      label={record.recordType}
                      color="primary"
                      sx={{ height: 22, fontSize: '0.75rem' }}
                    />
                    {fileSize !== 'N/A' && (
                      <Typography variant="body2" color="text.secondary">
                        {fileSize}
                      </Typography>
                    )}
                  </Stack>
                </Box>
              </Box>
              <Box
                sx={{
                  display: { xs: 'none', lg: 'flex' },
                  flexWrap: 'wrap',
                  gap: 0.75,
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  '& > *': {
                    flexShrink: 0,
                  },
                }}
              >
                {/* Edit button */}
                {!isRecordConnector && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={pencilIcon} style={{ fontSize: '1rem' }} />}
                    onClick={() => setIsEditDialogOpen(true)}
                    sx={{
                      height: 32,
                      px: 1.75,
                      py: 0.75,
                      borderRadius: '4px',
                      textTransform: 'none',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      minWidth: 100,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? alpha(themeVal.palette.primary.main, 0.7)
                          : themeVal.palette.primary.main,
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? themeVal.palette.primary.light
                          : themeVal.palette.primary.main,
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? themeVal.palette.primary.light
                            : themeVal.palette.primary.dark,
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.1)
                            : alpha(themeVal.palette.primary.main, 0.05),
                      },
                    }}
                  >
                    Edit
                  </Button>
                )}

                {/* Summary button */}
                {record.summaryDocumentId && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={descriptionIcon} style={{ fontSize: '1rem' }} />}
                    onClick={handleShowSummary}
                    sx={{
                      height: 32,
                      px: 1.75,
                      py: 0.75,
                      borderRadius: '4px',
                      textTransform: 'none',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      minWidth: 100,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(255,255,255,0.23)'
                          : 'rgba(0,0,0,0.23)',
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.4)'
                            : 'rgba(0,0,0,0.4)',
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.05)'
                            : 'rgba(0,0,0,0.03)',
                      },
                    }}
                  >
                    Summary
                  </Button>
                )}

                {webUrl && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={linkIcon} style={{ fontSize: '1rem' }} />}
                    onClick={() => window.open(webUrl, '_blank', 'noopener,noreferrer')}
                    sx={{
                      height: 32,
                      px: 1.75,
                      py: 0.75,
                      borderRadius: '4px',
                      textTransform: 'none',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      minWidth: 100,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(255,255,255,0.23)'
                          : 'rgba(0,0,0,0.23)',
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.4)'
                            : 'rgba(0,0,0,0.4)',
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.05)'
                            : 'rgba(0,0,0,0.03)',
                      },
                    }}
                  >
                    Open
                  </Button>
                )}

                {/* Reindex button */}
                {!isRecordConnector && recordId && (
                  <Tooltip title={getReindexTooltip(record.indexingStatus)} placement="top" arrow>
                    <span>
                      <Button
                        variant="outlined"
                        startIcon={<Icon icon={refreshIcon} style={{ fontSize: '1rem' }} />}
                        disabled={
                          record.indexingStatus === 'FILE_TYPE_NOT_SUPPORTED' ||
                          record.indexingStatus === 'IN_PROGRESS'
                        }
                        onClick={() => handleRetryIndexing(recordId)}
                        sx={{
                          height: 32,
                          px: 1.75,
                          py: 0.75,
                          borderRadius: '4px',
                          textTransform: 'none',
                          fontSize: '0.8125rem',
                          fontWeight: 500,
                          minWidth: 100,
                          borderColor: (themeVal) =>
                            record.indexingStatus === 'FAILED'
                              ? themeVal.palette.mode === 'dark'
                                ? '#FACC15'
                                : '#D97706'
                              : themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.23)'
                                : 'rgba(0,0,0,0.23)',
                          color: (themeVal) =>
                            record.indexingStatus === 'FAILED'
                              ? themeVal.palette.mode === 'dark'
                                ? '#FACC15'
                                : '#D97706'
                              : themeVal.palette.mode === 'dark'
                                ? '#E0E0E0'
                                : '#4B5563',
                          borderWidth: '1px',
                          bgcolor: 'transparent',
                          '&:hover': {
                            borderColor: (themeVal) =>
                              record.indexingStatus === 'FAILED'
                                ? themeVal.palette.mode === 'dark'
                                  ? '#FDE68A'
                                  : '#B45309'
                                : themeVal.palette.mode === 'dark'
                                  ? 'rgba(255,255,255,0.4)'
                                  : 'rgba(0,0,0,0.4)',
                            bgcolor: (themeVal) =>
                              record.indexingStatus === 'FAILED'
                                ? themeVal.palette.mode === 'dark'
                                  ? 'rgba(250,204,21,0.08)'
                                  : 'rgba(217,119,6,0.04)'
                                : themeVal.palette.mode === 'dark'
                                  ? 'rgba(255,255,255,0.05)'
                                  : 'rgba(0,0,0,0.03)',
                          },
                          '&.Mui-disabled': {
                            borderColor: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.12)'
                                : 'rgba(0,0,0,0.12)',
                            color: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.3)'
                                : 'rgba(0,0,0,0.38)',
                          },
                        }}
                      >
                        {getReindexButtonText(record.indexingStatus)}
                      </Button>
                    </span>
                  </Tooltip>
                )}

                {/* Delete button */}
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<Icon icon={trashCanIcon} style={{ fontSize: '1rem' }} />}
                  onClick={() => setIsDeleteDialogOpen(true)}
                  sx={{
                    height: 32,
                    px: 1.75,
                    py: 0.75,
                    borderRadius: '4px',
                    textTransform: 'none',
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    minWidth: 100,
                    borderColor: (themeVal) =>
                      themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626',
                    color: (themeVal) => (themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626'),
                    borderWidth: '1px',
                    bgcolor: 'transparent',
                    '&:hover': {
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#F87171' : '#B91C1C',
                      bgcolor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(239,68,68,0.08)'
                          : 'rgba(220,38,38,0.04)',
                    },
                  }}
                >
                  Delete
                </Button>
              </Box>
              <Box
                sx={{
                  display: { xs: 'none', sm: 'none', md: 'flex', lg: 'none' },
                  flexWrap: 'wrap',
                  gap: 0.75,
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  '& > *': {
                    flexShrink: 0,
                  },
                }}
              >
                {/* Edit button */}
                {!isRecordConnector && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={pencilIcon} style={{ fontSize: '1rem' }} />}
                    onClick={() => setIsEditDialogOpen(true)}
                    sx={{
                      height: 32,
                      px: 1.75,
                      py: 0.75,
                      borderRadius: '4px',
                      textTransform: 'none',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      minWidth: 100,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? alpha(themeVal.palette.primary.main, 0.7)
                          : themeVal.palette.primary.main,
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? themeVal.palette.primary.light
                          : themeVal.palette.primary.main,
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? themeVal.palette.primary.light
                            : themeVal.palette.primary.dark,
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.1)
                            : alpha(themeVal.palette.primary.main, 0.05),
                      },
                    }}
                  >
                    Edit
                  </Button>
                )}

                {/* Summary button */}
                {record.summaryDocumentId && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={descriptionIcon} style={{ fontSize: '1rem' }} />}
                    onClick={handleShowSummary}
                    sx={{
                      height: 32,
                      px: 1.75,
                      py: 0.75,
                      borderRadius: '4px',
                      textTransform: 'none',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      minWidth: 100,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(255,255,255,0.23)'
                          : 'rgba(0,0,0,0.23)',
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.4)'
                            : 'rgba(0,0,0,0.4)',
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.05)'
                            : 'rgba(0,0,0,0.03)',
                      },
                    }}
                  >
                    Summary
                  </Button>
                )}

                {webUrl && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={linkIcon} style={{ fontSize: '1rem' }} />}
                    onClick={() => window.open(webUrl, '_blank', 'noopener,noreferrer')}
                    sx={{
                      height: 32,
                      px: 1.75,
                      py: 0.75,
                      borderRadius: '4px',
                      textTransform: 'none',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      minWidth: 100,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(255,255,255,0.23)'
                          : 'rgba(0,0,0,0.23)',
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.4)'
                            : 'rgba(0,0,0,0.4)',
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.05)'
                            : 'rgba(0,0,0,0.03)',
                      },
                    }}
                  >
                    Open
                  </Button>
                )}

                {/* Reindex button */}
                {!isRecordConnector && recordId && (
                  <Tooltip title={getReindexTooltip(record.indexingStatus)} placement="top" arrow>
                    <span>
                      <Button
                        variant="outlined"
                        startIcon={<Icon icon={refreshIcon} style={{ fontSize: '1rem' }} />}
                        disabled={
                          record.indexingStatus === 'FILE_TYPE_NOT_SUPPORTED' ||
                          record.indexingStatus === 'IN_PROGRESS'
                        }
                        onClick={() => handleRetryIndexing(recordId)}
                        sx={{
                          height: 32,
                          px: 1.75,
                          py: 0.75,
                          borderRadius: '4px',
                          textTransform: 'none',
                          fontSize: '0.8125rem',
                          fontWeight: 500,
                          minWidth: 100,
                          borderColor: (themeVal) =>
                            record.indexingStatus === 'FAILED'
                              ? themeVal.palette.mode === 'dark'
                                ? '#FACC15'
                                : '#D97706'
                              : themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.23)'
                                : 'rgba(0,0,0,0.23)',
                          color: (themeVal) =>
                            record.indexingStatus === 'FAILED'
                              ? themeVal.palette.mode === 'dark'
                                ? '#FACC15'
                                : '#D97706'
                              : themeVal.palette.mode === 'dark'
                                ? '#E0E0E0'
                                : '#4B5563',
                          borderWidth: '1px',
                          bgcolor: 'transparent',
                          '&:hover': {
                            borderColor: (themeVal) =>
                              record.indexingStatus === 'FAILED'
                                ? themeVal.palette.mode === 'dark'
                                  ? '#FDE68A'
                                  : '#B45309'
                                : themeVal.palette.mode === 'dark'
                                  ? 'rgba(255,255,255,0.4)'
                                  : 'rgba(0,0,0,0.4)',
                            bgcolor: (themeVal) =>
                              record.indexingStatus === 'FAILED'
                                ? themeVal.palette.mode === 'dark'
                                  ? 'rgba(250,204,21,0.08)'
                                  : 'rgba(217,119,6,0.04)'
                                : themeVal.palette.mode === 'dark'
                                  ? 'rgba(255,255,255,0.05)'
                                  : 'rgba(0,0,0,0.03)',
                          },
                          '&.Mui-disabled': {
                            borderColor: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.12)'
                                : 'rgba(0,0,0,0.12)',
                            color: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.3)'
                                : 'rgba(0,0,0,0.38)',
                          },
                        }}
                      >
                        {getReindexButtonText(record.indexingStatus)}
                      </Button>
                    </span>
                  </Tooltip>
                )}

                {/* Delete button */}
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<Icon icon={trashCanIcon} style={{ fontSize: '1rem' }} />}
                  onClick={() => setIsDeleteDialogOpen(true)}
                  sx={{
                    height: 32,
                    px: 1.75,
                    py: 0.75,
                    borderRadius: '4px',
                    textTransform: 'none',
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    minWidth: 100,
                    borderColor: (themeVal) =>
                      themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626',
                    color: (themeVal) => (themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626'),
                    borderWidth: '1px',
                    bgcolor: 'transparent',
                    '&:hover': {
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#F87171' : '#B91C1C',
                      bgcolor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(239,68,68,0.08)'
                          : 'rgba(220,38,38,0.04)',
                    },
                  }}
                >
                  Delete
                </Button>
              </Box>

              {/* Tablet: Compact buttons with text */}
              <Box
                sx={{
                  display: { xs: 'none', sm: 'flex', lg: 'none', md: 'none' },
                  gap: 0.5,
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  flexWrap: 'wrap',
                }}
              >
                {/* Edit button - Compact */}
                {!isRecordConnector && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={pencilIcon} style={{ fontSize: '14px' }} />}
                    onClick={() => setIsEditDialogOpen(true)}
                    sx={{
                      height: 28,
                      px: 1,
                      py: 0.25,
                      borderRadius: '6px',
                      textTransform: 'none',
                      fontSize: '0.75rem',
                      fontWeight: 500,
                      minWidth: 0,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? alpha(themeVal.palette.primary.main, 0.7)
                          : themeVal.palette.primary.main,
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? themeVal.palette.primary.light
                          : themeVal.palette.primary.main,
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? themeVal.palette.primary.light
                            : themeVal.palette.primary.dark,
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.1)
                            : alpha(themeVal.palette.primary.main, 0.05),
                      },
                      '& .MuiButton-startIcon': {
                        marginRight: '4px',
                        marginLeft: 0,
                      },
                    }}
                  >
                    Edit
                  </Button>
                )}

                {/* Summary button - Compact */}
                {record.summaryDocumentId && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={descriptionIcon} style={{ fontSize: '14px' }} />}
                    onClick={handleShowSummary}
                    sx={{
                      height: 28,
                      px: 1,
                      py: 0.25,
                      borderRadius: '6px',
                      textTransform: 'none',
                      fontSize: '0.75rem',
                      fontWeight: 500,
                      minWidth: 0,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(255,255,255,0.23)'
                          : 'rgba(0,0,0,0.23)',
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.4)'
                            : 'rgba(0,0,0,0.4)',
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.05)'
                            : 'rgba(0,0,0,0.03)',
                      },
                      '& .MuiButton-startIcon': {
                        marginRight: '4px',
                        marginLeft: 0,
                      },
                    }}
                  >
                    Summary
                  </Button>
                )}

                {/* Reindex button - Compact */}
                {!isRecordConnector && recordId && (
                  <Tooltip title={getReindexTooltip(record.indexingStatus)} arrow>
                    <span>
                      <Button
                        variant="outlined"
                        startIcon={<Icon icon={refreshIcon} style={{ fontSize: '14px' }} />}
                        onClick={() => handleRetryIndexing(recordId)}
                        disabled={
                          record.indexingStatus === 'FILE_TYPE_NOT_SUPPORTED' ||
                          record.indexingStatus === 'IN_PROGRESS'
                        }
                        sx={{
                          height: 28,
                          px: 1,
                          py: 0.25,
                          borderRadius: '6px',
                          textTransform: 'none',
                          fontSize: '0.75rem',
                          fontWeight: 500,
                          minWidth: 0,
                          borderColor: (themeVal) =>
                            record.indexingStatus === 'FAILED'
                              ? themeVal.palette.mode === 'dark'
                                ? '#FACC15'
                                : '#D97706'
                              : themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.23)'
                                : 'rgba(0,0,0,0.23)',
                          color: (themeVal) =>
                            record.indexingStatus === 'FAILED'
                              ? themeVal.palette.mode === 'dark'
                                ? '#FACC15'
                                : '#D97706'
                              : themeVal.palette.mode === 'dark'
                                ? '#E0E0E0'
                                : '#4B5563',
                          borderWidth: '1px',
                          bgcolor: 'transparent',
                          '&:hover': {
                            borderColor: (themeVal) =>
                              record.indexingStatus === 'FAILED'
                                ? themeVal.palette.mode === 'dark'
                                  ? '#FDE68A'
                                  : '#B45309'
                                : themeVal.palette.mode === 'dark'
                                  ? 'rgba(255,255,255,0.4)'
                                  : 'rgba(0,0,0,0.4)',
                            bgcolor: (themeVal) =>
                              record.indexingStatus === 'FAILED'
                                ? themeVal.palette.mode === 'dark'
                                  ? 'rgba(250,204,21,0.08)'
                                  : 'rgba(217,119,6,0.04)'
                                : themeVal.palette.mode === 'dark'
                                  ? 'rgba(255,255,255,0.05)'
                                  : 'rgba(0,0,0,0.03)',
                          },
                          '&.Mui-disabled': {
                            borderColor: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.12)'
                                : 'rgba(0,0,0,0.12)',
                            color: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? 'rgba(255,255,255,0.3)'
                                : 'rgba(0,0,0,0.38)',
                          },
                          '& .MuiButton-startIcon': {
                            marginRight: '4px',
                            marginLeft: 0,
                          },
                        }}
                      >
                        {record.indexingStatus === 'FAILED' ? 'Retry' : 'Sync'}
                      </Button>
                    </span>
                  </Tooltip>
                )}

                {webUrl && (
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon={linkIcon} style={{ fontSize: '14px' }} />}
                    onClick={() => window.open(webUrl, '_blank', 'noopener,noreferrer')}
                    sx={{
                      height: 28,
                      px: 1,
                      py: 0.25,
                      borderRadius: '6px',
                      textTransform: 'none',
                      fontSize: '0.75rem',
                      fontWeight: 500,
                      minWidth: 0,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(255,255,255,0.23)'
                          : 'rgba(0,0,0,0.23)',
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.4)'
                            : 'rgba(0,0,0,0.4)',
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.05)'
                            : 'rgba(0,0,0,0.03)',
                      },
                      '& .MuiButton-startIcon': {
                        marginRight: '4px',
                        marginLeft: 0,
                      },
                    }}
                  >
                    Open
                  </Button>
                )}

                {/* Delete button - Compact */}
                <Button
                  variant="outlined"
                  startIcon={<Icon icon={trashCanIcon} style={{ fontSize: '14px' }} />}
                  onClick={() => setIsDeleteDialogOpen(true)}
                  sx={{
                    height: 28,
                    px: 1,
                    py: 0.25,
                    borderRadius: '6px',
                    textTransform: 'none',
                    fontSize: '0.75rem',
                    fontWeight: 500,
                    minWidth: 0,
                    borderColor: (themeVal) =>
                      themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626',
                    color: (themeVal) => (themeVal.palette.mode === 'dark' ? '#EF4444' : '#DC2626'),
                    borderWidth: '1px',
                    bgcolor: 'transparent',
                    '&:hover': {
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#F87171' : '#B91C1C',
                      bgcolor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(239,68,68,0.08)'
                          : 'rgba(220,38,38,0.04)',
                    },
                    '& .MuiButton-startIcon': {
                      marginRight: '4px',
                      marginLeft: 0,
                    },
                  }}
                >
                  Delete
                </Button>
              </Box>

              {/* Mobile: Priority action + Hamburger Menu */}
              <Box
                sx={{
                  display: { xs: 'flex', sm: 'none' },
                  flexDirection: 'column',
                  gap: 1,
                  width: '100%',
                }}
              >
                <Box
                  sx={{
                    display: 'flex',
                    gap: 2,
                    width: '80%',
                    mx: 'auto',
                    mt: 1,
                  }}
                >
                  {/* Most important action - Edit (if available) */}
                  {!isRecordConnector && (
                    <Button
                      variant="outlined"
                      startIcon={<Icon icon={pencilIcon} style={{ fontSize: '0.875rem' }} />}
                      onClick={() => setIsEditDialogOpen(true)}
                      sx={{
                        height: 36,
                        px: 1.5,
                        py: 0.75,
                        borderRadius: '4px',
                        textTransform: 'none',
                        fontSize: '0.8125rem',
                        fontWeight: 500,
                        flex: 1,
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha(themeVal.palette.primary.main, 0.7)
                            : themeVal.palette.primary.main,
                        color: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? themeVal.palette.primary.light
                            : themeVal.palette.primary.main,
                        borderWidth: '1px',
                        bgcolor: 'transparent',
                        '&:hover': {
                          borderColor: (themeVal) =>
                            themeVal.palette.mode === 'dark'
                              ? themeVal.palette.primary.light
                              : themeVal.palette.primary.dark,
                          bgcolor: (themeVal) =>
                            themeVal.palette.mode === 'dark'
                              ? alpha(themeVal.palette.primary.main, 0.1)
                              : alpha(themeVal.palette.primary.main, 0.05),
                        },
                      }}
                    >
                      Edit
                    </Button>
                  )}

                  {/* Actions Menu Button */}
                  <Button
                    variant="outlined"
                    startIcon={<Icon icon="mdi:dots-horizontal" style={{ fontSize: '0.875rem' }} />}
                    onClick={handleActionMenuOpen}
                    sx={{
                      height: 36,
                      px: 1.5,
                      py: 0.75,
                      borderRadius: '4px',
                      textTransform: 'none',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      flex: 1,
                      borderColor: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? 'rgba(255,255,255,0.23)'
                          : 'rgba(0,0,0,0.23)',
                      color: (themeVal) =>
                        themeVal.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
                      borderWidth: '1px',
                      bgcolor: 'transparent',
                      '&:hover': {
                        borderColor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.4)'
                            : 'rgba(0,0,0,0.4)',
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? 'rgba(255,255,255,0.05)'
                            : 'rgba(0,0,0,0.03)',
                      },
                    }}
                  >
                    Actions
                  </Button>
                </Box>

                {/* Actions Menu */}
                <Menu
                  anchorEl={actionMenuAnchor}
                  open={isActionMenuOpen}
                  onClose={handleActionMenuClose}
                  anchorOrigin={{
                    vertical: 'bottom',
                    horizontal: 'right',
                  }}
                  transformOrigin={{
                    vertical: 'top',
                    horizontal: 'right',
                  }}
                  PaperProps={{
                    sx: {
                      mt: 1,
                      maxWidth: 350,
                      borderRadius: '8px',
                      boxShadow: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? '0 8px 32px rgba(0, 0, 0, 0.4)'
                          : '0 8px 32px rgba(0, 0, 0, 0.12)',
                      border: (themeVal) =>
                        themeVal.palette.mode === 'dark'
                          ? '1px solid rgba(255, 255, 255, 0.08)'
                          : '1px solid rgba(0, 0, 0, 0.08)',
                    },
                  }}
                >
                  {/* Summary */}
                  {record.summaryDocumentId && (
                    <MenuItem
                      onClick={() => {
                        handleShowSummary();
                        handleActionMenuClose();
                      }}
                      sx={{
                        py: 1,
                        px: 1,
                        '&:hover': {
                          bgcolor: (themeVal) =>
                            themeVal.palette.mode === 'dark'
                              ? 'rgba(255, 255, 255, 0.05)'
                              : 'rgba(0, 0, 0, 0.04)',
                        },
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: 36 }}>
                        <Icon icon={descriptionIcon} style={{ fontSize: '1.125rem' }} />
                      </ListItemIcon>
                      <ListItemText
                        primary="View Summary"
                        secondary="Show document summary"
                        primaryTypographyProps={{
                          fontSize: '0.775rem',
                          fontWeight: 500,
                        }}
                        secondaryTypographyProps={{
                          fontSize: '0.65rem',
                        }}
                      />
                    </MenuItem>
                  )}

                  {/* Open - NEW */}
                  {webUrl && (
                    <MenuItem
                      onClick={() => {
                        window.open(webUrl, '_blank', 'noopener,noreferrer');
                        handleActionMenuClose();
                      }}
                      sx={{
                        py: 1,
                        px: 1,
                        '&:hover': {
                          bgcolor: (themeVal) =>
                            themeVal.palette.mode === 'dark'
                              ? 'rgba(255, 255, 255, 0.05)'
                              : 'rgba(0, 0, 0, 0.04)',
                        },
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: 36 }}>
                        <Icon icon={linkIcon} style={{ fontSize: '1.125rem' }} />
                      </ListItemIcon>
                      <ListItemText
                        primary="Open Link"
                        secondary="Open in new tab"
                        primaryTypographyProps={{
                          fontSize: '0.775rem',
                          fontWeight: 500,
                        }}
                        secondaryTypographyProps={{
                          fontSize: '0.65rem',
                        }}
                      />
                    </MenuItem>
                  )}

                  {/* Reindex */}
                  {!isRecordConnector && recordId && (
                    <MenuItem
                      onClick={() => {
                        handleRetryIndexing(recordId);
                        handleActionMenuClose();
                      }}
                      disabled={
                        record.indexingStatus === 'FILE_TYPE_NOT_SUPPORTED' ||
                        record.indexingStatus === 'IN_PROGRESS'
                      }
                      sx={{
                        py: 1,
                        px: 1,
                        '&:hover': {
                          bgcolor: (themeVal) =>
                            themeVal.palette.mode === 'dark'
                              ? 'rgba(255, 255, 255, 0.05)'
                              : 'rgba(0, 0, 0, 0.04)',
                        },
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: 36 }}>
                        <Icon
                          icon={refreshIcon}
                          style={{
                            fontSize: '1rem',
                            color: record.indexingStatus === 'FAILED' ? '#FACC15' : 'inherit',
                          }}
                        />
                      </ListItemIcon>
                      <ListItemText
                        primary={getReindexButtonText(record.indexingStatus)}
                        secondary={getReindexTooltip(record.indexingStatus)}
                        primaryTypographyProps={{
                          fontSize: '0.775rem',
                          fontWeight: 500,
                          color: record.indexingStatus === 'FAILED' ? '#FACC15' : 'inherit',
                        }}
                        secondaryTypographyProps={{
                          fontSize: '0.65rem',
                        }}
                      />
                    </MenuItem>
                  )}

                  <Divider sx={{ my: 0.5 }} />

                  {/* Delete - Dangerous action at bottom */}
                  <MenuItem
                    onClick={() => {
                      setIsDeleteDialogOpen(true);
                      handleActionMenuClose();
                    }}
                    sx={{
                      py: 1,
                      px: 1,
                      color: '#DC2626',
                      '&:hover': {
                        bgcolor: 'rgba(220, 38, 38, 0.04)',
                      },
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 36 }}>
                      <Icon
                        icon={trashCanIcon}
                        style={{
                          fontSize: '1.125rem',
                          color: '#DC2626',
                        }}
                      />
                    </ListItemIcon>
                    <ListItemText
                      primary="Delete Record"
                      secondary="Permanently remove this record"
                      primaryTypographyProps={{
                        fontSize: '0.775rem',
                        fontWeight: 500,
                      }}
                      secondaryTypographyProps={{
                        fontSize: '0.65rem',
                      }}
                    />
                  </MenuItem>
                </Menu>
              </Box>
            </Box>

            <Divider />

            <Box sx={{ px: { xs: 2, sm: 3 }, py: 2 }}>
              <Grid container spacing={{ xs: 2, sm: 3 }}>
                <Grid item xs={12} sm={6} md={3}>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      fontSize: { xs: '0.8125rem', sm: '0.875rem' },
                    }}
                  >
                    <Icon icon={clockIcon} style={{ fontSize: '16px' }} />
                    Created: {createdAt}
                  </Typography>
                </Grid>

                <Grid item xs={12} sm={6} md={3}>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      fontSize: { xs: '0.8125rem', sm: '0.875rem' },
                    }}
                  >
                    <Icon icon={updateIcon} style={{ fontSize: '16px' }} />
                    Updated: {updatedAt}
                  </Typography>
                </Grid>

                <Grid item xs={12} sm={6} md={3}>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      fontSize: { xs: '0.8125rem', sm: '0.875rem' },
                      flexWrap: 'wrap',
                    }}
                  >
                    <Icon icon={updateIcon} style={{ fontSize: '16px' }} />
                    <Box component="span">Indexing Status:</Box>
                    <Chip
                      label={record.indexingStatus.replace('_', ' ')}
                      size="small"
                      color={getIndexingStatusColor(record.indexingStatus)}
                      sx={{
                        height: 20,
                        fontSize: '0.7rem',
                        fontWeight: 600,
                        '& .MuiChip-label': { px: 1 },
                      }}
                    />
                    {record.indexingStatus === 'FAILED' &&
                      record.reason &&
                      record.reason.length > 0 && (
                        <Tooltip title={record.reason} arrow>
                          <span style={{ cursor: 'pointer' }}>
                            <Icon icon={infoIcon} style={{ fontSize: '16px' }} />
                          </span>
                        </Tooltip>
                      )}
                  </Typography>
                </Grid>

                <Grid item xs={12} sm={6} md={3}>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      fontSize: { xs: '0.8125rem', sm: '0.875rem' },
                      flexWrap: 'wrap',
                    }}
                  >
                    <Icon
                      icon={record?.origin === 'CONNECTOR' ? connectorIcon : databaseIcon}
                      style={{ fontSize: '16px' }}
                    />
                    {knowledgeBase && `KB: ${knowledgeBase.name || 'Default'}`}
                    {record?.origin === 'CONNECTOR' && record.connectorName && (
                      <>{record.connectorName}</>
                    )}
                  </Typography>
                </Grid>
              </Grid>
            </Box>
          </Card>

          <Grid container spacing={3}>
            <Grid item xs={12} lg={8}>
              <Card
                elevation={0}
                sx={{
                  mb: 3,
                  borderRadius: 1,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
                  overflow: 'hidden',
                }}
              >
                <CardHeader
                  title="Document Details"
                  titleTypographyProps={{
                    variant: 'subtitle1',
                    fontWeight: 500,
                    fontSize: '1rem',
                  }}
                  sx={{
                    borderBottom: '1px solid',
                    borderColor: 'divider',
                    p: 2.5,
                  }}
                />

                <CardContent sx={{ p: 3 }}>
                  <Grid container spacing={4}>
                    <Grid item xs={12} sm={6}>
                      <Stack spacing={3}>
                        {isFileRecord && record.fileRecord && (
                          <Box>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              gutterBottom
                              sx={{
                                textTransform: 'uppercase',
                                fontWeight: 500,
                                letterSpacing: '0.5px',
                                display: 'block',
                                mb: 0.75,
                              }}
                            >
                              File Name
                            </Typography>
                            <Typography variant="body2">
                              {record.fileRecord?.name || 'N/A'}
                            </Typography>
                          </Box>
                        )}

                        {isMailRecord && record.mailRecord && (
                          <Box>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              gutterBottom
                              sx={{
                                textTransform: 'uppercase',
                                fontWeight: 500,
                                letterSpacing: '0.5px',
                                display: 'block',
                                mb: 0.75,
                              }}
                            >
                              Subject
                            </Typography>
                            <Typography variant="body2">
                              {record.mailRecord?.subject || 'N/A'}
                            </Typography>
                          </Box>
                        )}

                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 500,
                              letterSpacing: '0.5px',
                              display: 'block',
                              mb: 0.75,
                            }}
                          >
                            Type
                          </Typography>
                          <Chip
                            label={fileType}
                            size="small"
                            sx={{
                              height: 22,
                              fontSize: '0.75rem',
                              fontWeight: 500,
                              borderRadius: '4px',
                              // Clean, professional styling for both modes
                              bgcolor: (themeVal) =>
                                themeVal.palette.mode !== 'dark'
                                  ? alpha(themeVal.palette.grey[800], 0.1)
                                  : alpha(themeVal.palette.grey[100], 0.8),
                              color: (themeVal) =>
                                themeVal.palette.mode === 'dark'
                                  ? themeVal.palette.grey[100]
                                  : themeVal.palette.grey[800],
                              border: (themeVal) =>
                                themeVal.palette.mode === 'dark'
                                  ? `1px solid ${alpha(themeVal.palette.grey[700], 0.5)}`
                                  : `1px solid ${alpha(themeVal.palette.grey[300], 1)}`,
                              '& .MuiChip-label': {
                                px: 1,
                                py: 0.25,
                              },
                              '&:hover': {
                                bgcolor: (themeVal) =>
                                  themeVal.palette.mode !== 'dark'
                                    ? alpha(themeVal.palette.grey[700], 0.1)
                                    : alpha(themeVal.palette.grey[200], 0.1),
                              },
                            }}
                          />
                        </Box>

                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 500,
                              letterSpacing: '0.5px',
                              display: 'block',
                              mb: 0.75,
                            }}
                          >
                            Record ID
                          </Typography>
                          <Typography
                            variant="body2"
                            sx={{
                              fontFamily: 'monospace',
                              bgcolor: alpha('#000', 0.03),
                              p: 1.5,
                              borderRadius: 1,
                              fontSize: '0.85rem',
                              overflow: 'auto',
                            }}
                          >
                            {record._key}
                          </Typography>
                        </Box>
                      </Stack>
                    </Grid>

                    <Grid item xs={12} sm={6}>
                      <Stack spacing={3}>
                        {isFileRecord && (
                          <Box>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              gutterBottom
                              sx={{
                                textTransform: 'uppercase',
                                fontWeight: 500,
                                letterSpacing: '0.5px',
                                display: 'block',
                                mb: 0.75,
                              }}
                            >
                              File Size
                            </Typography>
                            <Typography variant="body2">{fileSize}</Typography>
                          </Box>
                        )}

                        {isMailRecord && record.mailRecord && (
                          <>
                            <Box>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                gutterBottom
                                sx={{
                                  textTransform: 'uppercase',
                                  fontWeight: 500,
                                  letterSpacing: '0.5px',
                                  display: 'block',
                                  mb: 0.75,
                                }}
                              >
                                From
                              </Typography>
                              <Typography variant="body2">{record.mailRecord.from}</Typography>
                            </Box>
                            <Box>
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                gutterBottom
                                sx={{
                                  textTransform: 'uppercase',
                                  fontWeight: 500,
                                  letterSpacing: '0.5px',
                                  display: 'block',
                                  mb: 0.75,
                                }}
                              >
                                To
                              </Typography>
                              <Typography variant="body2">
                                {Array.isArray(record.mailRecord.to)
                                  ? record.mailRecord.to.join(', ')
                                  : record.mailRecord.to}
                              </Typography>
                            </Box>
                            {record.mailRecord.cc && record.mailRecord.cc.length > 0 && (
                              <Box>
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  gutterBottom
                                  sx={{
                                    textTransform: 'uppercase',
                                    fontWeight: 500,
                                    letterSpacing: '0.5px',
                                    display: 'block',
                                    mb: 0.75,
                                  }}
                                >
                                  CC
                                </Typography>
                                <Typography variant="body2">
                                  {record.mailRecord.cc.join(', ')}
                                </Typography>
                              </Box>
                            )}
                          </>
                        )}

                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 500,
                              letterSpacing: '0.5px',
                              display: 'block',
                              mb: 0.75,
                            }}
                          >
                            Origin
                          </Typography>
                          <Typography variant="body2">{record.origin}</Typography>
                        </Box>

                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 500,
                              letterSpacing: '0.5px',
                              display: 'block',
                              mb: 0.75,
                            }}
                          >
                            Permissions
                          </Typography>
                          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                            {permissions.length > 0 ? (
                              permissions.map((permission: Permissions) => (
                                <Chip
                                  key={permission.id}
                                  label={permission.relationship}
                                  size="small"
                                  sx={{
                                    height: 22,
                                    fontSize: '0.75rem',
                                    fontWeight: 500,
                                    borderRadius: '4px',
                                    // Clean, professional styling for both modes
                                    bgcolor: (themeVal) =>
                                      themeVal.palette.mode !== 'dark'
                                        ? alpha(themeVal.palette.grey[800], 0.1)
                                        : alpha(themeVal.palette.grey[100], 0.8),
                                    color: (themeVal) =>
                                      themeVal.palette.mode === 'dark'
                                        ? themeVal.palette.grey[100]
                                        : themeVal.palette.grey[800],
                                    border: (themeVal) =>
                                      themeVal.palette.mode === 'dark'
                                        ? `1px solid ${alpha(themeVal.palette.grey[700], 0.5)}`
                                        : `1px solid ${alpha(themeVal.palette.grey[300], 1)}`,
                                    '& .MuiChip-label': {
                                      px: 1,
                                      py: 0.25,
                                    },
                                    '&:hover': {
                                      bgcolor: (themeVal) =>
                                        themeVal.palette.mode !== 'dark'
                                          ? alpha(themeVal.palette.grey[700], 0.1)
                                          : alpha(themeVal.palette.grey[200], 0.1),
                                    },
                                  }}
                                />
                              ))
                            ) : (
                              <Typography variant="body2">No permissions assigned</Typography>
                            )}
                          </Box>
                        </Box>
                      </Stack>
                    </Grid>
                  </Grid>
                </CardContent>
              </Card>

              {/* Show document viewer for both file and mail records */}
              <Card
                elevation={0}
                sx={{
                  borderRadius: 1,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
                  overflow: 'hidden',
                }}
              > 
                <RecordDocumentViewer record={record} />
              </Card>
            </Grid>

            <Grid item xs={12} lg={4}>
              <Card
                elevation={0}
                sx={{
                  borderRadius: 1,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
                  height: '100%',
                  display: 'flex',
                  flexDirection: 'column',
                  zIndex: '-708',
                }}
              >
                <CardHeader
                  title="Additional Information"
                  titleTypographyProps={{
                    variant: 'subtitle1',
                    fontWeight: 500,
                    fontSize: '1rem',
                  }}
                  sx={{
                    borderBottom: '1px solid',
                    borderColor: 'divider',
                    p: 2.5,
                  }}
                />

                <CardContent sx={{ p: 3.5, flexGrow: 1 }}>
                  <Stack spacing={3}>
                    {/* Email specific information */}
                    {isMailRecord &&
                      record.mailRecord &&
                      record.mailRecord.labelIds &&
                      record.mailRecord.labelIds.length > 0 && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Labels
                          </Typography>
                          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                            {record.mailRecord.labelIds.map((label) => (
                              <Chip
                                key={label}
                                label={label.split('_').join(' ')}
                                size="small"
                                sx={{
                                  height: 22,
                                  fontSize: '0.75rem',
                                  fontWeight: 500,
                                  borderRadius: '4px',
                                  // Clean, professional styling for both modes
                                  bgcolor: (themeVal) =>
                                    themeVal.palette.mode !== 'dark'
                                      ? alpha(themeVal.palette.grey[800], 0.1)
                                      : alpha(themeVal.palette.grey[100], 0.8),
                                  color: (themeVal) =>
                                    themeVal.palette.mode === 'dark'
                                      ? themeVal.palette.grey[100]
                                      : themeVal.palette.grey[800],
                                  border: (themeVal) =>
                                    themeVal.palette.mode === 'dark'
                                      ? `1px solid ${alpha(themeVal.palette.grey[700], 0.5)}`
                                      : `1px solid ${alpha(themeVal.palette.grey[300], 1)}`,
                                  '& .MuiChip-label': {
                                    px: 1,
                                    py: 0.25,
                                  },
                                  '&:hover': {
                                    bgcolor: (themeVal) =>
                                      themeVal.palette.mode !== 'dark'
                                        ? alpha(themeVal.palette.grey[700], 0.1)
                                        : alpha(themeVal.palette.grey[200], 0.1),
                                  },
                                }}
                              />
                            ))}
                          </Box>
                        </Box>
                      )}

                    {isMailRecord && record.mailRecord && record.mailRecord.date && (
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          gutterBottom
                          sx={{
                            textTransform: 'uppercase',
                            fontWeight: 600,
                            letterSpacing: '0.8px',
                            fontSize: '0.6875rem',
                            display: 'block',
                            mb: 1.25,
                            opacity: 0.85,
                          }}
                        >
                          Date
                        </Typography>
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 500,
                            color: 'text.primary',
                          }}
                        >
                          {record.mailRecord.date}
                        </Typography>
                      </Box>
                    )}

                    {/* Departments */}
                    {metadata?.departments &&
                      metadata.departments.length > 0 &&
                      hasValidNames(metadata.departments) && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Departments
                          </Typography>
                          {renderChips(metadata.departments)}
                        </Box>
                      )}

                    {metadata?.categories &&
                      metadata.categories.length > 0 &&
                      hasValidNames(metadata.categories) && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Document Category
                          </Typography>
                          {renderChips(metadata.categories)}
                        </Box>
                      )}

                    {/* Subcategories1 */}
                    {metadata?.subcategories1 &&
                      metadata.subcategories1.length > 0 &&
                      hasValidNames(metadata.subcategories1) && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Sub-category Level 1
                          </Typography>
                          {renderChips(metadata.subcategories1)}
                        </Box>
                      )}

                    {/* Subcategories2 */}
                    {metadata?.subcategories2 &&
                      metadata.subcategories2.length > 0 &&
                      hasValidNames(metadata.subcategories2) && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Sub-category Level 2
                          </Typography>
                          {renderChips(metadata.subcategories2)}
                        </Box>
                      )}

                    {/* Subcategories3 */}
                    {metadata?.subcategories3 &&
                      metadata.subcategories3.length > 0 &&
                      hasValidNames(metadata.subcategories3) && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Sub-category Level 3
                          </Typography>
                          {renderChips(metadata.subcategories3)}
                        </Box>
                      )}

                    {/* Topics */}
                    {metadata?.topics &&
                      metadata.topics.length > 0 &&
                      hasValidNames(metadata.topics) && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Topics
                          </Typography>
                          {renderChips(metadata.topics)}
                        </Box>
                      )}

                    {/* Languages */}
                    {metadata?.languages &&
                      metadata.languages.length > 0 &&
                      hasValidNames(metadata.languages) && (
                        <Box>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            gutterBottom
                            sx={{
                              textTransform: 'uppercase',
                              fontWeight: 600,
                              letterSpacing: '0.8px',
                              fontSize: '0.6875rem',
                              display: 'block',
                              mb: 1.25,
                              opacity: 0.85,
                            }}
                          >
                            Languages
                          </Typography>
                          {renderChips(metadata.languages)}
                        </Box>
                      )}

                    {(record.departments || record.appSpecificRecordType) && (
                      <Divider
                        sx={{
                          my: 1,
                          borderColor: (themeVal) =>
                            alpha(
                              themeVal.palette.divider,
                              themeVal.palette.mode === 'dark' ? 0.15 : 0.1
                            ),
                        }}
                      />
                    )}

                    {/* Original department section from the record */}
                    {record.departments && record.departments.length > 0 && (
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          gutterBottom
                          sx={{
                            textTransform: 'uppercase',
                            fontWeight: 600,
                            letterSpacing: '0.8px',
                            fontSize: '0.6875rem',
                            display: 'block',
                            mb: 1.25,
                            opacity: 0.85,
                          }}
                        >
                          Record Departments
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                          {record.departments.map((dept) => (
                            <Chip
                              key={dept._id}
                              label={dept.name}
                              size="small"
                              sx={{
                                height: 24,
                                fontSize: '0.8125rem',
                                fontWeight: 500,
                                borderRadius: '6px',
                                transition: 'all 0.2s ease-in-out',
                                bgcolor: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? alpha(themeVal.palette.primary.main, 0.15)
                                    : alpha(themeVal.palette.primary.main, 0.08),
                                color: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? themeVal.palette.primary.light
                                    : themeVal.palette.primary.dark,
                                border: (themeVal) =>
                                  `1px solid ${alpha(
                                    themeVal.palette.primary.main,
                                    themeVal.palette.mode === 'dark' ? 0.3 : 0.2
                                  )}`,
                                '& .MuiChip-label': {
                                  px: 1.5,
                                  py: 0.5,
                                },
                                '&:hover': {
                                  bgcolor: (themeVal) =>
                                    themeVal.palette.mode === 'dark'
                                      ? alpha(themeVal.palette.primary.main, 0.25)
                                      : alpha(themeVal.palette.primary.main, 0.12),
                                  transform: 'translateY(-1px)',
                                  boxShadow: (themeVal) =>
                                    `0 2px 8px ${alpha(themeVal.palette.primary.main, 0.2)}`,
                                },
                              }}
                            />
                          ))}
                        </Box>
                      </Box>
                    )}

                    {/* Original categories from the record */}
                    {record.appSpecificRecordType && record.appSpecificRecordType.length > 0 && (
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          gutterBottom
                          sx={{
                            textTransform: 'uppercase',
                            fontWeight: 600,
                            letterSpacing: '0.8px',
                            fontSize: '0.6875rem',
                            display: 'block',
                            mb: 1.25,
                            opacity: 0.85,
                          }}
                        >
                          Record Categories
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                          {record.appSpecificRecordType.map((type) => (
                            <Chip
                              key={type._id}
                              label={type.name}
                              size="small"
                              sx={{
                                height: 24,
                                fontSize: '0.8125rem',
                                fontWeight: 500,
                                borderRadius: '6px',
                                transition: 'all 0.2s ease-in-out',
                                bgcolor: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? alpha(themeVal.palette.primary.main, 0.15)
                                    : alpha(themeVal.palette.primary.main, 0.08),
                                color: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? themeVal.palette.primary.light
                                    : themeVal.palette.primary.dark,
                                border: (themeVal) =>
                                  `1px solid ${alpha(
                                    themeVal.palette.primary.main,
                                    themeVal.palette.mode === 'dark' ? 0.3 : 0.2
                                  )}`,
                                '& .MuiChip-label': {
                                  px: 1.5,
                                  py: 0.5,
                                },
                                '&:hover': {
                                  bgcolor: (themeVal) =>
                                    themeVal.palette.mode === 'dark'
                                      ? alpha(themeVal.palette.primary.main, 0.25)
                                      : alpha(themeVal.palette.primary.main, 0.12),
                                  transform: 'translateY(-1px)',
                                  boxShadow: (themeVal) =>
                                    `0 2px 8px ${alpha(themeVal.palette.primary.main, 0.2)}`,
                                },
                              }}
                            />
                          ))}
                        </Box>
                      </Box>
                    )}

                    {/* Original modules from the record */}
                    {record.modules && record.modules.length > 0 && (
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          gutterBottom
                          sx={{
                            textTransform: 'uppercase',
                            fontWeight: 600,
                            letterSpacing: '0.8px',
                            fontSize: '0.6875rem',
                            display: 'block',
                            mb: 1.25,
                            opacity: 0.85,
                          }}
                        >
                          Record Modules
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                          {record.modules.map((module) => (
                            <Chip
                              key={module._id}
                              label={module.name}
                              size="small"
                              sx={{
                                height: 24,
                                fontSize: '0.8125rem',
                                fontWeight: 500,
                                borderRadius: '6px',
                                transition: 'all 0.2s ease-in-out',
                                bgcolor: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? alpha(themeVal.palette.primary.main, 0.15)
                                    : alpha(themeVal.palette.primary.main, 0.08),
                                color: (themeVal) =>
                                  themeVal.palette.mode === 'dark'
                                    ? themeVal.palette.primary.light
                                    : themeVal.palette.primary.dark,
                                border: (themeVal) =>
                                  `1px solid ${alpha(
                                    themeVal.palette.primary.main,
                                    themeVal.palette.mode === 'dark' ? 0.3 : 0.2
                                  )}`,
                                '& .MuiChip-label': {
                                  px: 1.5,
                                  py: 0.5,
                                },
                                '&:hover': {
                                  bgcolor: (themeVal) =>
                                    themeVal.palette.mode === 'dark'
                                      ? alpha(themeVal.palette.primary.main, 0.25)
                                      : alpha(themeVal.palette.primary.main, 0.12),
                                  transform: 'translateY(-1px)',
                                  boxShadow: (themeVal) =>
                                    `0 2px 8px ${alpha(themeVal.palette.primary.main, 0.2)}`,
                                },
                              }}
                            />
                          ))}
                        </Box>
                      </Box>
                    )}

                    {record.createdBy && (
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          gutterBottom
                          sx={{
                            textTransform: 'uppercase',
                            fontWeight: 600,
                            letterSpacing: '0.8px',
                            fontSize: '0.6875rem',
                            display: 'block',
                            mb: 1.25,
                            opacity: 0.85,
                          }}
                        >
                          Created By
                        </Typography>
                        <Box
                          sx={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1.5,
                            p: 1.5,
                            borderRadius: 2,
                            bgcolor: (themeVal) =>
                              themeVal.palette.mode === 'dark'
                                ? alpha(themeVal.palette.background.paper, 0.4)
                                : alpha(themeVal.palette.grey[100], 0.5),
                            border: (themeVal) =>
                              `1px solid ${alpha(themeVal.palette.divider, 0.1)}`,
                          }}
                        >
                          <Box
                            sx={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              width: 32,
                              height: 32,
                              borderRadius: '50%',
                              bgcolor: (themeVal) =>
                                themeVal.palette.mode === 'dark'
                                  ? alpha(themeVal.palette.primary.main, 0.2)
                                  : alpha(themeVal.palette.primary.main, 0.1),
                            }}
                          >
                            <Icon
                              icon={accountIcon}
                              style={{
                                fontSize: '18px',
                                color: 'currentColor',
                                opacity: 0.8,
                              }}
                            />
                          </Box>
                          <Typography
                            variant="body2"
                            sx={{
                              fontWeight: 500,
                              color: 'text.primary',
                            }}
                          >
                            {(users && users.find((u) => u._id === record.createdBy)?.fullName) ||
                              'Unknown User'}
                          </Typography>
                        </Box>
                      </Box>
                    )}
                  </Stack>
                </CardContent>
              </Card>
            </Grid>
          </Grid>
        </Container>
        {/* Edit Record Dialog */}
        {recordData && recordData.record && recordData.knowledgeBase && (
          <EditRecordDialog
            open={isEditDialogOpen}
            onClose={() => setIsEditDialogOpen(false)}
            onRecordUpdated={refreshRecordData}
            storageDocumentId={record.externalRecordId}
            recordId={record._key}
            record={record}
          />
        )}
        {recordData && recordData.record && (
          <DeleteRecordDialog
            open={isDeleteDialogOpen}
            onClose={() => setIsDeleteDialogOpen(false)}
            onRecordDeleted={handleDeleteRecord}
            recordId={record._key}
            recordName={record.recordName}
          />
        )}
      </Box>

      <Dialog
        open={isSummaryDialogOpen}
        onClose={() => setSummaryDialogOpen(false)}
        maxWidth="md"
        fullWidth
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: '8px',
            boxShadow: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? '0 12px 28px rgba(0, 0, 0, 0.3), 0 5px 10px rgba(0, 0, 0, 0.2)'
                : '0 12px 28px rgba(0, 0, 0, 0.15), 0 5px 10px rgba(0, 0, 0, 0.1)',
            bgcolor: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? alpha(themeVal.palette.background.paper, 0.95)
                : themeVal.palette.background.paper,
            overflow: 'hidden',
            backdropFilter: 'blur(8px)',
            border: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? `1px solid ${alpha(themeVal.palette.divider, 0.1)}`
                : 'none',
          },
        }}
      >
        {/* Header with close button */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            p: 2.5,
            pb: 2,
            borderBottom: '1px solid',
            borderColor: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? alpha(themeVal.palette.divider, 0.1)
                : themeVal.palette.divider,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 36,
                height: 36,
                borderRadius: '6px',
                bgcolor: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.primary.main, 0.15)
                    : alpha(themeVal.palette.primary.main, 0.08),
              }}
            >
              <Icon
                icon={descriptionIcon}
                style={{
                  fontSize: '20px',
                }}
              />
            </Box>
            <Typography
              variant="h6"
              component="h2"
              sx={{
                fontWeight: 600,
                fontSize: '1.125rem',
                color: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? themeVal.palette.common.white
                    : themeVal.palette.grey[800],
              }}
            >
              Document Summary
            </Typography>
          </Box>
          <IconButton
            onClick={() => setSummaryDialogOpen(false)}
            size="small"
            edge="end"
            aria-label="close"
            sx={{
              width: 32,
              height: 32,
              borderRadius: '6px',
              color: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? themeVal.palette.grey[400]
                  : themeVal.palette.grey[600],
              '&:hover': {
                bgcolor: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.common.white, 0.05)
                    : alpha(themeVal.palette.common.black, 0.04),
              },
            }}
          >
            <Icon icon="mdi:close" width={20} height={20} />
          </IconButton>
        </Box>

        {/* Content area */}
        <DialogContent
          sx={{
            p: { xs: 2, sm: 3 },
            pb: 0,
            '&::-webkit-scrollbar': {
              width: '8px',
              borderRadius: '4px',
            },
            '&::-webkit-scrollbar-track': {
              background: 'transparent',
            },
            '&::-webkit-scrollbar-thumb': {
              background: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? alpha(themeVal.palette.grey[600], 0.4)
                  : alpha(themeVal.palette.grey[400], 0.4),
              borderRadius: '4px',
            },
            '&::-webkit-scrollbar-thumb:hover': {
              background: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? alpha(themeVal.palette.grey[600], 0.6)
                  : alpha(themeVal.palette.grey[400], 0.6),
            },
          }}
        >
          {summaryLoading ? (
            <Box
              sx={{
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                minHeight: '200px',
              }}
            >
              <CircularProgress
                size={28}
                thickness={3}
                sx={{
                  color: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? themeVal.palette.primary.light
                      : themeVal.palette.primary.main,
                }}
              />
            </Box>
          ) : (
            <Box
              sx={{
                py: 3,
                px: { xs: 2, sm: 3 },
                borderRadius: '8px',
                bgcolor: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.grey[900], 0.4)
                    : alpha(themeVal.palette.grey[50], 1),
                border: (themeVal) =>
                  `1px solid ${
                    themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.divider, 0.08)
                      : alpha(themeVal.palette.divider, 0.5)
                  }`,
                '& p': {
                  mt: 0,
                  mb: 2,
                  color: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? themeVal.palette.grey[300]
                      : themeVal.palette.grey[800],
                  lineHeight: 1.6,
                  fontSize: '0.9375rem',
                },
                '& p:last-of-type': { mb: 0 },
                '& h1, & h2, & h3, & h4, & h5, & h6': {
                  color: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? themeVal.palette.common.white
                      : themeVal.palette.grey[900],
                  fontWeight: 600,
                },
                '& h1': { fontSize: '1.5rem' },
                '& h2': { fontSize: '1.25rem' },
                '& h3': { fontSize: '1.125rem' },
                '& ul, & ol': { mb: 2, pl: 2.5 },
                '& li': {
                  mb: 1,
                  color: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? themeVal.palette.grey[300]
                      : themeVal.palette.grey[800],
                },
                '& code': {
                  fontFamily: 'monospace',
                  padding: '0.2em 0.4em',
                  fontSize: '0.875rem',
                  borderRadius: '4px',
                  backgroundColor: (themeVal) =>
                    themeVal.palette.mode === 'dark'
                      ? alpha(themeVal.palette.grey[800], 0.6)
                      : alpha(themeVal.palette.grey[100], 1),
                },
              }}
            >
              <ReactMarkdown>{summary}</ReactMarkdown>
            </Box>
          )}
        </DialogContent>

        {/* Actions area with buttons */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            gap: 1.5,
            p: 2.5,
            pt: 2,
            borderTop: '1px solid',
            borderColor: (themeVal) =>
              themeVal.palette.mode === 'dark'
                ? alpha(themeVal.palette.divider, 0.1)
                : themeVal.palette.divider,
          }}
        >
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            onClick={() => setSummaryDialogOpen(false)}
            sx={{
              height: 32,
              px: 2,
              borderRadius: '6px',
              textTransform: 'none',
              fontSize: '0.8125rem',
              fontWeight: 500,
              borderColor: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? alpha(themeVal.palette.grey[600], 0.5)
                  : themeVal.palette.grey[300],
              color: (themeVal) =>
                themeVal.palette.mode === 'dark'
                  ? themeVal.palette.grey[300]
                  : themeVal.palette.grey[700],
              '&:hover': {
                borderColor: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.grey[500], 0.5)
                    : themeVal.palette.grey[400],
                bgcolor: (themeVal) =>
                  themeVal.palette.mode === 'dark'
                    ? alpha(themeVal.palette.common.white, 0.05)
                    : alpha(themeVal.palette.common.black, 0.03),
              },
            }}
          >
            Close
          </Button>
          {/* <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() =>
              window.open(
                `${CONFIG.backendUrl}/api/v1/document/${record.summaryDocumentId}/download`,
                '_blank'
              )
            }
            disableElevation
            sx={{
              height: 32,
              px: 2,
              borderRadius: '6px',
              textTransform: 'none',
              fontSize: '0.8125rem',
              fontWeight: 500,
              bgcolor: (theme) =>
                theme.palette.mode === 'dark'
                  ? theme.palette.primary.dark
                  : theme.palette.primary.main,
              '&:hover': {
                bgcolor: (theme) =>
                  theme.palette.mode === 'dark'
                    ? alpha(theme.palette.primary.dark, 0.9)
                    : theme.palette.primary.dark,
              },
            }}
          >
            Download
          </Button> */}
        </Box>
      </Dialog>

      {/* Chat Drawer */}
      <Drawer
        anchor="right"
        open={isChatOpen}
        onClose={toggleChat}
        PaperProps={{
          sx: {
            width: { xs: '100%', sm: '1050px', md: '1050px' },
            maxWidth: '100%',
            boxShadow: '-1px 0 8px rgba(0, 0, 0, 0.15)',
          },
        }}
        BackdropProps={{
          sx: {
            backgroundColor: 'rgba(0, 0, 0, 0.3)',
          },
        }}
      >
        <Box
          sx={{
            width: '100%',
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {/* Chat Header */}
          <Box
            sx={{
              px: 3,
              py: 2,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderBottom: '1px solid',
              borderColor: 'divider',
              bgcolor: 'background.paper',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  width: 32,
                  height: 32,
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                  borderRadius: 1,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Icon
                  icon={robotIcon}
                  style={{ fontSize: '18px', color: theme.palette.primary.main }}
                />
              </Box>
              <Box>
                <Typography variant="subtitle2" fontWeight={500}>
                  AI Assistant
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Ask questions about this document
                </Typography>
              </Box>
            </Box>
            <IconButton
              onClick={toggleChat}
              size="small"
              sx={{ bgcolor: alpha('#000', 0.03), borderRadius: 1 }}
            >
              <Icon icon={closeIcon} fontSize={18} />
            </IconButton>
          </Box>

          {/* Chat Interface */}
          <Box sx={{ flexGrow: 1 }}>
            <RecordSalesAgent
              key={record._id} // Force new instance when record changes
              initialContext={{
                recordId: record._id,
                recordName: record.recordName,
                recordType: record.recordType,
                departments: record.departments?.map((d) => d.name),
                modules: record.modules?.map((m) => m.name),
                categories: record.appSpecificRecordType?.map((t) => t.name),
              }}
              recordId={record._id}
              containerStyle={{ height: '100%' }}
            />
          </Box>
        </Box>
      </Drawer>
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 7 }}
      >
        <Alert
          severity={snackbar.severity}
          sx={{
            width: '100%',
            ...(snackbar.severity === 'success' && {
              bgcolor: theme.palette.success.main,
              color: theme.palette.success.contrastText,
            }),
          }}
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </>
  );
}

// Helper function to format file size
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
};

// Get file icon based on extension
function getFileIcon(extension: string): React.ComponentProps<typeof IconifyIcon>['icon'] {
  const ext = extension?.toLowerCase() || '';

  switch (ext) {
    case 'pdf':
      return pdfIcon;
    case 'doc':
    case 'docx':
      return docIcon;
    case 'xls':
    case 'xlsx':
    case 'csv':
      return xlsIcon;
    case 'ppt':
    case 'pptx':
      return pptIcon;
    case 'jpg':
    case 'jpeg':
    case 'png':
    case 'gif':
    case 'bmp':
    case 'tiff':
    case 'ico':
    case 'webp':
      return imageIcon;
    case 'zip':
    case 'rar':
    case '7z':
      return zipIcon;
    case 'txt':
      return txtIcon;
    case 'html':
    case 'css':
    case 'js':
      return htmlIcon;
    case 'md':
    case 'mdx':
      return mdIcon;
    case 'json':
      return jsonIcon;
    case 'database':
      return databaseIcon;
    default:
      return descriptionIcon;
  }
}

// Get file icon color based on extension
function getFileIconColor(extension: string): string {
  const ext = extension?.toLowerCase() || '';

  switch (ext) {
    case 'pdf':
      return '#f44336';
    case 'doc':
    case 'docx':
      return '#2196f3';
    case 'xls':
    case 'xlsx':
      return '#4caf50';
    case 'ppt':
    case 'pptx':
      return '#ff9800';
    default:
      return '#1976d2';
  }
}
