// components/upload/simplified-upload-manager.tsx
import { Icon } from '@iconify/react';
import { useDropzone } from 'react-dropzone';
import React, { useRef, useState } from 'react';
import cloudIcon from '@iconify-icons/mdi/cloud-upload';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import filePlusIcon from '@iconify-icons/mdi/file-plus-outline';
import trashCanIcon from '@iconify-icons/mdi/trash-can-outline';
import folderUploadIcon from '@iconify-icons/mdi/folder-upload-outline';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';

import {
  Box,
  Fade,
  Stack,
  Alert,
  alpha,
  Paper,
  Button,
  Dialog,
  useTheme,
  Typography,
  AlertTitle,
  DialogTitle,
  DialogContent,
  DialogActions,
  LinearProgress,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

interface FileWithPath extends File {
  webkitRelativePath: string;
  lastModified: number;
}

interface UploadManagerProps {
  open: boolean;
  onClose: () => void;
  knowledgeBaseId: string | null | undefined;
  folderId: string | null | undefined;
  onUploadSuccess: (message?: string) => Promise<void>;
}

interface ProcessedFile {
  file: FileWithPath;
  path: string;
  lastModified: number;
}

interface FolderInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  webkitdirectory?: string;
  directory?: string;
}

const FolderInput = React.forwardRef<HTMLInputElement, FolderInputProps>((props, ref) => (
  <input {...props} ref={ref} />
));

// Maximum file size: 30MB in bytes
const MAX_FILE_SIZE = 30 * 1024 * 1024;

export default function UploadManager({
  open,
  onClose,
  knowledgeBaseId,
  folderId,
  onUploadSuccess,
}: UploadManagerProps) {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const [files, setFiles] = useState<ProcessedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<{ show: boolean; message: string }>({
    show: false,
    message: '',
  });
  const [fileSizeError, setFileSizeError] = useState<{ show: boolean; files: FileWithPath[] }>({
    show: false,
    files: [],
  });

  // Refs for file inputs
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  const validateFileSize = (filesToCheck: FileWithPath[]) => {
    const oversizedFiles = filesToCheck.filter((file) => file.size > MAX_FILE_SIZE);
    return {
      valid: oversizedFiles.length === 0,
      oversizedFiles,
    };
  };

  // Simplified file processing - just extract path and metadata
  const processFiles = (acceptedFiles: FileWithPath[]): ProcessedFile[] =>
    acceptedFiles
      .filter((file) => file.name !== '.DS_Store' && !file.name.startsWith('.'))
      .map((file) => {
        // Use webkitRelativePath if available (folder upload), otherwise use file name
        const path = file.webkitRelativePath || file.name;

        return {
          file,
          path,
          lastModified: file.lastModified || Date.now(),
        };
      });

  const onDrop = (acceptedFiles: FileWithPath[]) => {
    const processedFiles = processFiles(acceptedFiles);
    setFiles(processedFiles);

    // Validate file sizes
    const { valid, oversizedFiles } = validateFileSize(acceptedFiles);
    if (!valid) {
      setFileSizeError({ show: true, files: oversizedFiles });
    } else {
      setFileSizeError({ show: false, files: [] });
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    multiple: true,
  });

  const handleFileSelectClick = () => {
    fileInputRef.current?.click();
  };

  const handleFolderSelectClick = () => {
    folderInputRef.current?.click();
  };

  const handleFileInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) {
      const fileList = Array.from(event.target.files) as FileWithPath[];
      onDrop(fileList);
      event.target.value = '';
    }
  };

  const handleFolderInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) {
      const fileList = Array.from(event.target.files) as FileWithPath[];
      onDrop(fileList);
      event.target.value = '';
    }
  };

  const handleUpload = async () => {
    if (files.length === 0) {
      setUploadError({ show: true, message: 'Please select at least one file to upload.' });
      return;
    }
    if (!knowledgeBaseId) {
      setUploadError({ show: true, message: 'Knowledge base id missing. Please refresh' });
      return;
    }

    const { valid, oversizedFiles } = validateFileSize(files.map((f) => f.file));
    if (!valid) {
      setFileSizeError({ show: true, files: oversizedFiles });
      return;
    }

    setUploading(true);
    setUploadError({ show: false, message: '' });
    setUploadProgress(0);

    try {
      const formData = new FormData();

      // Add knowledge base ID
      formData.append('kb_id', knowledgeBaseId);

      // Add files with their metadata
      files.forEach((processedFile, index) => {
        // Add the actual file
        formData.append('files', processedFile.file);

        // Add metadata for each file
        formData.append(`file_paths`, processedFile.path);
        formData.append(`last_modified`, processedFile.lastModified.toString());
      });
      const url = folderId
        ? `/api/v1/knowledgebase/${knowledgeBaseId}/folder/${folderId}/upload`
        : `/api/v1/knowledgebase/${knowledgeBaseId}/upload`;
      // Track upload progress
      const response = await axios.post(url, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setUploadProgress(percentCompleted);
          }
        },
      });

      setUploadProgress(100);

      // Show success message
      const uploadResult = response.data;
      const successMessage =
        uploadResult.message ||
        `Successfully uploaded ${files.length} file${files.length > 1 ? 's' : ''}.`;

      onUploadSuccess(successMessage);
      handleClose();
    } catch (error: any) {
      setUploadError({
        show: true,
        message:
          error.response?.data?.message ||
          error.message ||
          'Failed to upload files. Please try again.',
      });
    } finally {
      setUploading(false);
    }
  };

  const handleClose = () => {
    if (!uploading) {
      setFiles([]);
      setUploadError({ show: false, message: '' });
      setFileSizeError({ show: false, files: [] });
      setUploadProgress(0);
      onClose();
    }
  };

  // Group files by folders for display
  const groupFilesByFolder = (fileList: ProcessedFile[]) => {
    const rootFiles: ProcessedFile[] = [];
    const folderGroups: Record<string, ProcessedFile[]> = {};

    fileList.forEach((file) => {
      if (file.path.includes('/')) {
        // File is in a folder
        const folderPath = file.path.substring(0, file.path.lastIndexOf('/'));
        if (!folderGroups[folderPath]) {
          folderGroups[folderPath] = [];
        }
        folderGroups[folderPath].push(file);
      } else {
        // Root file
        rootFiles.push(file);
      }
    });

    return { rootFiles, folderGroups };
  };

  const renderFilesList = () => {
    if (files.length === 0) return null;

    const { rootFiles, folderGroups } = groupFilesByFolder(files);
    const folderCount = Object.keys(folderGroups).length;

    return (
      <Paper
        variant="outlined"
        sx={{
          mt: 3,
          p: 2,
          borderRadius: '12px',
          bgcolor: alpha('#000', 0.01),
          border: `1px solid ${alpha('#000', 0.08)}`,
        }}
      >
        <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
          <Typography variant="subtitle1" fontWeight={500}>
            {folderCount > 0
              ? `Upload Contents (${folderCount} folders, ${files.length} files)`
              : `Selected Files (${files.length})`}
          </Typography>
          <Button
            size="small"
            color="error"
            variant="text"
            onClick={() => setFiles([])}
            startIcon={<Icon icon={trashCanIcon} />}
            sx={{ borderRadius: 1, textTransform: 'none', fontSize: '0.75rem', fontWeight: 500 }}
          >
            Clear All
          </Button>
        </Stack>

        <Box
          sx={{
            maxHeight: '250px',
            overflow: 'auto',
            pr: 0.5,
            '&::-webkit-scrollbar': { width: '4px' },
            '&::-webkit-scrollbar-thumb': {
              backgroundColor: alpha(theme.palette.common.black, 0.2),
              borderRadius: '4px',
            },
          }}
        >
          {/* Root files */}
          {rootFiles.map((processedFile, index) => (
            <Stack
              key={`root-${index}`}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                mb: 0.5,
                p: 1,
                borderRadius: '6px',
                '&:hover': { bgcolor: 'background.paper' },
              }}
            >
              <Icon
                icon={fileDocumentOutlineIcon}
                style={{
                  fontSize: '16px',
                  color: '#1976d2',
                  flexShrink: 0,
                }}
              />
              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography
                  variant="body2"
                  noWrap
                  title={processedFile.file.name}
                  sx={{ fontSize: '0.8rem' }}
                >
                  {processedFile.file.name}
                </Typography>
              </Box>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
                {formatFileSize(processedFile.file.size)}
              </Typography>
            </Stack>
          ))}

          {/* Folders and their files */}
          {Object.entries(folderGroups).map(([folderPath, folderFiles]) => (
            <Box key={folderPath} sx={{ mb: 1 }}>
              {/* Folder header */}
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{
                  mb: 0.5,
                  p: 1,
                  borderRadius: '6px',
                  bgcolor: alpha('#ffa726', 0.1),
                }}
              >
                <Icon
                  icon={folderIcon}
                  style={{
                    fontSize: '16px',
                    color: '#ffa726',
                    flexShrink: 0,
                  }}
                />
                <Typography variant="body2" fontWeight={500} sx={{ fontSize: '0.8rem' }}>
                  {folderPath}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
                  {folderFiles.length} files
                </Typography>
              </Stack>

              {/* Files in folder (show first 5) */}
              {folderFiles.slice(0, 5).map((processedFile, index) => (
                <Stack
                  key={`${folderPath}-${index}`}
                  direction="row"
                  alignItems="center"
                  spacing={1.5}
                  sx={{
                    mb: 0.5,
                    p: 1,
                    pl: 3,
                    borderRadius: '6px',
                    '&:hover': { bgcolor: 'background.paper' },
                  }}
                >
                  <Icon
                    icon={fileDocumentOutlineIcon}
                    style={{
                      fontSize: '14px',
                      color: '#1976d2',
                      flexShrink: 0,
                    }}
                  />
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Typography
                      variant="body2"
                      noWrap
                      title={processedFile.file.name}
                      sx={{ fontSize: '0.75rem' }}
                    >
                      {processedFile.file.name}
                    </Typography>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                    {formatFileSize(processedFile.file.size)}
                  </Typography>
                </Stack>
              ))}

              {folderFiles.length > 5 && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ pl: 3, fontStyle: 'italic', fontSize: '0.7rem' }}
                >
                  ... and {folderFiles.length - 5} more files
                </Typography>
              )}
            </Box>
          ))}
        </Box>
      </Paper>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      TransitionComponent={Fade}
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(5px)',
          backgroundColor: alpha(theme.palette.common.black, isDark ? 0.7 : 0.5),
        },
      }}
    >
      <DialogTitle
        sx={{ px: 3, py: 2.5, borderBottom: '1px solid', borderColor: alpha('#000', 0.08) }}
      >
        <Typography variant="h6" fontWeight={600}>
          Upload Files & Folders
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
            py: 4,
          }}
        >
          <Box sx={{ width: '100%', mb: 2 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>
              Uploading files... {Math.round(uploadProgress)}%
            </Typography>
            <LinearProgress
              variant="determinate"
              value={uploadProgress}
              sx={{ height: 8, borderRadius: 4, '& .MuiLinearProgress-bar': { borderRadius: 4 } }}
            />
          </Box>
          <CircularProgress size={40} thickness={4} sx={{ mb: 3 }} />
          <Typography variant="subtitle1" fontWeight={500}>
            Processing your upload...
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Please wait, this may take a few moments.
          </Typography>
        </DialogContent>
      ) : (
        <>
          <DialogContent sx={{ px: 3, py: 3 }}>
            {/* Error Alerts */}
            {fileSizeError.show && (
              <Box sx={{ mb: 3 }}>
                <Alert
                  severity="error"
                  onClose={() => setFileSizeError({ show: false, files: [] })}
                  sx={{ borderRadius: '8px' }}
                >
                  <AlertTitle>File size exceeds limit</AlertTitle>
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    The following file(s) exceed the maximum upload size of 30MB:
                  </Typography>
                  <Box
                    sx={{
                      maxHeight: '100px',
                      overflowY: 'auto',
                      bgcolor: alpha('#f44336', 0.05),
                      borderRadius: '4px',
                      p: 1,
                    }}
                  >
                    {fileSizeError.files.map((file, index) => (
                      <Typography key={index} variant="body2" sx={{ mb: 0.5 }}>
                        • {file.webkitRelativePath || file.name} ({formatFileSize(file.size)})
                      </Typography>
                    ))}
                  </Box>
                </Alert>
              </Box>
            )}

            {uploadError.show && (
              <Box sx={{ mb: 3 }}>
                <Alert
                  severity="error"
                  onClose={() => setUploadError({ show: false, message: '' })}
                  sx={{ borderRadius: '8px' }}
                >
                  <AlertTitle>Upload Failed</AlertTitle>
                  <Typography variant="body2">{uploadError.message}</Typography>
                </Alert>
              </Box>
            )}

            {/* Hidden inputs */}
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileInputChange}
              style={{ display: 'none' }}
              multiple
            />
            <FolderInput
              type="file"
              ref={folderInputRef}
              onChange={handleFolderInputChange}
              style={{ display: 'none' }}
              webkitdirectory="true"
              directory="true"
              multiple
            />

            {/* Dropzone */}
            <Box
              {...getRootProps()}
              sx={{
                border: '1px dashed',
                borderColor: isDragActive
                  ? theme.palette.primary.main
                  : alpha(theme.palette.grey[500], 0.3),
                borderRadius: 1,
                p: 3,
                textAlign: 'center',
                cursor: 'pointer',
                mb: 3,
                transition: 'all 0.2s ease-in-out',
                bgcolor: isDragActive ? alpha(theme.palette.primary.main, 0.04) : 'transparent',
                '&:hover': {
                  borderColor: theme.palette.primary.main,
                  bgcolor: alpha(theme.palette.primary.main, 0.04),
                },
              }}
            >
              <input {...getInputProps()} />
              <Icon
                icon={cloudIcon}
                style={{
                  fontSize: '36px',
                  marginBottom: '12px',
                  color: isDragActive ? theme.palette.primary.main : theme.palette.grey[600],
                }}
              />
              <Typography variant="h6" sx={{ mb: 1, fontWeight: 500 }}>
                {isDragActive ? 'Drop files or folders here...' : 'Drag and drop files or a folder'}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                or use the buttons below
              </Typography>

              <Stack direction="row" spacing={2} justifyContent="center">
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<Icon icon={filePlusIcon} />}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleFileSelectClick();
                  }}
                  sx={{ borderRadius: 1, textTransform: 'none' }}
                >
                  Select Files
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<Icon icon={folderUploadIcon} />}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleFolderSelectClick();
                  }}
                  sx={{ borderRadius: 1, textTransform: 'none' }}
                >
                  Select Folder
                </Button>
              </Stack>

              <Typography variant="caption" display="block" sx={{ mt: 2, color: 'text.secondary' }}>
                Maximum file size: 30MB per file
              </Typography>
            </Box>

            {/* Files List */}
            {renderFilesList()}
          </DialogContent>

          <DialogActions
            sx={{
              px: 3,
              py: 2,
              borderTop: '1px solid',
              borderColor: alpha(theme.palette.divider, 0.08),
            }}
          >
            <Button
              onClick={handleClose}
              variant="text"
              color="inherit"
              sx={{ borderRadius: 1, textTransform: 'none', fontWeight: 500 }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleUpload}
              variant="contained"
              disabled={files.length === 0}
              startIcon={<Icon icon={cloudIcon} fontSize={18} />}
              sx={{ borderRadius: 1, textTransform: 'none', fontWeight: 500, boxShadow: 'none' }}
            >
              Upload {files.length > 0 && `(${files.length})`}
            </Button>
          </DialogActions>
        </>
      )}
    </Dialog>
  );
}
