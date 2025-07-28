import type { CustomCitation } from 'src/types/chat-bot';
import type {
  Permissions,
  RecordDetailsResponse,
} from 'src/sections/knowledgebase/types/record-details';

import { Icon } from '@iconify/react';
import React, { useState, useEffect } from 'react';
import linkIcon from '@iconify-icons/mdi/open-in-new';
import departmentIcon from '@iconify-icons/mdi/domain';
import fileIcon from '@iconify-icons/mdi/file-outline';
import languageIcon from '@iconify-icons/mdi/translate';
import filePdfIcon from '@iconify-icons/mdi/file-pdf-box';
import categoryIcon from '@iconify-icons/mdi/shape-outline';
import topicIcon from '@iconify-icons/mdi/bookmark-outline';
import fileDocIcon from '@iconify-icons/mdi/file-document-outline';

import {
  Box,
  Chip,
  Paper,
  alpha,
  Button,
  styled,
  Tooltip,
  useTheme,
  Typography,
  IconButton,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { ORIGIN } from 'src/sections/knowledgebase/constants/knowledge-search';
import { getConnectorPublicUrl } from 'src/sections/accountdetails/account-settings/services/utils/services-configuration-service';

import PDFViewer from './pdf-viewer';

// Styled components for consistent styling
const StyledPaper = styled(Paper)(({ theme }) => ({
  borderRadius: theme.shape.borderRadius,
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.paper, 0.8)
      : theme.palette.background.paper,
  boxShadow:
    theme.palette.mode === 'dark'
      ? '0 4px 20px 0 rgba(0, 0, 0, 0.3)'
      : '0 4px 20px 0 rgba(0, 0, 0, 0.08)',
  border: theme.palette.mode === 'dark' ? `1px solid ${alpha(theme.palette.divider, 0.1)}` : 'none',
  overflow: 'hidden',
}));

const RecordHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2.5),
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.dark, 0.1)
      : alpha(theme.palette.primary.lighter, 0.2),
  borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  position: 'relative',
}));

const InfoGrid = styled(Box)(({ theme }) => ({
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
  gap: theme.spacing(2),
  margin: theme.spacing(2, 0),
}));

const InfoCard = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.5),
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.default, 0.4)
      : alpha(theme.palette.background.default, 1),
  borderRadius: theme.shape.borderRadius,
  border:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.divider, 0.05)}`
      : `1px solid ${alpha(theme.palette.divider, 0.1)}`,
  transition: 'all 0.2s ease-in-out',
  '&:hover': {
    backgroundColor:
      theme.palette.mode === 'dark'
        ? alpha(theme.palette.background.default, 0.5)
        : alpha(theme.palette.background.default, 0.7),
  },
}));

const SectionTitle = styled(Typography)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.75),
  marginBottom: theme.spacing(1.5),
  fontWeight: 600,
  color: theme.palette.text.primary,
  '& svg': {
    color: theme.palette.primary.main,
    opacity: 0.8,
  },
}));

const MetadataSection = styled(Box)(({ theme }) => ({
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.default, 0.4)
      : alpha(theme.palette.background.default, 1),
  borderRadius: theme.shape.borderRadius,
  padding: theme.spacing(1.5),
  marginBottom: theme.spacing(2),
  border:
    theme.palette.mode === 'dark'
      ? `1px solid ${alpha(theme.palette.divider, 0.05)}`
      : `1px solid ${alpha(theme.palette.divider, 0.1)}`,
}));

const ActionButton = styled(Button)(({ theme }) => ({
  textTransform: 'none',
  borderRadius: theme.shape.borderRadius,
  fontWeight: 500,
  boxShadow: 'none',
  padding: '6px 12px',
  '&:hover': {
    boxShadow:
      theme.palette.mode === 'dark'
        ? '0 4px 10px rgba(0, 0, 0, 0.3)'
        : '0 4px 10px rgba(0, 0, 0, 0.1)',
  },
}));

interface RecordDetailsProps {
  recordId: string;
  onExternalLink?: string;
}

const RecordDetails = ({ recordId, onExternalLink}: RecordDetailsProps) => {
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';

  const [recordData, setRecordData] = useState<RecordDetailsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [isPDFViewerOpen, setIsPDFViewerOpen] = useState<boolean>(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [fileBuffer, setFileBuffer] = useState<ArrayBuffer>();

  useEffect(() => {
    if (!recordId) return;

    const fetchRecordDetails = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await axios.get<RecordDetailsResponse>(
          `/api/v1/knowledgebase/record/${recordId}`
        );
        setRecordData(response.data);
      } catch (err) {
        setError('Failed to fetch record details. Please try again later.');
      } finally {
        setLoading(false);
      }
    };

    fetchRecordDetails();
  }, [recordId]);

  const handleOpenPDFViewer = async () => {
    const record = recordData?.record;
    if (record?.origin === ORIGIN.UPLOAD) {
      if (record?.externalRecordId) {
        try {
          const { externalRecordId } = record;
          const response = await axios.get(`/api/v1/document/${externalRecordId}/download`, {
            responseType: 'blob',
          });

          // Read the blob response as text to check if it's JSON with signedUrl
          const reader = new FileReader();
          const textPromise = new Promise<string>((resolve) => {
            reader.onload = () => {
              resolve(reader.result?.toString() || '');
            };
          });

          reader.readAsText(response.data);
          const text = await textPromise;

          try {
            // Try to parse as JSON to check for signedUrl property
            const jsonData = JSON.parse(text);
            if (jsonData && jsonData.signedUrl) {
              setPdfUrl(jsonData.signedUrl);
              setIsPDFViewerOpen(true);
              return;
            }
          } catch (e) {
            // Case 2: Local storage - Return buffer
            const bufferReader = new FileReader();
            const arrayBufferPromise = new Promise<ArrayBuffer>((resolve) => {
              bufferReader.onload = () => {
                resolve(bufferReader.result as ArrayBuffer);
              };
              bufferReader.readAsArrayBuffer(response.data);
            });

            const buffer = await arrayBufferPromise;
            setFileBuffer(buffer);
            setIsPDFViewerOpen(true);
            return;
          }

          throw new Error('Invalid response format');
        } catch (err) {
          console.error('Error downloading document:', err);
          throw new Error('Failed to download document');
        }
      }
    } else if (record?.origin === ORIGIN.CONNECTOR) {
      try {
        const publicConnectorUrlResponse = await getConnectorPublicUrl();
        let response;
        if (publicConnectorUrlResponse && publicConnectorUrlResponse.url) {
          const CONNECTOR_URL = publicConnectorUrlResponse.url;
          response = await axios.get(`${CONNECTOR_URL}/api/v1/stream/record/${recordId}`, {
            responseType: 'blob',
          });
        } else {
          response = await axios.get(
            `${CONFIG.backendUrl}/api/v1/knowledgeBase/stream/record/${recordId}`,
            {
              responseType: 'blob',
            }
          );
        }
        if (!response) return;

        // Convert blob directly to ArrayBuffer
        const bufferReader = new FileReader();
        const arrayBufferPromise = new Promise<ArrayBuffer>((resolve, reject) => {
          bufferReader.onload = () => {
            // Create a copy of the buffer to prevent detachment issues
            const originalBuffer = bufferReader.result as ArrayBuffer;
            const bufferCopy = originalBuffer.slice(0);
            resolve(bufferCopy);
          };
          bufferReader.onerror = () => {
            reject(new Error('Failed to read blob as array buffer'));
          };
          bufferReader.readAsArrayBuffer(response.data);
        });

        const buffer = await arrayBufferPromise;
        setFileBuffer(buffer);
        setIsPDFViewerOpen(true);
      } catch (err) {
        console.error('Error downloading document:', err);
        throw new Error(`Failed to download document: ${err.message}`);
      }
    }
  };

  const handleClosePDFViewer = () => {
    setIsPDFViewerOpen(false);
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl);
      setPdfUrl(null);
    }
  };

  // Helper function to render metadata chips with consistent styling
  const renderChips = (items: any) => {
    if (!items || items.length === 0) return null;

    return (
      <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
        {items.map((item: any) => (
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

  if (loading) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          py: 6,
          gap: 2,
        }}
      >
        <CircularProgress size={24} thickness={2} sx={{ color: 'primary.main' }} />
        <Typography
          color="text.secondary"
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            fontSize: '0.875rem',
            fontWeight: 500,
          }}
        >
          Loading record details...
        </Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography color="error.main" sx={{ fontWeight: 500 }}>
          {error}
        </Typography>
      </Box>
    );
  }

  if (!recordData) {
    return null;
  }

  const { record, metadata } = recordData;

  let webUrl = record.fileRecord?.webUrl || record.mailRecord?.webUrl;
  if (record.origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
    const baseUrl = `${window.location.protocol}//${window.location.host}`;
    const newWebUrl = baseUrl + webUrl;
    webUrl = newWebUrl;
  }

  return (
    <StyledPaper sx={{ mt: 2 }}>
      {/* Header Section */}
      <RecordHeader>
        <Typography
          variant="h6"
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1.5,
            fontWeight: 600,
            color: 'text.primary',
          }}
        >
          <Icon
            icon={fileIcon}
            width={20}
            height={20}
            style={{ color: theme.palette.primary.main }}
          />
          {record.recordName}
          {webUrl && (
            <Tooltip title="View document">
              <IconButton
                onClick={() => window.open(webUrl, '_blank', 'noopener,noreferrer')}
                size="small"
                sx={{
                  ml: 0.5,
                  color: 'primary.main',
                  bgcolor: isDarkMode
                    ? alpha(theme.palette.primary.main, 0.1)
                    : alpha(theme.palette.primary.lighter, 0.4),
                  '&:hover': {
                    bgcolor: isDarkMode
                      ? alpha(theme.palette.primary.main, 0.2)
                      : alpha(theme.palette.primary.lighter, 0.6),
                  },
                }}
              >
                <Icon icon={linkIcon} width={16} height={16} />
              </IconButton>
            </Tooltip>
          )}
        </Typography>

        {onExternalLink && (
          <Tooltip title="Open External Link">
            <IconButton
              size="small"
              onClick={() => window.open(onExternalLink, '_blank', 'noopener,noreferrer')}
              sx={{
                color: 'primary.main',
                bgcolor: isDarkMode
                  ? alpha(theme.palette.primary.main, 0.1)
                  : alpha(theme.palette.primary.lighter, 0.4),
                '&:hover': {
                  bgcolor: isDarkMode
                    ? alpha(theme.palette.primary.main, 0.2)
                    : alpha(theme.palette.primary.lighter, 0.6),
                },
              }}
            >
              <Icon icon={linkIcon} width={16} height={16} />
            </IconButton>
          </Tooltip>
        )}
      </RecordHeader>

      <Box sx={{ p: 3 }}>
        {/* Basic Record Information */}
        <SectionTitle variant="subtitle1">
          <Icon icon={fileDocIcon} width={18} height={18} />
          Record Information
        </SectionTitle>

        <InfoGrid>
          {[
            { label: 'Name', value: record.recordName },
            { label: 'Record Type', value: record.recordType },
            { label: 'Origin', value: record.origin },
            { label: 'Indexing Status', value: record.indexingStatus },
            { label: 'Version', value: record.version },
            {
              label: 'Created At',
              value: new Date(record.createdAtTimestamp).toLocaleString(),
            },
            {
              label: 'Updated At',
              value: new Date(record.updatedAtTimestamp).toLocaleString(),
            },
          ].map((item) => (
            <InfoCard key={item.label}>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontWeight: 600, letterSpacing: 0.2 }}
              >
                {item.label}
              </Typography>
              <Typography variant="body2" color="text.primary" sx={{ fontWeight: 500, mt: 0.5 }}>
                {item.value || 'N/A'}
              </Typography>
            </InfoCard>
          ))}

          {/* Knowledge Base */}
          {recordData?.knowledgeBase && (
            <InfoCard>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontWeight: 600, letterSpacing: 0.2 }}
              >
                Knowledge Base
              </Typography>
              <Typography variant="body2" color="text.primary" sx={{ fontWeight: 500, mt: 0.5 }}>
                {recordData?.knowledgeBase?.name}
              </Typography>
            </InfoCard>
          )}

          {/* Permissions */}
          {recordData?.permissions && recordData?.permissions?.length > 0 && (
            <InfoCard>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontWeight: 600, letterSpacing: 0.2 }}
              >
                Permissions
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', mt: 0.75 }}>
                {recordData?.permissions?.map((permission: Permissions) => (
                  <Chip
                    key={permission?.id || permission?.relationship}
                    label={permission?.relationship}
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
            </InfoCard>
          )}
        </InfoGrid>

        {/* Metadata Section */}
        {recordData?.metadata && (
          <Box sx={{ mt: 4 }}>
            <SectionTitle variant="subtitle1">
              <Icon icon={categoryIcon} width={18} height={18} />
              Metadata
            </SectionTitle>

            <Box
              sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}
            >
              {/* Departments */}
              {recordData.metadata.departments && recordData.metadata.departments.length > 0 && (
                <MetadataSection>
                  <Typography
                    variant="caption"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.5,
                      fontWeight: 600,
                      color: 'text.secondary',
                      letterSpacing: 0.2,
                      mb: 1,
                    }}
                  >
                    <Icon icon={departmentIcon} width={14} height={14} />
                    Departments
                  </Typography>
                  {renderChips(recordData.metadata.departments)}
                </MetadataSection>
              )}

              {/* Categories */}
              {recordData.metadata.categories && recordData.metadata.categories.length > 0 && (
                <MetadataSection>
                  <Typography
                    variant="caption"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.5,
                      fontWeight: 600,
                      color: 'text.secondary',
                      letterSpacing: 0.2,
                      mb: 1,
                    }}
                  >
                    <Icon icon={categoryIcon} width={14} height={14} />
                    Document Category
                  </Typography>
                  {renderChips(recordData.metadata.categories)}
                </MetadataSection>
              )}

              {/* Web Development */}
              {recordData.metadata.subcategories1 &&
                recordData.metadata.subcategories1.length > 0 &&
                recordData.metadata?.subcategories1[0]?.name !== '' && (
                  <MetadataSection>
                    <Typography
                      variant="caption"
                      sx={{
                        fontWeight: 600,
                        color: 'text.secondary',
                        letterSpacing: 0.2,
                        mb: 1,
                      }}
                    >
                      Document Sub-category level 1
                    </Typography>
                    {renderChips(recordData.metadata.subcategories1)}
                  </MetadataSection>
                )}

              {/* Technologies */}
              {recordData.metadata.subcategories2 &&
                recordData.metadata.subcategories2.length > 0 &&
                recordData.metadata?.subcategories2[0]?.name !== '' && (
                  <MetadataSection>
                    <Typography
                      variant="caption"
                      sx={{
                        fontWeight: 600,
                        color: 'text.secondary',
                        letterSpacing: 0.2,
                        mb: 1,
                      }}
                    >
                      Document Sub-category level 2
                    </Typography>
                    {renderChips(recordData.metadata.subcategories2)}
                  </MetadataSection>
                )}

              {/* Focus Areas */}
              {recordData.metadata.subcategories3 &&
                recordData.metadata.subcategories3.length > 0 &&
                recordData.metadata?.subcategories3[0]?.name !== '' && (
                  <MetadataSection>
                    <Typography
                      variant="caption"
                      sx={{
                        fontWeight: 600,
                        color: 'text.secondary',
                        letterSpacing: 0.2,
                        mb: 1,
                      }}
                    >
                      Document Sub-category level 3
                    </Typography>
                    {renderChips(recordData.metadata.subcategories3)}
                  </MetadataSection>
                )}

              {/* Topics */}
              {recordData.metadata.topics && recordData.metadata.topics.length > 0 && (
                <MetadataSection sx={{ gridColumn: { xs: 'auto', md: '1 / -1' } }}>
                  <Typography
                    variant="caption"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.5,
                      fontWeight: 600,
                      color: 'text.secondary',
                      letterSpacing: 0.2,
                      mb: 1,
                    }}
                  >
                    <Icon icon={topicIcon} width={14} height={14} />
                    Topics
                  </Typography>
                  {renderChips(recordData.metadata.topics)}
                </MetadataSection>
              )}

              {/* Languages */}
              {recordData.metadata.languages && recordData.metadata.languages.length > 0 && (
                <MetadataSection>
                  <Typography
                    variant="caption"
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 0.5,
                      fontWeight: 600,
                      color: 'text.secondary',
                      letterSpacing: 0.2,
                      mb: 1,
                    }}
                  >
                    <Icon icon={languageIcon} width={14} height={14} />
                    Languages
                  </Typography>
                  {renderChips(recordData.metadata.languages)}
                </MetadataSection>
              )}
            </Box>
          </Box>
        )}

        {/* File Record Details */}
        {record.fileRecord && (
          <Box sx={{ mt: 4 }}>
            <SectionTitle variant="subtitle1">
              <Icon icon={fileDocIcon} width={18} height={18} />
              File Information
            </SectionTitle>

            <InfoCard sx={{ p: 2 }}>
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' },
                  gap: 2,
                }}
              >
                <Typography variant="body2" sx={{ display: 'flex', flexDirection: 'column' }}>
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.secondary"
                    fontWeight={600}
                  >
                    File Name
                  </Typography>
                  {record.fileRecord.name}
                </Typography>

                {record.fileRecord?.extension && (
                  <Typography variant="body2" sx={{ display: 'flex', flexDirection: 'column' }}>
                    <Typography
                      component="span"
                      variant="caption"
                      color="text.secondary"
                      fontWeight={600}
                    >
                      File Extension
                    </Typography>
                    {record.fileRecord?.extension}
                  </Typography>
                )}

                <Typography variant="body2" sx={{ display: 'flex', flexDirection: 'column' }}>
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.secondary"
                    fontWeight={600}
                  >
                    MIME Type
                  </Typography>
                  {record.fileRecord.mimeType}
                </Typography>

                <Typography variant="body2" sx={{ display: 'flex', flexDirection: 'column' }}>
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.secondary"
                    fontWeight={600}
                  >
                    Size
                  </Typography>
                  {(record.fileRecord.sizeInBytes / 1024).toFixed(2)} KB
                </Typography>
              </Box>

              {record.fileRecord?.extension &&
                record.fileRecord?.extension.toLowerCase() === 'pdf' && (
                  <Box sx={{ mt: 2 }}>
                    <ActionButton
                      variant="contained"
                      color="primary"
                      startIcon={<Icon icon={filePdfIcon} width={16} />}
                      onClick={handleOpenPDFViewer}
                      sx={{
                        mt: 1,
                        bgcolor: 'primary.main',
                        '&:hover': {
                          bgcolor: 'primary.dark',
                        },
                      }}
                    >
                      View Document
                    </ActionButton>
                  </Box>
                )}
            </InfoCard>
          </Box>
        )}

        {(pdfUrl || fileBuffer) && (
          <PDFViewer
            open={isPDFViewerOpen}
            onClose={handleClosePDFViewer}
            pdfUrl={pdfUrl}
            pdfBuffer={fileBuffer}
            fileName={record.fileRecord?.name || 'Document'}
            // citations={citations}
          />
        )}
      </Box>
    </StyledPaper>
  );
};

export default RecordDetails;
