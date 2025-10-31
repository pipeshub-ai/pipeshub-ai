// GridView.tsx
import { Icon } from '@iconify/react';
import { useNavigate } from 'react-router';
import React, { useRef, useEffect, useCallback } from 'react';
import databaseIcon from '@iconify-icons/mdi/database';
import codeJsonIcon from '@iconify-icons/mdi/code-json';
// Import specific icons for better performance and type safety
import folderIcon from '@iconify-icons/mdi/folder-outline';
import languageGoIcon from '@iconify-icons/mdi/language-go';
import moreVertIcon from '@iconify-icons/mdi/dots-vertical';
import filePdfBoxIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import languagePhpIcon from '@iconify-icons/mdi/language-php';
import fileWordBoxIcon from '@iconify-icons/vscode-icons/file-type-word';
import languageCss3Icon from '@iconify-icons/mdi/language-css3';
import languageJavaIcon from '@iconify-icons/mdi/language-java';
import languageRubyIcon from '@iconify-icons/mdi/language-ruby';
import emailOutlineIcon from '@iconify-icons/mdi/email-outline';
import fileExcelBoxIcon from '@iconify-icons/vscode-icons/file-type-excel';
import fileImageBoxIcon from '@iconify-icons/vscode-icons/file-type-image';
import languageHtml5Icon from '@iconify-icons/mdi/language-html5';
import fileArchiveBoxIcon from '@iconify-icons/mdi/archive-outline';
import languagePythonIcon from '@iconify-icons/mdi/language-python';
import noteTextOutlineIcon from '@iconify-icons/mdi/note-text-outline';
import languageMarkdownIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import fileMusicOutlineIcon from '@iconify-icons/mdi/file-music-outline';
import fileVideoOutlineIcon from '@iconify-icons/mdi/file-video-outline';
import filePowerpointBoxIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import languageJavascriptIcon from '@iconify-icons/mdi/language-javascript';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';

import {
  Box,
  Grid,
  Chip,
  Stack,
  alpha,
  Button,
  Tooltip,
  useTheme,
  Skeleton,
  Typography,
  CardActionArea,
  CircularProgress
} from '@mui/material';

import {createScrollableContainerStyle} from 'src/sections/qna/chatbot/utils/styles/scrollbar';

interface GridViewProps {
  items: any[];
  pageLoading: boolean;
  navigateToFolder: (item: any) => void;
  handleMenuOpen: (event: React.MouseEvent<HTMLElement>, item: any) => void;
  CompactCard: any;
  CompactIconButton: any;
  totalCount: number;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}

// Helper hook for infinite scroll
const useIntersectionObserver = (callback: () => void, options: IntersectionObserverInit = {}) => {
  const targetRef = useRef<HTMLDivElement>(null);
  
  // Use useCallback to memoize the callback and prevent unnecessary re-renders
  const memoizedCallback = useCallback(callback, [callback]);

  useEffect(() => {
    const target = targetRef.current;
    if (!target) return undefined; // Explicitly return undefined instead of implicit return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          memoizedCallback();
        }
      },
      {
        threshold: 0.1,
        rootMargin: '100px', // Load content before it's visible
        ...options,
      }
    );

    observer.observe(target);

    return () => observer.disconnect();
  }, [memoizedCallback, options]); // Include options in dependency array

  return targetRef;
};

export const GridView: React.FC<GridViewProps> = ({
  items,
  pageLoading,
  navigateToFolder,
  handleMenuOpen,
  CompactCard,
  CompactIconButton,
  totalCount,
  hasMore,
  loadingMore,
  onLoadMore,
}) => {
  const navigate = useNavigate();
  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);

  const loadMoreRef = useIntersectionObserver(onLoadMore);

  // Enhanced file icon mapping - matches ListView exactly
  const getFileIcon = (extension: string, type: string, mimeType?: string) => {
    if (type === 'folder') return folderIcon;

    // Handle mime types first (like in KnowledgeBaseDetails)
    if ((!extension || extension === '') && mimeType) {
      switch (mimeType) {
        case 'application/vnd.google-apps.document':
          return fileWordBoxIcon;
        case 'application/vnd.google-apps.spreadsheet':
          return fileExcelBoxIcon;
        case 'application/vnd.google-apps.presentation':
          return filePowerpointBoxIcon;
        case 'application/vnd.google-apps.form':
          return noteTextOutlineIcon;
        case 'application/vnd.google-apps.drawing':
          return fileImageBoxIcon;
        case 'application/vnd.google-apps.folder':
          return folderIcon;
        case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        case 'application/vnd.microsoft.word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document':
          return fileWordBoxIcon;
        case 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        case 'application/vnd.microsoft.excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel':
          return fileExcelBoxIcon;
        case 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
        case 'application/vnd.microsoft.powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint':
          return filePowerpointBoxIcon;
        case 'application/vnd.microsoft.onedrive.document':
          return fileWordBoxIcon;
        case 'application/vnd.microsoft.onedrive.spreadsheet':
          return fileExcelBoxIcon;
        case 'application/vnd.microsoft.onedrive.presentation':
          return filePowerpointBoxIcon;
        case 'application/vnd.microsoft.onedrive.drawing':
          return fileImageBoxIcon;
        case 'application/vnd.microsoft.onedrive.folder':
          return folderIcon;
        default:
          return fileDocumentOutlineIcon;
      }
    }

    const ext = extension?.toLowerCase() || '';
    switch (ext) {
      case 'pdf':
        return filePdfBoxIcon;
      case 'doc':
      case 'docx':
        return fileWordBoxIcon;
      case 'xls':
      case 'xlsx':
      case 'csv':
        return fileExcelBoxIcon;
      case 'ppt':
      case 'pptx':
        return filePowerpointBoxIcon;
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'svg':
      case 'webp':
        return fileImageBoxIcon;
      case 'zip':
      case 'rar':
      case '7z':
      case 'tar':
      case 'gz':
        return fileArchiveBoxIcon;
      case 'txt':
        return noteTextOutlineIcon;
      case 'rtf':
        return fileDocumentOutlineIcon;
      case 'md':
      case 'mdx':
        return languageMarkdownIcon;
      case 'html':
      case 'htm':
        return languageHtml5Icon;
      case 'css':
        return languageCss3Icon;
      case 'js':
      case 'ts':
      case 'jsx':
      case 'tsx':
        return languageJavascriptIcon;
      case 'json':
        return codeJsonIcon;
      case 'py':
        return languagePythonIcon;
      case 'java':
        return languageJavaIcon;
      case 'php':
        return languagePhpIcon;
      case 'rb':
        return languageRubyIcon;
      case 'go':
        return languageGoIcon;
      case 'sql':
        return databaseIcon;
      case 'mp3':
      case 'wav':
      case 'ogg':
      case 'flac':
        return fileMusicOutlineIcon;
      case 'mp4':
      case 'avi':
      case 'mov':
      case 'wmv':
      case 'mkv':
        return fileVideoOutlineIcon;
      case 'eml':
      case 'msg':
        return emailOutlineIcon;
      default:
        return fileDocumentOutlineIcon;
    }
  };

  // Enhanced file icon color mapping - matches ListView exactly
  const getFileIconColor = (extension: string, type: string, mimeType?: string): string => {
    if (type === 'folder') return theme.palette.warning.main;

    // Handle mime types first
    if ((!extension || extension === '') && mimeType) {
      switch (mimeType) {
        case 'application/vnd.google-apps.document':
          return '#4285F4';
        case 'application/vnd.google-apps.spreadsheet':
          return '#0F9D58';
        case 'application/vnd.google-apps.presentation':
          return '#F4B400';
        case 'application/vnd.google-apps.form':
          return '#673AB7';
        case 'application/vnd.google-apps.drawing':
          return '#DB4437';
        case 'application/vnd.google-apps.folder':
          return '#5F6368';
        case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        case 'application/vnd.microsoft.word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document':
        case 'application/vnd.microsoft.onedrive.document':
          return '#2B579A';
        case 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        case 'application/vnd.microsoft.excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel':
        case 'application/vnd.microsoft.onedrive.spreadsheet':
          return '#217346';
        case 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
        case 'application/vnd.microsoft.powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint':
        case 'application/vnd.microsoft.onedrive.presentation':
          return '#B7472A';
        case 'application/vnd.microsoft.onedrive.drawing':
          return '#8C6A4F';
        case 'application/vnd.microsoft.onedrive.folder':
          return '#0078D4';
        default:
          return '#1976d2';
      }
    }

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
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'svg':
      case 'webp':
        return '#9c27b0';
      case 'zip':
      case 'rar':
      case '7z':
      case 'tar':
      case 'gz':
        return '#795548';
      case 'txt':
      case 'rtf':
      case 'md':
        return '#607d8b';
      case 'html':
      case 'htm':
        return '#e65100';
      case 'css':
        return '#0277bd';
      case 'js':
      case 'ts':
      case 'jsx':
      case 'tsx':
        return '#ffd600';
      case 'json':
        return '#616161';
      case 'py':
        return '#1976d2';
      case 'java':
        return '#b71c1c';
      case 'php':
        return '#6a1b9a';
      case 'rb':
        return '#c62828';
      case 'go':
        return '#00acc1';
      case 'sql':
        return '#00695c';
      case 'mp3':
      case 'wav':
      case 'ogg':
      case 'flac':
        return '#283593';
      case 'mp4':
      case 'avi':
      case 'mov':
      case 'wmv':
      case 'mkv':
        return '#d81b60';
      case 'eml':
      case 'msg':
        return '#6a1b9a';
      default:
        return theme.palette.info.main;
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (!bytes || bytes === 0) return '—';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  const formatDate = (timestamp: number) => {
    if (!timestamp) return '—';
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return '—'; // Use Number.isNaN instead of isNaN
    return date.toLocaleDateString();
  };

  const getStatusColor = (status: string) => {
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
        return 'primary';
      default:
        return 'default';
    }
  };

  const getStatusLabel = (status: string) => {
    if (!status) return '';

    switch (status) {
      case 'FILE_TYPE_NOT_SUPPORTED':
        return 'Not Supported';
      case 'AUTO_INDEX_OFF':
        return 'Manual Sync';
      case 'NOT_STARTED':
        return 'Not Started';
      case 'IN_PROGRESS':
        return 'In Progress';
      default:
        return status
          .replace(/_/g, ' ')
          .toLowerCase()
          .split(' ')
          .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
          .join(' ');
    }
  };

  // Get item data with proper fallbacks
  const getItemData = (item: any) => {
    const isFolder = item.type === 'folder';
    const isRecord = item.type === 'record';

    return {
      id: item.id,
      name: item.name || item.recordName,
      type: isFolder ? 'folder' : 'file',
      extension: item.extension || item.fileRecord?.extension,
      mimeType: item.fileRecord?.mimeType,
      sizeInBytes: item.sizeInBytes || item.fileRecord?.sizeInBytes,
      updatedAt: item.updatedAtTimestamp || item.updatedAt || item.sourceLastModifiedTimestamp,
      createdAt: item.createdAtTimestamp || item.createdAt || item.sourceCreatedAtTimestamp,
      indexingStatus: item.indexingStatus,
      recordType: item.recordType,
      origin: item.origin,
      hasChildren: item.hasChildren,
      counts: item.counts,
      webUrl: item.webUrl,
    };
  };

  return (
    <Box sx={{ maxHeight: '75vh', ...scrollableStyles }}>
      <Grid container spacing={2}>
        {pageLoading
          ? Array.from(new Array(16)).map((_, index) => (
              <Grid item xs={12} sm={6} md={4} lg={3} xl={2} key={index}>
                <Skeleton
                  variant="rounded"
                  height={120}
                  sx={{
                    borderRadius: 3,
                    backgroundColor: alpha(theme.palette.grey[300], 0.1),
                  }}
                  animation="wave"
                />
              </Grid>
            ))
          : items.map((item) => {
              const itemData = getItemData(item);
              const isFolder = itemData.type === 'folder';

              return (
                <Grid item xs={12} sm={6} md={4} lg={3} xl={3} key={itemData.id} sx={{mt:2,}}>
                  <CompactCard
                    sx={{
                      height: 120,
                      position: 'relative',
                      transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                      '&:hover': {
                        transform: 'translateY(-2px)',
                        boxShadow: theme.shadows[8],
                      },
                    }}
                  >
                    <CardActionArea
                      onClick={() => isFolder ? navigateToFolder(item) :   window.open(`/record/${item.id}`, '_blank')}
                      disabled={!isFolder}
                      sx={{
                        p: 2,
                        height: '100%',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'flex-start',
                        cursor: 'pointer',
                        '&:hover': {
                          backgroundColor: isFolder
                            ? alpha(theme.palette.primary.main, 0.02)
                            : 'transparent',
                        },
                        '&.Mui-disabled': {
                          opacity: 1,
                        },
                      }}
                    >
                      {/* Header with icon and name */}
                      <Stack direction="row" alignItems="center" spacing={1.5} width="100%" mb={1}>
                        <Box
                          sx={{
                            width: 36,
                            height: 36,
                            borderRadius: 2,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            backgroundColor: alpha(
                              getFileIconColor(
                                itemData.extension,
                                itemData.type,
                                itemData.mimeType
                              ),
                              0.12
                            ),
                            position: 'relative',
                          }}
                        >
                          <Icon
                            icon={getFileIcon(itemData.extension, itemData.type, itemData.mimeType)}
                            style={{
                              fontSize: '26px',
                              color: getFileIconColor(
                                itemData.extension,
                                itemData.type,
                                itemData.mimeType
                              ),
                              opacity: 0.9,
                            }}
                          />
                        </Box>

                        <Box sx={{ overflow: 'hidden', flexGrow: 1, minWidth: 0 }}>
                          <Tooltip title={itemData.name} placement="top">
                            <Typography
                              variant="body2"
                              fontWeight={600}
                              noWrap
                              sx={{
                                fontSize: '0.875rem',
                                lineHeight: 1.3,
                                color: theme.palette.text.primary,
                              }}
                            >
                              {itemData.name}
                            </Typography>
                          </Tooltip>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            noWrap
                            sx={{
                              fontSize: '0.7rem',
                              textTransform: 'uppercase',
                              letterSpacing: 0.5,
                              fontWeight: 500,
                            }}
                          >
                            {itemData.type === 'folder'
                              ? `${itemData.counts?.totalItems || 0} items`
                              : itemData.extension?.toUpperCase() || itemData.recordType || 'FILE'}
                          </Typography>
                        </Box>
                      </Stack>

                      {/* Footer with metadata */}
                      <Box sx={{ mt: 'auto', width: '100%' }}>
                        <Stack
                          direction="row"
                          justifyContent="space-between"
                          alignItems="center"
                          mb={0.5}
                        >
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{
                              fontSize: '0.7rem',
                              fontWeight: 500,
                            }}
                          >
                            {formatDate(itemData.updatedAt)}
                          </Typography>
                          {itemData.type === 'file' && itemData.sizeInBytes && (
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{
                                fontSize: '0.7rem',
                                fontWeight: 500,
                                fontFamily: 'monospace',
                              }}
                            >
                              {formatFileSize(itemData.sizeInBytes)}
                            </Typography>
                          )}
                        </Stack>

                        {/* Status and origin chips */}
                        <Stack direction="row" spacing={0.5} alignItems="center">
                          {itemData.indexingStatus && (
                            <Chip
                              label={getStatusLabel(itemData.indexingStatus)}
                              size="small"
                              color={getStatusColor(itemData.indexingStatus) as any}
                              variant="outlined"
                              sx={{
                                fontSize: '0.65rem',
                                height: 20,
                                fontWeight: 500,
                                borderRadius: 1,
                                '& .MuiChip-label': {
                                  px: 0.75,
                                },
                              }}
                            />
                          )}
                          {itemData.origin && itemData.origin !== 'UPLOAD' && (
                            <Chip
                              label={itemData.origin}
                              size="small"
                              variant="filled"
                              sx={{
                                fontSize: '0.6rem',
                                height: 18,
                                backgroundColor: alpha(theme.palette.info.main, 0.1),
                                color: theme.palette.info.main,
                                fontWeight: 500,
                                '& .MuiChip-label': {
                                  px: 0.5,
                                },
                              }}
                            />
                          )}
                        </Stack>
                      </Box>
                    </CardActionArea>

                    {/* Action menu button */}
                    <CompactIconButton
                      size="small"
                      onClick={(e: React.MouseEvent<HTMLElement>) => {
                        e.stopPropagation();
                        handleMenuOpen(e, item);
                      }}
                      sx={{
                        position: 'absolute',
                        top: 8,
                        right: 8,
                        width: 28,
                        height: 28,
                        backgroundColor: alpha(theme.palette.background.paper, 0.8),
                        backdropFilter: 'blur(4px)',
                        border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                        opacity: 0.7,
                        transition: 'all 0.2s ease',
                        '&:hover': {
                          opacity: 1,
                          backgroundColor: alpha(theme.palette.primary.main, 0.1),
                          borderColor: alpha(theme.palette.primary.main, 0.2),
                          transform: 'scale(1.05)',
                        },
                      }}
                    >
                      <Icon icon={moreVertIcon} fontSize={14} />
                    </CompactIconButton>
                  </CompactCard>
                </Grid>
              );
            })}
      </Grid>
      {/* Skeletons for "load more" action */}
      {loadingMore && (
        <Grid container spacing={2} sx={{ mt: 2 }}>
          {Array.from(new Array(6)).map((_, index) => (
            <Grid item xs={12} sm={6} md={4} lg={3} xl={2} key={`skeleton-${index}`}>
              <Skeleton variant="rounded" height={120} sx={{ borderRadius: 3 }} animation="wave" />
            </Grid>
          ))}
        </Grid>
      )}

      <Box sx={{ mt: 4, textAlign: 'center' }}>
        {/* Case 1: More items are available, and we are not currently loading */}
        {hasMore && !loadingMore && !pageLoading && (
          <Box ref={loadMoreRef}>
            <Button
              variant="outlined"
              onClick={onLoadMore}
              disabled={loadingMore}
              sx={{
                borderRadius: 2,
                px: 3,
                py: 1,
                fontSize: '0.875rem',
                fontWeight: 500,
              }}
            >
              Load More
            </Button>
          </Box>
        )}

        {/* Case 2: We are actively loading more items */}
        {loadingMore && (
          <Box sx={{ py: 2 }}>
            <CircularProgress size={24} thickness={4} />
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Loading more items...
            </Typography>
          </Box>
        )}

        {/* Case 3: Display the count after the initial load is complete */}
        {!pageLoading && items.length > 0 && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="body2" color="text.secondary">
              Showing {items.length} of {totalCount} items
            </Typography>
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default GridView;