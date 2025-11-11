// components/upload/simplified-upload-manager.tsx
import { Icon } from '@iconify/react';
import { useDropzone } from 'react-dropzone';
import React, { useRef, useState, useMemo, useEffect } from 'react';
import cloudIcon from '@iconify-icons/mdi/cloud-upload';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import filePlusIcon from '@iconify-icons/mdi/file-plus-outline';
import closeIcon from '@iconify-icons/mdi/close';
import folderUploadIcon from '@iconify-icons/mdi/folder-upload-outline';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import checkCircleIcon from '@iconify-icons/mdi/check-circle-outline';

import {
  Box,
  Fade,
  Stack,
  alpha,
  Button,
  Dialog,
  useTheme,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  LinearProgress,
  CircularProgress,
  IconButton,
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
  isOversized: boolean;
}

interface FolderInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  webkitdirectory?: string;
  directory?: string;
}

const FolderInput = React.forwardRef<HTMLInputElement, FolderInputProps>((props, ref) => (
  <input {...props} ref={ref} />
));

// Default maximum file size: 30MB in bytes (overridden by platform settings)
const DEFAULT_MAX_FILE_SIZE = 30 * 1024 * 1024;

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
  const [uploadStatus, setUploadStatus] = useState<{
    currentBatch: number;
    totalBatches: number;
    uploadedFiles: number;
    totalFiles: number;
  } | null>(null);
  const [uploadError, setUploadError] = useState<{ show: boolean; message: string }>({
    show: false,
    message: '',
  });
  const [maxFileSize, setMaxFileSize] = useState<number>(DEFAULT_MAX_FILE_SIZE);

  // Load platform settings for max upload size
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res = await axios.get('/api/v1/configurationManager/platform/settings');
        const value = Number(res.data?.fileUploadMaxSizeBytes);
        if (mounted && Number.isFinite(value) && value > 0) {
          setMaxFileSize(value);
        }
      } catch (e) {
        // Keep default, but log for visibility
        // eslint-disable-next-line no-console
        console.warn('[UploadManager] Failed to fetch platform settings; using defaults', e);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

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

  // Memoized calculations for file statistics
  const fileStats = useMemo(() => {
    const oversizedFiles = files.filter((pf) => pf.isOversized);
    const validFiles = files.filter((pf) => !pf.isOversized);
    return {
      total: files.length,
      oversized: oversizedFiles.length,
      valid: validFiles.length,
      oversizedFiles,
      validFiles,
    };
  }, [files]);

  const removeFileFromSelection = (target: ProcessedFile) => {
    const targetKey = `${target.path}-${target.file.size}-${target.lastModified}`;
    setFiles((prev) =>
      prev.filter((pf) => `${pf.path}-${pf.file.size}-${pf.lastModified}` !== targetKey)
    );
  };

  const removeAllOversizedFiles = () => {
    setFiles((prev) => prev.filter((pf) => !pf.isOversized));
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
          isOversized: file.size > maxFileSize,
        };
      });

  const onDrop = (acceptedFiles: FileWithPath[]) => {
    const processedFiles = processFiles(acceptedFiles);

    // Append new files to existing ones instead of replacing
    setFiles((prevFiles) => {
      // Create a map of existing files by path to avoid duplicates
      const existingFileMap = new Map(
        prevFiles.map((pf) => [`${pf.path}-${pf.file.size}-${pf.lastModified}`, pf])
      );

      // Add new files, skipping duplicates
      processedFiles.forEach((pf) => {
        const key = `${pf.path}-${pf.file.size}-${pf.lastModified}`;
        if (!existingFileMap.has(key)) {
          existingFileMap.set(key, pf);
        }
      });

      return Array.from(existingFileMap.values());
    });

    setUploadError({ show: false, message: '' });
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

  // Batch upload configuration (discover from backend limits endpoint)
  const [maxFilesPerRequest, setMaxFilesPerRequest] = useState<number>(1000);
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const resp = await axios.get<{ maxFilesPerRequest: number; maxFileSizeBytes?: number }>(
          '/api/v1/knowledgebase/limits'
        );
        const n = Number(resp.data?.maxFilesPerRequest);
        if (mounted && Number.isFinite(n) && n > 0) {
          setMaxFilesPerRequest(n);
        }
        // Optionally update maxFileSize from server if provided
        const s = Number(resp.data?.maxFileSizeBytes);
        if (mounted && Number.isFinite(s) && s > 0) {
          setMaxFileSize(s);
        }
      } catch (_e) {
        // fallback to defaults silently
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);
  const BATCH_SIZE = 10; // safe per-request files count
  const CONCURRENCY = 5; // parallel requests

  const chunkArray = <T,>(arr: T[], size: number): T[][] => {
    if (size <= 0) return [arr];
    const chunks: T[][] = [];
    for (let i = 0; i < arr.length; i += size) chunks.push(arr.slice(i, i + size));
    return chunks;
  };

  const buildFormDataForBatch = (batchFiles: ProcessedFile[], kbId: string): FormData => {
    const formData = new FormData();
    formData.append('kb_id', kbId);
    batchFiles.forEach((processedFile) => {
      formData.append('files', processedFile.file);
      formData.append('file_paths', processedFile.path);
      formData.append('last_modified', processedFile.lastModified.toString());
    });
    return formData;
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

    if (fileStats.oversized > 0) {
      setUploadError({
        show: true,
        message: `Cannot upload: ${fileStats.oversized} file(s) exceed the ${formatFileSize(maxFileSize)} limit. Please remove them to continue.`,
      });
      return;
    }

    setUploading(true);
    setUploadError({ show: false, message: '' });
    setUploadProgress(0);
    setUploadStatus(null);

    try {
      // Prepare batches
      const valid = fileStats.validFiles;
      const perRequest = Math.min(BATCH_SIZE, maxFilesPerRequest);
      const batches = chunkArray(valid, perRequest);
      const totalFiles = valid.length;
      const totalBatches = batches.length;

      // Initialize status
      setUploadStatus({
        currentBatch: 0,
        totalBatches,
        uploadedFiles: 0,
        totalFiles,
      });

      const url = folderId
        ? `/api/v1/knowledgebase/${knowledgeBaseId}/folder/${folderId}/upload`
        : `/api/v1/knowledgebase/${knowledgeBaseId}/upload`;

      // Track batch progress: completed batches and in-progress batches
      const completedBatches = new Set<number>();
      const batchFileCounts = batches.map((b) => b.length);
      const batchProgress: Record<number, number> = {}; // batch index -> progress 0-100

      const updateProgress = () => {
        // Calculate files from completed batches
        const completedFiles = Array.from(completedBatches).reduce(
          (sum, idx) => sum + batchFileCounts[idx],
          0
        );

        // Calculate files from in-progress batches
        const inProgressFiles = Object.entries(batchProgress).reduce((sum, [idx, progress]) => {
          if (!completedBatches.has(Number(idx))) {
            return sum + (batchFileCounts[Number(idx)] * progress) / 100;
          }
          return sum;
        }, 0);

        const totalProgress = completedFiles + inProgressFiles;
        const overallProgress = Math.min(100, Math.round((totalProgress / totalFiles) * 100));
        setUploadProgress(overallProgress);

        // Find the highest batch index being processed
        const activeBatches = Object.keys(batchProgress)
          .map(Number)
          .filter((idx) => !completedBatches.has(idx));
        const maxActiveBatch = activeBatches.length > 0 ? Math.max(...activeBatches) : -1;

        setUploadStatus({
          currentBatch: maxActiveBatch >= 0 ? maxActiveBatch + 1 : totalBatches,
          totalBatches,
          uploadedFiles: Math.floor(totalProgress),
          totalFiles,
        });
      };

      // Concurrency-controlled workers
      let nextIndex = 0;
      const worker = async () => {
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const idx = nextIndex;
          nextIndex += 1;
          if (idx >= batches.length) break;
          const batch = batches[idx];
          const formData = buildFormDataForBatch(batch, knowledgeBaseId);

          try {
            // eslint-disable-next-line no-await-in-loop
            await axios.post(url, formData, {
              headers: { 'Content-Type': 'multipart/form-data' },
              onUploadProgress: (progressEvent) => {
                if (progressEvent.total) {
                  const batchProgressPercent = Math.round(
                    (progressEvent.loaded * 100) / progressEvent.total
                  );
                  batchProgress[idx] = batchProgressPercent;
                  updateProgress();
                }
              },
            });
            completedBatches.add(idx);
            delete batchProgress[idx];
            updateProgress();
          } catch (err) {
            delete batchProgress[idx];
            throw err;
          }
        }
      };

      const workers = Array.from({ length: Math.min(CONCURRENCY, batches.length) }, () => worker());
      await Promise.all(workers);

      setUploadProgress(100);
      setUploadStatus({
        currentBatch: totalBatches,
        totalBatches,
        uploadedFiles: totalFiles,
        totalFiles,
      });

      const successMessage = `Successfully uploaded ${totalFiles} file${totalFiles > 1 ? 's' : ''}.`;
      await onUploadSuccess(successMessage);
      handleClose();
    } catch (error: any) {
      // Use processed error message from axios interceptor if available
      const errorMessage =
        error?.message ||
        error?.response?.data?.message ||
        'Failed to upload files. Please try again.';
      setUploadError({
        show: true,
        message: errorMessage,
      });
      setUploadStatus(null);
    } finally {
      setUploading(false);
    }
  };

  const handleClose = () => {
    if (!uploading) {
      setFiles([]);
      setUploadError({ show: false, message: '' });
      setUploadProgress(0);
      setUploadStatus(null);
      onClose();
    }
  };

  // Group files by folders for display
  const groupFilesByFolder = (fileList: ProcessedFile[]) => {
    const rootFiles: ProcessedFile[] = [];
    const folderGroups: Record<
      string,
      { files: ProcessedFile[]; oversizedCount: number; validCount: number }
    > = {};

    fileList.forEach((file) => {
      if (file.path.includes('/')) {
        // File is in a folder
        const folderPath = file.path.substring(0, file.path.lastIndexOf('/'));
        if (!folderGroups[folderPath]) {
          folderGroups[folderPath] = { files: [], oversizedCount: 0, validCount: 0 };
        }
        folderGroups[folderPath].files.push(file);
        if (file.isOversized) {
          folderGroups[folderPath].oversizedCount += 1;
        } else {
          folderGroups[folderPath].validCount += 1;
        }
      } else {
        // Root file
        rootFiles.push(file);
      }
    });

    return { rootFiles, folderGroups };
  };

  const renderFileItem = (processedFile: ProcessedFile, indent: boolean = false) => (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        py: 1.25,
        px: indent ? 2.5 : 1.5,
        borderRadius: '6px',
        transition: 'all 0.15s ease',
        border: '1px solid transparent',
        ...(processedFile.isOversized && {
          bgcolor: alpha(theme.palette.error.main, 0.04),
          borderColor: alpha(theme.palette.error.main, 0.15),
        }),
        '&:hover': {
          bgcolor: processedFile.isOversized
            ? alpha(theme.palette.error.main, 0.08)
            : alpha(theme.palette.grey[500], 0.04),
          '& .remove-btn': {
            opacity: 1,
          },
        },
      }}
    >
      <Icon
        icon={processedFile.isOversized ? alertCircleIcon : fileDocumentOutlineIcon}
        style={{
          fontSize: '18px',
          color: processedFile.isOversized
            ? theme.palette.error.main
            : theme.palette.text.secondary,
          flexShrink: 0,
        }}
      />

      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          variant="body2"
          noWrap
          title={processedFile.file.name}
          sx={{
            fontSize: '0.875rem',
            fontWeight: 500,
            color: processedFile.isOversized ? 'error.main' : 'text.primary',
          }}
        >
          {processedFile.file.name}
        </Typography>
        <Typography
          variant="caption"
          sx={{
            fontSize: '0.75rem',
            color: processedFile.isOversized ? 'error.main' : 'text.secondary',
            fontWeight: 400,
          }}
        >
          {formatFileSize(processedFile.file.size)}
          {processedFile.isOversized && ` • Exceeds ${formatFileSize(maxFileSize)} limit`}
        </Typography>
      </Box>

      <IconButton
        className="remove-btn"
        size="small"
        onClick={() => removeFileFromSelection(processedFile)}
        sx={{
          opacity: 0.6,
          transition: 'opacity 0.15s ease',
          color: 'text.secondary',
          '&:hover': {
            color: 'error.main',
            bgcolor: alpha(theme.palette.error.main, 0.08),
          },
        }}
      >
        <Icon icon={closeIcon} style={{ fontSize: '18px' }} />
      </IconButton>
    </Box>
  );

  const renderFilesList = () => {
    if (files.length === 0) return null;

    const { rootFiles, folderGroups } = groupFilesByFolder(files);
    const folderCount = Object.keys(folderGroups).length;

    return (
      <Box sx={{ mt: 3 }}>
        {/* Stats Header */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{
            mb: 2,
            pb: 1.5,
            borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={2}>
            <Typography variant="body2" fontWeight={600} color="text.primary">
              {fileStats.total} selected
            </Typography>

            <Stack direction="row" spacing={1}>
              {fileStats.valid > 0 && (
                <Box
                  sx={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 0.5,
                    px: 1,
                    py: 0.25,
                    borderRadius: '4px',
                    bgcolor: alpha(theme.palette.success.main, 0.08),
                  }}
                >
                  <Icon
                    icon={checkCircleIcon}
                    style={{ fontSize: '14px', color: theme.palette.success.main }}
                  />
                  <Typography
                    variant="caption"
                    sx={{ fontSize: '0.75rem', fontWeight: 600, color: 'success.main' }}
                  >
                    {fileStats.valid} ready
                  </Typography>
                </Box>
              )}

              {fileStats.oversized > 0 && (
                <Box
                  sx={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 0.5,
                    px: 1,
                    py: 0.25,
                    borderRadius: '4px',
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                  }}
                >
                  <Icon
                    icon={alertCircleIcon}
                    style={{ fontSize: '14px', color: theme.palette.error.main }}
                  />
                  <Typography
                    variant="caption"
                    sx={{ fontSize: '0.75rem', fontWeight: 600, color: 'error.main' }}
                  >
                    {fileStats.oversized} oversized
                  </Typography>
                </Box>
              )}
            </Stack>
          </Stack>

          <Stack direction="row" spacing={1}>
            {fileStats.oversized > 0 && (
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={removeAllOversizedFiles}
                sx={{
                  textTransform: 'none',
                  fontSize: '0.8125rem',
                  fontWeight: 500,
                  borderRadius: '6px',
                  minWidth: 'auto',
                  px: 1.5,
                }}
              >
                Remove oversized
              </Button>
            )}
            <Button
              size="small"
              variant="text"
              color="inherit"
              onClick={() => setFiles([])}
              sx={{
                textTransform: 'none',
                fontSize: '0.8125rem',
                fontWeight: 500,
                borderRadius: '6px',
                color: 'text.secondary',
                minWidth: 'auto',
                px: 1.5,
                '&:hover': {
                  bgcolor: alpha(theme.palette.grey[500], 0.08),
                },
              }}
            >
              Clear all
            </Button>
          </Stack>
        </Stack>

        {/* File List */}
        <Box
          sx={{
            maxHeight: '380px',
            overflow: 'auto',
            pr: 0.5,
            '&::-webkit-scrollbar': {
              width: '6px',
            },
            '&::-webkit-scrollbar-track': {
              backgroundColor: 'transparent',
            },
            '&::-webkit-scrollbar-thumb': {
              backgroundColor: alpha(theme.palette.grey[500], 0.2),
              borderRadius: '3px',
              '&:hover': {
                backgroundColor: alpha(theme.palette.grey[500], 0.3),
              },
            },
          }}
        >
          {/* Root files */}
          {rootFiles.length > 0 && (
            <Box sx={{ mb: 2 }}>
              {rootFiles.map((processedFile, index) => (
                <Box key={`root-${index}`}>{renderFileItem(processedFile, false)}</Box>
              ))}
            </Box>
          )}

          {/* Folders and their files */}
          {Object.entries(folderGroups).map(([folderPath, folderData]) => (
            <Box key={folderPath} sx={{ mb: 2.5 }}>
              {/* Folder header */}
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{
                  mb: 1,
                  py: 1,
                  px: 1.5,
                  borderRadius: '6px',
                  bgcolor: alpha(theme.palette.grey[500], 0.04),
                }}
              >
                <Icon
                  icon={folderIcon}
                  style={{
                    fontSize: '18px',
                    color: theme.palette.text.secondary,
                    flexShrink: 0,
                  }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    variant="body2"
                    fontWeight={600}
                    noWrap
                    sx={{ fontSize: '0.875rem', color: 'text.primary' }}
                  >
                    {folderPath}
                  </Typography>
                </Box>
                <Typography
                  variant="caption"
                  sx={{
                    fontSize: '0.75rem',
                    color: 'text.secondary',
                    fontWeight: 500,
                  }}
                >
                  {folderData.files.length} {folderData.files.length === 1 ? 'file' : 'files'}
                </Typography>
              </Stack>

              {/* Files in folder */}
              <Box>
                {[...folderData.files]
                  .sort((a, b) => (b.isOversized ? 1 : 0) - (a.isOversized ? 1 : 0))
                  .map((processedFile, index) => (
                    <Box key={`${folderPath}-${index}`}>{renderFileItem(processedFile, true)}</Box>
                  ))}
              </Box>
            </Box>
          ))}
        </Box>

        {/* Warning Banner */}
        {fileStats.oversized > 0 && (
          <Box
            sx={{
              mt: 2,
              p: 2,
              borderRadius: '8px',
              bgcolor: alpha(theme.palette.error.main, 0.04),
              border: `1px solid ${alpha(theme.palette.error.main, 0.15)}`,
            }}
          >
            <Stack direction="row" spacing={1.5} alignItems="flex-start">
              <Icon
                icon={alertCircleIcon}
                style={{
                  fontSize: '20px',
                  color: theme.palette.error.main,
                  flexShrink: 0,
                  marginTop: '1px',
                }}
              />
              <Box sx={{ flex: 1 }}>
                <Typography
                  variant="body2"
                  fontWeight={600}
                  color="error.main"
                  sx={{ mb: 0.5, fontSize: '0.875rem' }}
                >
                  {fileStats.oversized}{' '}
                  {fileStats.oversized === 1 ? 'file exceeds' : 'files exceed'} size limit
                </Typography>
                <Typography variant="body2" sx={{ fontSize: '0.8125rem', color: 'text.secondary' }}>
                  Remove oversized files to continue with the remaining {fileStats.valid}{' '}
                  {fileStats.valid === 1 ? 'file' : 'files'}.
                </Typography>
              </Box>
              <Button
                size="small"
                variant="contained"
                color="error"
                onClick={removeAllOversizedFiles}
                disableElevation
                sx={{
                  textTransform: 'none',
                  fontSize: '0.8125rem',
                  fontWeight: 500,
                  borderRadius: '6px',
                  whiteSpace: 'nowrap',
                }}
              >
                Remove all
              </Button>
            </Stack>
          </Box>
        )}
      </Box>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      TransitionComponent={Fade}
      PaperProps={{
        elevation: 0,
        sx: {
          borderRadius: '12px',
          border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
        },
      }}
      BackdropProps={{
        sx: {
          backdropFilter: 'blur(8px)',
          backgroundColor: alpha(theme.palette.common.black, isDark ? 0.6 : 0.4),
        },
      }}
    >
      <DialogTitle
        sx={{
          px: 3,
          py: 2.5,
          borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
        }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Box>
            <Typography variant="h6" fontWeight={600} sx={{ fontSize: '1.125rem' }}>
              Upload Files
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 0.5, fontSize: '0.8125rem', fontWeight: 400 }}
            >
              {files.length > 0
                ? `${files.length} ${files.length === 1 ? 'file' : 'files'} selected • Add more or remove to adjust`
                : `Select files or folders to upload • Max ${formatFileSize(maxFileSize)} per file`}
            </Typography>
          </Box>
          <IconButton
            onClick={handleClose}
            disabled={uploading}
            sx={{
              color: 'text.secondary',
              '&:hover': {
                bgcolor: alpha(theme.palette.grey[500], 0.08),
              },
            }}
          >
            <Icon icon={closeIcon} style={{ fontSize: '20px' }} />
          </IconButton>
        </Stack>
      </DialogTitle>

      {uploading ? (
        <DialogContent
          sx={{
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: '320px',
            py: 5,
          }}
        >
          <CircularProgress size={56} thickness={3.6} sx={{ mb: 3 }} />
          <Typography variant="h6" fontWeight={600} sx={{ mb: 1, fontSize: '1rem' }}>
            {uploadStatus
              ? `Uploading ${uploadStatus.uploadedFiles} of ${uploadStatus.totalFiles} ${uploadStatus.totalFiles === 1 ? 'file' : 'files'}`
              : `Uploading ${fileStats.valid} ${fileStats.valid === 1 ? 'file' : 'files'}`}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3, fontSize: '0.875rem' }}>
            {uploadStatus && uploadStatus.totalBatches > 1
              ? `Processing batch ${uploadStatus.currentBatch} of ${uploadStatus.totalBatches} • Please wait while we process your upload`
              : 'Please wait while we process your upload'}
          </Typography>
          <Box sx={{ width: '100%', maxWidth: '420px' }}>
            <LinearProgress
              variant="determinate"
              value={uploadProgress}
              sx={{
                height: 6,
                borderRadius: 3,
                bgcolor: alpha(theme.palette.primary.main, 0.08),
                '& .MuiLinearProgress-bar': {
                  borderRadius: 3,
                },
              }}
            />
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mt: 1 }}>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: '0.75rem' }}
              >
                {uploadStatus
                  ? `${uploadStatus.uploadedFiles} / ${uploadStatus.totalFiles} files`
                  : `${Math.round(uploadProgress)}%`}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ fontSize: '0.75rem' }}
              >
                {Math.round(uploadProgress)}%
              </Typography>
            </Stack>
          </Box>
        </DialogContent>
      ) : (
        <>
          <DialogContent sx={{ px: 3, py: 3 }}>
            {/* Error Message */}
            {uploadError.show && (
              <Box
                sx={{
                  mb: 3,
                  p: 2,
                  borderRadius: '8px',
                  bgcolor: alpha(theme.palette.error.main, 0.04),
                  border: `1px solid ${alpha(theme.palette.error.main, 0.15)}`,
                }}
              >
                <Stack direction="row" spacing={1.5} alignItems="flex-start">
                  <Icon
                    icon={alertCircleIcon}
                    style={{
                      fontSize: '20px',
                      color: theme.palette.error.main,
                      flexShrink: 0,
                      marginTop: '1px',
                    }}
                  />
                  <Box sx={{ flex: 1 }}>
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      color="error.main"
                      sx={{ mb: 0.5, fontSize: '0.875rem' }}
                    >
                      Upload Error
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{ fontSize: '0.8125rem', color: 'text.secondary' }}
                    >
                      {uploadError.message}
                    </Typography>
                  </Box>
                  <IconButton
                    size="small"
                    onClick={() => setUploadError({ show: false, message: '' })}
                    sx={{ color: 'error.main', mt: -0.5 }}
                  >
                    <Icon icon={closeIcon} style={{ fontSize: '18px' }} />
                  </IconButton>
                </Stack>
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

            {/* Dropzone - Only show when no files are selected */}
            {files.length === 0 ? (
              <Box
                {...getRootProps()}
                sx={{
                  position: 'relative',
                  border: '2px dashed',
                  borderColor: isDragActive
                    ? theme.palette.primary.main
                    : alpha(theme.palette.divider, 0.3),
                  borderRadius: '10px',
                  p: 4,
                  textAlign: 'center',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  bgcolor: isDragActive ? alpha(theme.palette.primary.main, 0.04) : 'transparent',
                  '&:hover': {
                    borderColor: theme.palette.primary.main,
                    bgcolor: alpha(theme.palette.primary.main, 0.02),
                  },
                }}
              >
                <input {...getInputProps()} />
                <Icon
                  icon={cloudIcon}
                  style={{
                    fontSize: '48px',
                    marginBottom: '16px',
                    color: isDragActive ? theme.palette.primary.main : theme.palette.text.secondary,
                    opacity: isDragActive ? 1 : 0.6,
                  }}
                />
                <Typography variant="h6" sx={{ mb: 0.5, fontWeight: 600, fontSize: '1rem' }}>
                  {isDragActive ? 'Drop here to upload' : 'Drag and drop files or folders'}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mb: 3, fontSize: '0.875rem' }}
                >
                  or click to browse from your computer
                </Typography>

                <Stack direction="row" spacing={1.5} justifyContent="center">
                  <Button
                    variant="outlined"
                    size="medium"
                    startIcon={<Icon icon={filePlusIcon} />}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleFileSelectClick();
                    }}
                    sx={{
                      borderRadius: '8px',
                      textTransform: 'none',
                      fontWeight: 500,
                      fontSize: '0.875rem',
                      px: 2.5,
                      borderColor: alpha(theme.palette.divider, 0.2),
                      color: 'text.primary',
                      '&:hover': {
                        borderColor: theme.palette.primary.main,
                        bgcolor: alpha(theme.palette.primary.main, 0.04),
                      },
                    }}
                  >
                    Browse files
                  </Button>
                  <Button
                    variant="outlined"
                    size="medium"
                    startIcon={<Icon icon={folderUploadIcon} />}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleFolderSelectClick();
                    }}
                    sx={{
                      borderRadius: '8px',
                      textTransform: 'none',
                      fontWeight: 500,
                      fontSize: '0.875rem',
                      px: 2.5,
                      borderColor: alpha(theme.palette.divider, 0.2),
                      color: 'text.primary',
                      '&:hover': {
                        borderColor: theme.palette.primary.main,
                        bgcolor: alpha(theme.palette.primary.main, 0.04),
                      },
                    }}
                  >
                    Browse folders
                  </Button>
                </Stack>
              </Box>
            ) : (
              /* Compact add more files bar when files are present */
              <Stack
                direction="row"
                spacing={1.5}
                alignItems="center"
                sx={{
                  p: 1.75,
                  borderRadius: '8px',
                  bgcolor: alpha(theme.palette.primary.main, 0.02),
                  border: `1px dashed ${alpha(theme.palette.divider, 0.15)}`,
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    bgcolor: alpha(theme.palette.primary.main, 0.04),
                    borderColor: alpha(theme.palette.primary.main, 0.3),
                  },
                }}
              >
                <Icon
                  icon={filePlusIcon}
                  style={{
                    fontSize: '20px',
                    color: theme.palette.primary.main,
                    opacity: 0.7,
                  }}
                />
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    fontSize: '0.875rem',
                    color: 'text.secondary',
                    fontWeight: 500,
                  }}
                >
                  Add more to selection
                </Typography>
                <Button
                  variant="text"
                  size="small"
                  startIcon={<Icon icon={filePlusIcon} />}
                  onClick={handleFileSelectClick}
                  sx={{
                    textTransform: 'none',
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    borderRadius: '6px',
                    color: 'primary.main',
                    '&:hover': {
                      bgcolor: alpha(theme.palette.primary.main, 0.08),
                    },
                  }}
                >
                  Add files
                </Button>
                <Button
                  variant="text"
                  size="small"
                  startIcon={<Icon icon={folderUploadIcon} />}
                  onClick={handleFolderSelectClick}
                  sx={{
                    textTransform: 'none',
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    borderRadius: '6px',
                    color: 'primary.main',
                    '&:hover': {
                      bgcolor: alpha(theme.palette.primary.main, 0.08),
                    },
                  }}
                >
                  Add folder
                </Button>
              </Stack>
            )}

            {/* Files List */}
            {renderFilesList()}
          </DialogContent>

          <DialogActions
            sx={{
              px: 3,
              py: 2.5,
              borderTop: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
              bgcolor: alpha(theme.palette.grey[500], 0.02),
            }}
          >
            <Button
              onClick={handleClose}
              variant="text"
              color="inherit"
              sx={{
                textTransform: 'none',
                fontSize: '0.875rem',
                fontWeight: 500,
                borderRadius: '8px',
                px: 2.5,
                color: 'text.secondary',
                '&:hover': {
                  bgcolor: alpha(theme.palette.grey[500], 0.08),
                },
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleUpload}
              variant="contained"
              disabled={files.length === 0 || fileStats.oversized > 0}
              disableElevation
              startIcon={<Icon icon={cloudIcon} fontSize={20} />}
              sx={{
                textTransform: 'none',
                fontSize: '0.875rem',
                fontWeight: 600,
                borderRadius: '8px',
                px: 3,
                '&.Mui-disabled': {
                  bgcolor: alpha(theme.palette.primary.main, 0.12),
                  color: alpha(theme.palette.primary.contrastText, 0.4),
                },
              }}
            >
              {fileStats.oversized > 0
                ? 'Remove oversized files first'
                : fileStats.valid > 0
                  ? `Upload ${fileStats.valid} ${fileStats.valid === 1 ? 'file' : 'files'}`
                  : 'Upload'}
            </Button>
          </DialogActions>
        </>
      )}
    </Dialog>
  );
}
