import React, { useState, useEffect } from 'react';
import { Icon } from '@iconify/react';
import cloudUploadIcon from '@iconify-icons/mdi/cloud-upload';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import closeIcon from '@iconify-icons/mdi/close';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import chevronUpIcon from '@iconify-icons/mdi/chevron-up';
import fileDocumentIcon from '@iconify-icons/mdi/file-document';

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
  useEffect(() => {
    if (uploads.size > 0) {
      console.log('[UploadNotification] Component render - Total uploads:', uploads.size);
      console.log('[UploadNotification] Component render - Filtered uploads:', uploadsArray.length);
      console.log('[UploadNotification] Component render - Current KB:', currentKBId, 'Folder:', currentFolderId);
      console.log('[UploadNotification] Upload entries:', Array.from(uploads.entries()));
      if (uploadsArray.length > 0) {
        console.log('[UploadNotification] Will render', uploadsArray.length, 'notifications');
      } else {
        console.log('[UploadNotification] Filtered out all uploads');
      }
    }
  }, [uploads, uploadsArray.length, currentKBId, currentFolderId]);

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
        const isExpanded = expanded.get(uploadKey) ?? false;
        const totalFiles = upload.files.length;
        const filesToShow = isExpanded ? upload.files : upload.files.slice(0, 3);
        const remainingCount = totalFiles > 3 ? totalFiles - 3 : 0;

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
                  backgroundColor: isCompleted
                    ? alpha(theme.palette.success.main, 0.08)
                    : alpha(theme.palette.primary.main, 0.08),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}
              >
                <Stack direction="row" alignItems="center" spacing={1}>
                  {isCompleted ? (
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
                      color: isCompleted ? theme.palette.success.main : 'text.primary',
                    }}
                  >
                    {isCompleted
                      ? `${totalFiles} upload${totalFiles > 1 ? 's' : ''} complete`
                      : isProcessing
                      ? `Processing ${totalFiles} file${totalFiles > 1 ? 's' : ''}...`
                      : isUploading
                      ? `Uploading ${totalFiles} file${totalFiles > 1 ? 's' : ''}...`
                      : `Uploading ${totalFiles} file${totalFiles > 1 ? 's' : ''}...`}
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
                    {filesToShow.map((fileName, index) => (
                      <Stack
                        key={index}
                        direction="row"
                        alignItems="center"
                        spacing={1}
                        sx={{
                          py: 0.5,
                          px: 0.75,
                          borderRadius: 0.75,
                          '&:hover': {
                            backgroundColor: alpha(theme.palette.action.hover, 0.3),
                          },
                        }}
                      >
                        <Icon
                          icon={fileDocumentIcon}
                          width={16}
                          height={16}
                          style={{
                            color: theme.palette.text.secondary,
                            flexShrink: 0,
                          }}
                        />
                        <Typography
                          variant="body2"
                          sx={{
                            fontSize: '0.8125rem',
                            color: 'text.primary',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            flex: 1,
                          }}
                          title={fileName}
                        >
                          {fileName}
                        </Typography>
                        {isCompleted && (
                          <Icon
                            icon={checkCircleIcon}
                            width={16}
                            height={16}
                            style={{
                              color: theme.palette.success.main,
                              flexShrink: 0,
                            }}
                          />
                        )}
                      </Stack>
                    ))}

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
              </Box>
            </Paper>
          </Slide>
        );
      })}
    </Box>
  );
};
