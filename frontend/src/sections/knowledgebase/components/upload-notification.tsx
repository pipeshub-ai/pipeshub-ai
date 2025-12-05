import React, { useState, useEffect } from 'react';
import { Icon } from '@iconify/react';
import cloudUploadIcon from '@iconify-icons/mdi/cloud-upload';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import closeIcon from '@iconify-icons/mdi/close';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import chevronUpIcon from '@iconify-icons/mdi/chevron-up';
import fileDocumentIcon from '@iconify-icons/mdi/file-document';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle';

import {
  Box,
  Paper,
  alpha,
  Stack,
  Slide,
  useTheme,
  IconButton,
  Typography,
  LinearProgress,
  CircularProgress,
  Collapse,
} from '@mui/material';

interface UploadNotificationProps {
  uploads: Map<string, {
    kbId: string;
    folderId?: string;
    files: string[];
    startTime: number;
    recordIds?: string[];
    status?: 'uploading' | 'processing' | 'completed';
    failedFiles?: Array<{
      recordId: string;
      fileName: string;
      filePath: string;
      error: string;
    }>;
    hasFailures?: boolean;
  }>;
  onDismiss?: (uploadKey: string) => void;
  currentKBId?: string;
  currentFolderId?: string;
}

export const UploadNotification: React.FC<UploadNotificationProps> = ({
  uploads,
  onDismiss,
  currentKBId,
  currentFolderId,
}) => {
  const theme = useTheme();
  const [expanded, setExpanded] = useState<Map<string, boolean>>(new Map());
  
  // Filter uploads to only show those for the current KB/folder being viewed
  const uploadsArray = Array.from(uploads.entries()).filter(([_, upload]) => {
    if (!upload) {
      return false;
    }
    // Always show if no currentKBId is set (shouldn't happen, but be safe)
    if (!currentKBId) {
      return true;
    }
    // Match KB
    if (upload.kbId !== currentKBId) {
      return false;
    }
    // Match folder (both undefined/null means root)
    const uploadFolderId = upload.folderId || 'root';
    const viewFolderId = currentFolderId || 'root';
    return uploadFolderId === viewFolderId;
  });

  // Debug logging

  if (uploadsArray.length === 0) {
    return null;
  }

  const toggleExpand = (uploadKey: string) => {
    setExpanded((prev) => {
      const newMap = new Map(prev);
      newMap.set(uploadKey, !newMap.get(uploadKey));
      return newMap;
    });
  };

  return (
    <Box
      sx={{
        position: 'fixed',
        bottom: 24,
        right: 24,
        zIndex: 1400,
        display: 'flex',
        flexDirection: 'column',
        gap: 1,
        maxWidth: 360,
        pointerEvents: 'none',
      }}
    >
      {uploadsArray.map(([uploadKey, upload]) => {
        const isUploading = upload.status === 'uploading' || (!upload.status && !upload.recordIds);
        const isProcessing = upload.status === 'processing' || (upload.recordIds && upload.status !== 'completed' && !isUploading);
        const isCompleted = upload.status === 'completed';
        const hasFailures = upload.hasFailures || (upload.failedFiles && upload.failedFiles.length > 0);
        const failedFiles = upload.failedFiles || [];
        const failedFileNames = new Set(failedFiles.map((ff) => ff.fileName || ff.filePath));
        const successfulFiles = upload.files.filter(
          (fileName) => !failedFileNames.has(fileName)
        );
        const isExpanded = expanded.get(uploadKey) ?? false;
        const totalFiles = upload.files.length;
        const totalSuccessful = successfulFiles.length;
        const totalFailed = failedFiles.length;
        
        // Show successful files first (prioritize showing successes), then failed files
        // When collapsed, show successful files first to emphasize positive outcome
        const maxFilesToShow = 3;
        const successfulToShow = isExpanded 
          ? successfulFiles 
          : successfulFiles.slice(0, Math.min(maxFilesToShow, successfulFiles.length));
        const failedToShow = isExpanded
          ? failedFiles
          : failedFiles.slice(0, Math.max(0, maxFilesToShow - successfulToShow.length));
        
        const allFilesToShow: Array<{ name: string; isFailed: boolean; error?: string }> = [
          ...successfulToShow.map((name) => ({ name, isFailed: false })),
          ...failedToShow.map((ff) => ({ 
            name: ff.fileName || ff.filePath, 
            isFailed: true, 
            error: ff.error 
          }))
        ];
        const remainingCount = totalFiles > maxFilesToShow ? totalFiles - maxFilesToShow : 0;

        return (
          <Slide direction="left" in mountOnEnter unmountOnExit key={uploadKey}>
            <Paper
              elevation={4}
              sx={{
                pointerEvents: 'auto',
                borderRadius: 1.5,
                border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
                backgroundColor: theme.palette.background.paper,
                overflow: 'hidden',
                transition: theme.transitions.create(['transform', 'box-shadow'], {
                  duration: theme.transitions.duration.shorter,
                }),
                '&:hover': {
                  boxShadow: theme.shadows[8],
                },
              }}
            >
              {/* Header */}
              <Box
                sx={{
                  px: 1.5,
                  py: 1,
                  backgroundColor: isCompleted && !hasFailures
                    ? alpha(theme.palette.success.main, 0.1)
                    : isCompleted && hasFailures
                    ? alpha(theme.palette.success.main, 0.08)
                    : alpha(theme.palette.primary.main, 0.08),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <Stack direction="row" alignItems="center" spacing={1}>
                  {isCompleted && !hasFailures ? (
                    <Icon
                      icon={checkCircleIcon}
                      width={18}
                      height={18}
                      style={{ color: theme.palette.success.main }}
                    />
                  ) : isCompleted && hasFailures ? (
                    <Icon
                      icon={checkCircleIcon}
                      width={18}
                      height={18}
                      style={{ color: theme.palette.success.main }}
                    />
                  ) : isProcessing ? (
                    <CircularProgress
                      size={18}
                      thickness={4}
                      sx={{
                        color: theme.palette.info.main,
                      }}
                    />
                  ) : (
                    <Icon
                      icon={cloudUploadIcon}
                      width={18}
                      height={18}
                      style={{ color: theme.palette.primary.main }}
                    />
                  )}
                  <Typography
                    variant="body2"
                    fontWeight={500}
                    sx={{
                      fontSize: '0.8125rem',
                      color: isCompleted && !hasFailures
                        ? theme.palette.success.main
                        : isCompleted && hasFailures && totalSuccessful === 0
                        ? theme.palette.error.main
                        : 'text.primary',
                    }}
                  >
                    {isCompleted && hasFailures && totalSuccessful === 0
                      ? `Upload failed`
                      : isCompleted && hasFailures
                      ? `${totalSuccessful} succeeded`
                      : isCompleted
                      ? `${totalFiles} upload${totalFiles > 1 ? 's' : ''} complete`
                      : isProcessing
                      ? `Processing ${totalFiles} file${totalFiles > 1 ? 's' : ''}...`
                      : isUploading
                      ? `Uploading ${totalFiles} file${totalFiles > 1 ? 's' : ''}...`
                      : `Uploading ${totalFiles} file${totalFiles > 1 ? 's' : ''}...`}
                    {isCompleted && hasFailures && totalFailed > 0 && (
                      <>
                        <Typography
                          component="span"
                          sx={{
                            color: 'text.secondary',
                            fontWeight: 500,
                            mx: 0.5,
                            fontSize: '0.8125rem',
                          }}
                        >
                          {totalSuccessful > 0 ? ',' : ''}
                        </Typography>
                        <Typography
                          component="span"
                          sx={{
                            color: theme.palette.error.main,
                            fontWeight: 500,
                            fontSize: '0.8125rem',
                          }}
                        >
                          {totalFailed} {totalSuccessful === 0 ? 'file' : ''}{totalFailed > 1 ? 's' : ''} failed
                        </Typography>
                      </>
                    )}
                  </Typography>
                </Stack>

                <Stack direction="row" alignItems="center" spacing={0.5}>
                  {totalFiles > 3 && (
                    <IconButton
                      size="small"
                      onClick={() => toggleExpand(uploadKey)}
                      sx={{
                        width: 24,
                        height: 24,
                        color: 'text.secondary',
                        '&:hover': {
                          backgroundColor: alpha(theme.palette.action.hover, 0.5),
                        },
                      }}
                    >
                      <Icon
                        icon={isExpanded ? chevronUpIcon : chevronDownIcon}
                        width={18}
                        height={18}
                      />
                    </IconButton>
                  )}
                  {onDismiss && (
                    <IconButton
                      size="small"
                      onClick={() => onDismiss(uploadKey)}
                      sx={{
                        width: 24,
                        height: 24,
                        color: 'text.secondary',
                        '&:hover': {
                          backgroundColor: alpha(theme.palette.error.main, 0.1),
                          color: 'error.main',
                        },
                      }}
                    >
                      <Icon icon={closeIcon} width={16} height={16} />
                    </IconButton>
                  )}
                </Stack>
              </Box>

              {/* Content */}
              <Box sx={{ p: 1.5 }}>
                {/* File list with max height and scroll */}
                <Box
                  sx={{
                    maxHeight: isExpanded ? 300 : 120, // Max height: 300px when expanded, 120px when collapsed
                    overflowY: 'auto',
                    overflowX: 'hidden',
                    pr: 0.5,
                    // Custom scrollbar styling
                    '&::-webkit-scrollbar': {
                      width: '6px',
                    },
                    '&::-webkit-scrollbar-track': {
                      backgroundColor: alpha(theme.palette.divider, 0.1),
                      borderRadius: '3px',
                    },
                    '&::-webkit-scrollbar-thumb': {
                      backgroundColor: alpha(theme.palette.text.secondary, 0.3),
                      borderRadius: '3px',
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.text.secondary, 0.5),
                      },
                    },
                  }}
                >
                  <Stack spacing={0.5}>
                    {allFilesToShow.map((fileInfo, index) => {
                      const { name, isFailed, error } = fileInfo;
                      
                      return (
                        <Stack
                          key={index}
                          direction="row"
                          alignItems="center"
                          spacing={1}
                          sx={{
                            py: 0.5,
                            px: 0.75,
                            borderRadius: 0.75,
                            backgroundColor: isCompleted && !isFailed
                              ? alpha(theme.palette.success.main, 0.05)
                              : 'transparent',
                            '&:hover': {
                              backgroundColor: isCompleted && !isFailed
                                ? alpha(theme.palette.success.main, 0.1)
                                : alpha(theme.palette.action.hover, 0.3),
                            },
                          }}
                        >
                          {/* Show green checkmark for successful files, neutral icon for failed files */}
                          {isCompleted && !isFailed ? (
                            <Icon
                              icon={checkCircleIcon}
                              width={16}
                              height={16}
                              style={{
                                color: theme.palette.success.main,
                                flexShrink: 0,
                              }}
                            />
                          ) : (
                            <Icon
                              icon={fileDocumentIcon}
                              width={16}
                              height={16}
                              style={{
                                color: isFailed ? theme.palette.error.main : theme.palette.text.secondary,
                                flexShrink: 0,
                              }}
                            />
                          )}
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography
                              variant="body2"
                              sx={{
                                fontSize: '0.8125rem',
                                color: isCompleted && !isFailed
                                  ? theme.palette.success.main
                                  :  isFailed ? theme.palette.error.main : 'text.primary',
                                fontWeight: isCompleted && !isFailed ? 500 : 400,
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                              title={name}
                            >
                              {name}
                            </Typography>
                            {isFailed && error && (
                              <Typography
                                variant="caption"
                                sx={{
                                  fontSize: '0.7rem',
                                  color: theme.palette.text.secondary,
                                  display: 'block',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                  mt: 0.25,
                                  fontStyle: 'italic',
                                }}
                                title={error}
                              >
                                {error}
                              </Typography>
                            )}
                          </Box>
                        </Stack>
                      );
                    })}

                    {/* Show remaining count if collapsed and more files */}
                    {!isExpanded && remainingCount > 0 && (
                      <Typography
                        variant="caption"
                        sx={{
                          fontSize: '0.75rem',
                          color: 'text.secondary',
                          pl: 3,
                          py: 0.5,
                        }}
                      >
                        +{remainingCount} more file{remainingCount > 1 ? 's' : ''}
                      </Typography>
                    )}
                  </Stack>
                </Box>

                {/* Progress bar */}
                {!isCompleted && (
                  <Box sx={{ mt: 1.5 }}>
                    <LinearProgress
                      variant={isProcessing ? 'indeterminate' : 'determinate'}
                      value={isProcessing ? undefined : 100}
                      sx={{
                        height: 2,
                        borderRadius: 1,
                        backgroundColor: alpha(theme.palette.primary.main, 0.1),
                        '& .MuiLinearProgress-bar': {
                          borderRadius: 1,
                          backgroundColor: isProcessing
                            ? theme.palette.info.main
                            : theme.palette.primary.main,
                        },
                      }}
                    />
                  </Box>
                )}
                
                {/* Show summary for completed uploads with failures */}
                {isCompleted && hasFailures && (
                  <Box 
                    sx={{ 
                      mt: 1.5, 
                      pt: 1.5, 
                      borderTop: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                      backgroundColor: alpha(theme.palette.error.main, 0.04),
                      borderRadius: 1,
                      px: 1,
                      py: 0.75,
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <Icon
                        icon={alertCircleIcon}
                        width={16}
                        height={16}
                        style={{ color: theme.palette.error.main }}
                      />
                      <Typography
                        variant="caption"
                        sx={{
                          fontSize: '0.8125rem',
                          color: theme.palette.error.main,
                          fontWeight: 600,
                        }}
                      >
                        {totalFailed} file{totalFailed > 1 ? 's' : ''} failed to upload
                      </Typography>
                    </Stack>
                  </Box>
                )}
              </Box>
            </Paper>
          </Slide>
        );
      })}
    </Box>
  );
};
