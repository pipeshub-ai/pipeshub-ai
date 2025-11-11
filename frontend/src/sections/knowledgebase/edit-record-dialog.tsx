import type { Icon as IconifyIcon } from '@iconify/react';

import { Icon } from '@iconify/react';
import closeIcon from '@iconify-icons/carbon/close';
import infoIcon from '@iconify-icons/eva/info-outline';
import React, { useRef, useState, useEffect } from 'react';
import filePdfBoxIcon from '@iconify-icons/mdi/file-pdf-box';
import fileWordBoxIcon from '@iconify-icons/mdi/file-word-box';
import cloudIcon from '@iconify-icons/eva/cloud-upload-outline';
import fileExcelBoxIcon from '@iconify-icons/mdi/file-excel-box';
import fileImageBoxIcon from '@iconify-icons/mdi/file-image-box';
import fileTextBoxIcon from '@iconify-icons/mdi/file-text-outline';
import fileCodeBoxIcon from '@iconify-icons/mdi/file-code-outline';
import fileArchiveBoxIcon from '@iconify-icons/mdi/archive-outline';
import filePowerpointBoxIcon from '@iconify-icons/mdi/file-powerpoint-box';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';
import fileDelimitedOutlineIcon from '@iconify-icons/mdi/file-delimited-outline';

import {
  Box,
  alpha,
  Alert,
  Dialog,
  Button,
  Divider,
  useTheme,
  TextField,
  Typography,
  IconButton,
  AlertTitle,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import type { Record } from './types/record-details';
// Define a constant for maximum file size (30MB)
const MAX_FILE_SIZE = 30 * 1024 * 1024; // 30MB in bytes

interface SingleFileUploadDialogProps {
  open: boolean;
  onClose: () => void;
  maxFileSize?: number; // in bytes
  onRecordUpdated: () => void;
  storageDocumentId: string;
  recordId: string;
  record: Record;
}

const SingleFileUploadDialog: React.FC<SingleFileUploadDialogProps> = ({
  open,
  onClose,
  maxFileSize = MAX_FILE_SIZE, // Default to 30MB if not specified
  onRecordUpdated,
  storageDocumentId,
  recordId,
  record,
}) => {
  const [name, setName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [nameChanged, setNameChanged] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const theme = useTheme();
  // Create a ref for the file input to reset it when needed
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Get the current file extension from the record
  const currentExtension = record?.fileRecord?.extension?.toLowerCase() || '';
  const initialName = record?.recordName || '';
  const existingFile = !!record?.fileRecord;

  // Reset state when dialog opens/closes and set initial name
  useEffect(() => {
    if (open) {
      setName(initialName);
      setNameChanged(false);
      setFileError(null);
      // Reset file input when dialog opens
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    } else {
      setFile(null);
      setUploading(false);
      setNameChanged(false);
      setFileError(null);
    }
  }, [open, initialName]);

  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setName(e.target.value);
    setNameChanged(true);
  };

  // Helper function to get a more friendly file type name
  const getFileTypeName = (extension: string): string => {
    switch (extension.toLowerCase()) {
      case 'pdf':
        return 'PDF';
      case 'xlsx':
      case 'xls':
        return 'Excel';
      case 'docx':
      case 'doc':
        return 'Word';
      case 'pptx':
      case 'ppt':
        return 'PowerPoint';
      case 'txt':
        return 'Text';
      case 'csv':
        return 'CSV';
      default:
        return extension.toUpperCase();
    }
  };

  const validateFile = (selectedFile: File): boolean => {
    const newExtension = selectedFile.name.split('.').pop()?.toLowerCase() || '';

    // Ensure an extension exists
    if (!newExtension) {
      setFileError('Invalid file type. Please select a valid file.');
      return false;
    }

    // If there's an existing file, enforce the same extension
    if (existingFile && currentExtension) {
      if (newExtension !== currentExtension) {
        setFileError(
          `File type mismatch. You must upload a ${getFileTypeName(currentExtension)} file (.${currentExtension}).`
        );
        return false;
      }
    }

    // Ensure file size is within the limit
    if (selectedFile.size > maxFileSize) {
      setFileError(`File is too large. Maximum size is ${formatFileSize(maxFileSize)}.`);
      return false;
    }

    setFileError(null);
    return true;
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selectedFile = e.target.files[0];

      if (!validateFile(selectedFile)) {
        // Reset the file input on validation failure
        if (fileInputRef.current) {
          fileInputRef.current.value = '';
        }
        return;
      }

      setFile(selectedFile);

      // If name is empty, use the file name (without extension)
      if (!name.trim()) {
        const fileName = selectedFile.name.split('.').slice(0, -1).join('.');
        setName(fileName);
        setNameChanged(true);
      }
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const droppedFile = e.dataTransfer.files[0];

      if (!validateFile(droppedFile)) {
        return;
      }

      setFile(droppedFile);

      // If name is empty, use the file name (without extension)
      if (!name.trim()) {
        const fileName = droppedFile.name.split('.').slice(0, -1).join('.');
        setName(fileName);
        setNameChanged(true);
      }
    }
  };

  const handleUpload = async () => {
    if (file && !validateFile(file)) {
      return; // Double-check validation before upload
    }

    setUploading(true);

    try {
      // Create FormData object
      const formData = new FormData();

      // Only append file if a new one was selected
      if (file) {
        formData.append('file', file);
      }
      const config = {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      };
      // Always append the current name (which might be changed or not)
      formData.append('recordName', name.trim() || (file ? file.name : initialName));

      // Send the file to the API
      const response = await axios.put(
        `/api/v1/knowledgeBase/record/${recordId}`,
        formData,
        config
      );

      if (!response) {
        throw new Error(`Upload failed with status: ${response}`);
      }

      // Call onRecordUpdated
      onRecordUpdated();
      // Close the dialog
      onClose();
    } catch (error) {
      console.error('Error updating record:', error);
      setFileError('Failed to update. Please try again.');
    } finally {
      setUploading(false);
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  // Get file icon based on extension
  const getFileIcon = (extension: string): React.ComponentProps<typeof IconifyIcon>['icon'] => {
    const ext = extension?.toLowerCase() || '';

    switch (ext) {
      case 'pdf':
        return filePdfBoxIcon;
      case 'doc':
      case 'docx':
        return fileWordBoxIcon;
      case 'xls':
      case 'xlsx':
        return fileExcelBoxIcon;
      case 'ppt':
      case 'pptx':
        return filePowerpointBoxIcon;
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'webp':
      case 'svg':
        return fileImageBoxIcon;
      case 'zip':
      case 'rar':
      case '7z':
        return fileArchiveBoxIcon;
      case 'txt':
        return fileTextBoxIcon;
      case 'html':
      case 'css':
      case 'js':
        return fileCodeBoxIcon;
      case 'csv':
        return fileDelimitedOutlineIcon;
      default:
        return fileDocumentOutlineIcon;
    }
  };

  // Get file icon color based on extension
  const getFileIconColor = (extension: string): string => {
    const ext = extension?.toLowerCase() || '';

    switch (ext) {
      case 'pdf':
        return '#f44336';
      case 'doc':
      case 'docx':
        return '#2196f3';
      case 'xls':
      case 'xlsx':
      case 'csv':
        return '#4caf50';
      case 'ppt':
      case 'pptx':
        return '#ff9800';
      default:
        return '#1976d2';
    }
  };

  // Determine if we should enable the upload button
  const canUpload = file !== null || (nameChanged && name.trim() !== initialName);

  // This function handles the "Browse File" button click
  const handleBrowseClick = () => {
    // If we can't determine the file type for an existing file, show an error
    if (existingFile && !currentExtension) {
      setFileError('Cannot determine the file type to upload. Please contact support.');
      return;
    }

    // Otherwise, trigger the file input click
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(1px)',
          backgroundColor: alpha(theme.palette.common.black, 0.3),
        },
      }}
      PaperProps={{
        sx: {
          borderRadius: '16px',
          overflow: 'hidden',
          boxShadow: '0 10px 40px rgba(0,0,0,0.1)',
        },
      }}
    >
      <DialogTitle sx={{ p: 2, pb: 1 }}>
        <Typography variant="h6" fontWeight={600}>
          {existingFile ? 'Replace File' : 'Upload File'}
        </Typography>
      </DialogTitle>

      {uploading ? (
        <DialogContent
          sx={{
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: '250px',
            py: 5,
          }}
        >
          <CircularProgress size={42} thickness={4} sx={{ mb: 3 }} />
          <Typography variant="subtitle1" fontWeight={500}>
            {file ? 'Uploading file...' : 'Updating record...'}
          </Typography>
        </DialogContent>
      ) : (
        <DialogContent sx={{ p: 3, pt: 2 }}>
          {fileError && (
            <Alert
              severity="error"
              sx={{ mb: 3, borderRadius: '10px', boxShadow: '0 2px 8px rgba(244,67,54,0.15)' }}
            >
              <AlertTitle>Error</AlertTitle>
              {fileError}
            </Alert>
          )}

          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
            Name {existingFile ? '' : '(Optional)'}
          </Typography>
          <TextField
            value={name}
            onChange={handleNameChange}
            fullWidth
            size="small"
            placeholder="Enter name"
            variant="outlined"
            sx={{
              mb: 2,
              '& .MuiOutlinedInput-root': {
                borderRadius: '10px',
                '&:hover fieldset': {
                  borderColor: alpha('#1976d2', 0.5),
                },
              },
            }}
          />

          {/* Current file info when existing */}
          {existingFile && record?.fileRecord && (
            <>
              <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                Current File
              </Typography>
              <Box
                sx={{
                  mb: 1,
                  p: 2,
                  borderRadius: '12px',
                  bgcolor: alpha('#000', 0.02),
                  border: `1px solid ${alpha('#000', 0.06)}`,
                  display: 'flex',
                  alignItems: 'center',
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    bgcolor: alpha('#000', 0.03),
                  },
                }}
              >
                <Icon
                  icon={getFileIcon(currentExtension)}
                  style={{
                    fontSize: '32px',
                    color: getFileIconColor(currentExtension),
                    marginRight: '16px',
                  }}
                />
                <Box sx={{ flexGrow: 1 }}>
                  <Typography variant="body1" fontWeight={500} sx={{ mb: 0.5 }}>
                    {record.recordName}
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'flex', alignItems: 'center', gap: 1 }}
                  >
                    <span>
                      {getFileTypeName(currentExtension)} (.{currentExtension})
                    </span>
                    <Box
                      component="span"
                      sx={{
                        width: '4px',
                        height: '4px',
                        borderRadius: '50%',
                        bgcolor: 'text.disabled',
                      }}
                    />
                    <span>{formatFileSize(record.fileRecord.sizeInBytes)}</span>
                  </Typography>
                </Box>
              </Box>

              {/* File type restriction notice for existing files */}
              {currentExtension && (
                <Alert
                  severity="info"
                  variant="outlined"
                  icon={<Icon icon={infoIcon} style={{ fontSize: '20px' }} />}
                  sx={{
                    mb: 2,
                    borderRadius: '10px',
                    '& .MuiAlert-message': {
                      width: '100%',
                    },
                    border: `1px solid ${alpha('#1976d2', 0.3)}`,
                    bgcolor: alpha('#1976d2', 0.02),
                  }}
                >
                  <Typography variant="body2">
                    You can only upload {getFileTypeName(currentExtension)} files (.
                    {currentExtension}) to replace this document.
                  </Typography>
                </Alert>
              )}
            </>
          )}

          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
            {existingFile ? 'Replace File' : 'Upload File'}
          </Typography>

          <Box
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            sx={{
              border: '2px dashed',
              borderColor: dragActive ? 'primary.main' : alpha('#000', 0.12),
              borderRadius: '12px',
              p: 1,
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'all 0.2s ease-in-out',
              bgcolor: dragActive ? alpha('#1976d2', 0.04) : alpha('#000', 0.01),
              '&:hover': {
                borderColor: 'primary.main',
                bgcolor: alpha('#1976d2', 0.04),
              },
            }}
          >
            {/* Hide input and control it programmatically */}
            <input
              type="file"
              id="fileInput"
              ref={fileInputRef}
              style={{ display: 'none' }}
              accept={existingFile && currentExtension ? `.${currentExtension}` : undefined}
              onChange={handleFileChange}
            />

            <div style={{ display: 'block', cursor: 'pointer' }}>
              <Icon
                icon={cloudIcon}
                style={{
                  fontSize: '48px',
                  marginBottom: '16px',
                  color: dragActive ? '#1976d2' : '#757575',
                }}
              />
              <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 500 }}>
                {dragActive
                  ? 'Drop file here'
                  : existingFile
                    ? 'Drag and drop to replace file'
                    : 'Drag and drop file here'}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                or
              </Typography>
              <Button
                variant="contained"
                size="medium"
                disableElevation
                onClick={handleBrowseClick}
                sx={{
                  borderRadius: '8px',
                  px: 3,
                  textTransform: 'none',
                  fontWeight: 500,
                }}
              >
                Browse Files
              </Button>
              <Box sx={{ mt: 1 }}>
                <Typography variant="caption" display="block" color="text.secondary">
                  {existingFile && currentExtension
                    ? `Only .${currentExtension} files are accepted`
                    : 'All file types are accepted'}{' '}
                  • Maximum size: 30MB
                </Typography>
              </Box>
            </div>
          </Box>

          {file && (
            <Box
              sx={{
                mt: 2,
                p: 1.5,
                borderRadius: '12px',
                bgcolor: alpha('#1976d2', 0.04),
                border: `1px solid ${alpha('#1976d2', 0.2)}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, overflow: 'hidden' }}>
                <Icon
                  icon={getFileIcon(file.name.split('.').pop() || '')}
                  style={{
                    fontSize: '28px',
                    color: getFileIconColor(file.name.split('.').pop() || ''),
                    flexShrink: 0,
                  }}
                />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" noWrap title={file.name} fontWeight={500}>
                    {file.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {formatFileSize(file.size)}
                  </Typography>
                </Box>
              </Box>
              <IconButton
                size="small"
                onClick={() => {
                  setFile(null);
                  setFileError(null);
                  // Reset the file input when removing a file
                  if (fileInputRef.current) {
                    fileInputRef.current.value = '';
                  }
                }}
                sx={{
                  ml: 1,
                  flexShrink: 0,
                  bgcolor: alpha('#000', 0.05),
                  '&:hover': {
                    bgcolor: alpha('#000', 0.1),
                  },
                }}
              >
                <Icon icon={closeIcon} fontSize={18} />
              </IconButton>
            </Box>
          )}
        </DialogContent>
      )}

      <Divider />

      <DialogActions sx={{ px: 3, py: 2.5 }}>
        <Button
          onClick={onClose}
          variant="outlined"
          size="medium"
          disabled={uploading}
          sx={{
            borderRadius: '8px',
            textTransform: 'none',
            fontWeight: 500,
          }}
        >
          Cancel
        </Button>
        <Button
          onClick={handleUpload}
          variant="contained"
          size="medium"
          disableElevation
          disabled={!canUpload || uploading || !!fileError}
          sx={{
            borderRadius: '8px',
            textTransform: 'none',
            fontWeight: 500,
            px: 3,
          }}
        >
          {existingFile ? (file ? 'Replace' : nameChanged ? 'Update' : 'Save') : 'Upload'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default SingleFileUploadDialog;
