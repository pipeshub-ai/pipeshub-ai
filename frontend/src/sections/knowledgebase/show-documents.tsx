import dayjs from 'dayjs';
import { Icon } from '@iconify/react';
import React, { useState } from 'react';
import downloadIcon from '@iconify-icons/mdi/download';
import zipIcon from '@iconify-icons/vscode-icons/file-type-zip';
import pdfIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import wordIcon from '@iconify-icons/vscode-icons/file-type-word2';
import imageIcon from '@iconify-icons/vscode-icons/file-type-image';
import excelIcon from '@iconify-icons/vscode-icons/file-type-excel2';
import defaultFileIcon from '@iconify-icons/mdi/file-document-outline';
import powerpointIcon from '@iconify-icons/vscode-icons/file-type-powerpoint2';

import {
  Box,
  Card,
  Stack,
  Typography,
  IconButton,
  CardContent,
  CircularProgress,
} from '@mui/material';

import { handleDownloadDocument } from './utils';

import type { Record } from './types/record-details';

// Keep the existing utility functions
const getFileIcon = (extension: string) => {
  const ext = extension?.replace('.', '').toLowerCase();

  switch (ext) {
    case 'pdf':
      return pdfIcon;
    case 'doc':
    case 'docx':
      return wordIcon;
    case 'xls':
    case 'xlsx':
      return excelIcon;
    case 'ppt':
    case 'pptx':
      return powerpointIcon;
    case 'jpg':
    case 'jpeg':
    case 'png':
      return imageIcon;
    case 'zip':
    case 'rar':
      return zipIcon;
    default:
      return defaultFileIcon;
  }
};

const getExtensionColor = (extension: string) => {
  const ext = extension?.replace('.', '').toLowerCase();

  switch (ext) {
    case 'pdf':
      return '#FF4B4B';
    case 'doc':
    case 'docx':
      return '#2B579A';
    case 'xls':
    case 'xlsx':
      return '#217346';
    case 'jpg':
    case 'jpeg':
    case 'png':
      return '#4BAFFF';
    case 'zip':
    case 'rar':
      return '#FFA000';
    default:
      return '#757575';
  }
};

interface RecordDocumentViewerProps {
  record: Record;
}

const RecordDocumentViewer = ({ record }: RecordDocumentViewerProps) => {
  const [isDownloading, setIsDownloading] = useState<boolean>(false);
  if (!record?.fileRecord) return null;

  const {
    recordName,
    externalRecordId,
    departments,
    appSpecificRecordType,
    modules,
    createdAtTimestamp,
    fileRecord,
    origin,
  } = record;

  const handleDownload = async () => {
    try {
      if (origin === 'CONNECTOR') {
        const webUrl = record.fileRecord?.webUrl || record.mailRecord?.webUrl;
        if (webUrl) {
          window.open(webUrl, '_blank', 'noopener,noreferrer');
          return;
        }
      }
      setIsDownloading(true);
      await handleDownloadDocument(externalRecordId, recordName);
    } catch (error) {
      console.error('Failed to download document:', error);
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <Box sx={{ maxWidth: 800, width: '100%',p:2 }}>
      <Box>
        {/* Document Header */}
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 3 }}>
          <Icon
            icon={getFileIcon(fileRecord.extension)}
            width={40}
            height={40}
            style={{ color: getExtensionColor(fileRecord.extension) }}
          />
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" gutterBottom>
              {recordName}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Added on {dayjs(createdAtTimestamp).format('MMM DD, YYYY')}
            </Typography>
          </Box>
          {isDownloading ? (
            <CircularProgress size={28} />
          ) : (
            <IconButton onClick={handleDownload} sx={{ color: 'primary.main' }}>
              <Icon icon={downloadIcon} width={24} />
            </IconButton>
          )}
        </Stack>

        {/* {certificate && (
          <Stack direction="row" spacing={1} alignItems="flex-start">
            <Typography variant="body2" color="text.secondary">
              Certificate:
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap">
              <Chip
                label={certificate.certificateType}
                size="small"
                variant="outlined"
                color="primary"
              />
            </Stack>
          </Stack>
        )} */}

        {/* Document Details */}
        {/* <Stack spacing={2}>
          
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="body2" color="text.secondary" sx={{ width: 120 }}>
              Status:
            </Typography>
            <Chip
              label={status}
              size="small"
              color={status === 'PROCESSING' ? 'warning' : 'success'}
            />
          </Stack>

          
          {departments?.length > 0 && (
            <Stack direction="row" spacing={1} alignItems="flex-start">
              <Typography variant="body2" color="text.secondary" sx={{ width: 120 }}>
                Departments:
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {departments.map((dept) => (
                  <Chip key={dept._id} label={dept.name} size="small" variant="outlined" />
                ))}
              </Stack>
            </Stack>
          )}

          
          {appSpecificRecordType?.length > 0 && (
            <Stack direction="row" spacing={1} alignItems="flex-start">
              <Typography variant="body2" color="text.secondary" sx={{ width: 120 }}>
                Record Type:
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {appSpecificRecordType.map((type) => (
                  <Chip key={type._id} label={type.name} size="small" variant="outlined" />
                ))}
              </Stack>
            </Stack>
          )}

         
          {certificate && (
            <Stack direction="row" spacing={1} alignItems="flex-start">
              <Typography variant="body2" color="text.secondary" sx={{ width: 120 }}>
                Certificate:
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                <Chip
                  label={certificate.certificateType}
                  size="small"
                  variant="outlined"
                  color="primary"
                />
              </Stack>
            </Stack>
          )}
        </Stack> */}
      </Box>
    </Box>
  );
};

export default RecordDocumentViewer;
