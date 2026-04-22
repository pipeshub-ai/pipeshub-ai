'use client';

import React, { useState, useRef, useCallback, useEffect, useLayoutEffect } from 'react';
import { Flex, Box, Text, Button, IconButton, Dialog, VisuallyHidden } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { useUploadLimits } from '@/lib/hooks/use-upload-limits';

// Supported file types
const SUPPORTED_FILE_TYPES = ['TXT', 'PDF', 'DOC', 'DOCX', 'PNG', 'JPEG', 'JPG', 'SVG', 'XLS', 'XLSX', 'CSV', 'HTML', 'PPT', 'PPTX', 'MD', 'MDX'];
const SUPPORTED_MIME_TYPES = [
  'text/plain',
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'image/png',
  'image/jpeg',
  'image/svg+xml',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'text/csv',
  'application/csv',
  'text/html',
  'application/vnd.ms-powerpoint',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'text/markdown',
  'text/x-markdown',
  'application/x-markdown',
  'text/mdx',
];
// Extensions used as a fallback when the browser doesn't report a MIME type
// (e.g. some OSes send CSV/SVG/Markdown files with an empty or generic `type`).
const SUPPORTED_EXTENSIONS = [
  'txt', 'pdf', 'doc', 'docx', 'png', 'jpeg', 'jpg', 'svg', 'xls', 'xlsx', 'csv', 'html', 'htm', 'ppt', 'pptx', 'md', 'markdown', 'mdx',
];
function isSupportedFile(file: File): boolean {
  if (SUPPORTED_MIME_TYPES.includes(file.type)) return true;
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  return SUPPORTED_EXTENSIONS.includes(ext);
}

// File with relative path for folder uploads
export interface FileWithPath {
  file: File;
  relativePath: string; // e.g., "subfolder/nested/document.pdf"
}

// File/folder item to be uploaded
export interface UploadFileItem {
  id: string;
  name: string;
  size: number;
  type: 'file' | 'folder';
  file?: File;
  files?: File[]; // Keep for backward compatibility
  filesWithPaths?: FileWithPath[]; // For folder uploads with path preservation
}

interface DropZoneProps {
  type: 'file' | 'folder';
  onDrop: (items: UploadFileItem[]) => void;
  isEmpty: boolean;
  maxFileSizeBytes: number;
}

function DropZone({ type, onDrop, isEmpty, maxFileSizeBytes }: DropZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const processFiles = useCallback(
    (files: FileList | File[]) => {
      const items: UploadFileItem[] = [];
      const fileArray = Array.from(files);

      if (type === 'file') {
        fileArray.forEach((file) => {
          if (file.size <= maxFileSizeBytes && isSupportedFile(file)) {
            items.push({
              id: `file-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
              name: file.name,
              size: file.size,
              type: 'file',
              file,
            });
          }
        });
      }

      if (items.length > 0) {
        onDrop(items);
      }
    },
    [type, onDrop]
  );

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);

      const { items, files } = e.dataTransfer;

      if (type === 'folder' && items) {
        const folderItems: UploadFileItem[] = [];

        for (let i = 0; i < items.length; i++) {
          const item = items[i];
          const entry = item.webkitGetAsEntry?.();

          if (entry?.isDirectory) {
            const folderFiles: FileWithPath[] = [];
            let totalSize = 0;

            const readDirectory = async (
              dirEntry: FileSystemDirectoryEntry,
              currentPath: string = ''
            ): Promise<void> => {
              return new Promise((resolve) => {
                const reader = dirEntry.createReader();
                reader.readEntries(async (entries) => {
                  for (const entry of entries) {
                    if (entry.isFile) {
                      const fileEntry = entry as FileSystemFileEntry;
                      const file = await new Promise<File>((res) => {
                        fileEntry.file((f) => res(f));
                      });
                      // Build relative path
                      const relativePath = currentPath
                        ? `${currentPath}/${file.name}`
                        : file.name;
                      folderFiles.push({ file, relativePath });
                      totalSize += file.size;
                    } else if (entry.isDirectory) {
                      // Recurse with updated path
                      const newPath = currentPath
                        ? `${currentPath}/${entry.name}`
                        : entry.name;
                      await readDirectory(entry as FileSystemDirectoryEntry, newPath);
                    }
                  }
                  resolve();
                });
              });
            };

            await readDirectory(entry as FileSystemDirectoryEntry);

            if (totalSize <= maxFileSizeBytes) {
              folderItems.push({
                id: `folder-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                name: entry.name,
                size: totalSize,
                type: 'folder',
                filesWithPaths: folderFiles,
              });
            }
          }
        }

        if (folderItems.length > 0) {
          onDrop(folderItems);
        }
      } else if (type === 'file' && files) {
        processFiles(files);
      }
    },
    [type, onDrop, processFiles]
  );

  const handleClick = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const { files } = e.target;
      if (files) {
        if (type === 'folder') {
          const folderMap = new Map<string, FileWithPath[]>();

          Array.from(files).forEach((file) => {
            const pathParts = file.webkitRelativePath.split('/');
            const folderName = pathParts[0];
            // Get path after root folder name (e.g., "subfolder/file.pdf")
            const relativePath = pathParts.slice(1).join('/');

            if (!folderMap.has(folderName)) {
              folderMap.set(folderName, []);
            }
            folderMap.get(folderName)?.push({ file, relativePath });
          });

          const items: UploadFileItem[] = [];
          folderMap.forEach((folderFiles, folderName) => {
            const totalSize = folderFiles.reduce((sum, f) => sum + f.file.size, 0);
            if (totalSize <= maxFileSizeBytes) {
              items.push({
                id: `folder-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                name: folderName,
                size: totalSize,
                type: 'folder',
                filesWithPaths: folderFiles,
              });
            }
          });

          if (items.length > 0) {
            onDrop(items);
          }
        } else {
          processFiles(files);
        }
      }
      e.target.value = '';
    },
    [type, onDrop, processFiles]
  );

  const inputProps =
    type === 'folder'
      ? { webkitdirectory: '', directory: '', multiple: true }
      : {
          multiple: true,
          // Include both MIME types and extensions so browsers that can't
          // resolve a CSV MIME still allow the file via extension match.
          accept: [
            ...SUPPORTED_MIME_TYPES,
            ...SUPPORTED_EXTENSIONS.map((e) => `.${e}`),
          ].join(','),
        };

  return (
     <Flex
        style={{
          padding: 'var(--space-3)',
          borderRadius: 'var(--radius-1)',
          background: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          backdropFilter: 'blur(25px)',
          flex: 1,
          height: '100%',
        }}
    >
    <Box
      onClick={handleClick}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      style={{
        border: `1px dashed ${isDragOver ? 'var(--accent-9)' : 'var(--slate-9)'}`,
        borderRadius: 'var(--radius-4)',
        background: isDragOver ? 'var(--accent-a2)' : 'rgba(255, 255, 255, 0.00)',
        padding: isEmpty ? 'var(--space-8)' : 'var(--space-4)',
        cursor: 'pointer',
        transition: 'all 0.15s ease',
        height: '100%',
        // minHeight: '120px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flex: 1,
      }}
    >
      <input
        ref={inputRef}
        type="file"
        style={{ display: 'none' }}
        onChange={handleInputChange}
        {...inputProps}
      />
      <Flex direction="column" align="center" gap="1">
        <MaterialIcon
          name="add"
          size={24}
          color={isDragOver ? 'var(--accent-9)' : 'var(--slate-9)'}
        />
        {isEmpty && (
          <>
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Upload
            </Text>
            <Text size="1" weight="light" style={{ color: 'var(--slate-12)' }}>
              {type === 'file'
                ? `Supports: ${SUPPORTED_FILE_TYPES.join(', ')}`
                : 'Supports: Only folders'}
            </Text>
          </>
        )}
      </Flex>
    </Box>
    </Flex>
  );
}

// Scrollable list that surfaces clear affordances (visible scrollbar, edge
// shadows, and a "more below" chip) so users can tell when there is content
// above/below the visible area. File/folder items share the same background
// as the container, so a plain fade-to-bg gradient is invisible - we use a
// dark inset shadow plus an explicit chip instead.
interface ScrollableListProps {
  children: React.ReactNode;
}

function ScrollableList({ children }: ScrollableListProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [showTopFade, setShowTopFade] = useState(false);
  const [showBottomFade, setShowBottomFade] = useState(false);

  const updateFades = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const overflows = scrollHeight - clientHeight > 1;
    setShowTopFade(overflows && scrollTop > 1);
    setShowBottomFade(overflows && scrollTop + clientHeight < scrollHeight - 1);
  }, []);

  useLayoutEffect(() => {
    updateFades();
  });

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const handleScroll = () => updateFades();
    el.addEventListener('scroll', handleScroll, { passive: true });

    const ro = new ResizeObserver(() => updateFades());
    ro.observe(el);
    Array.from(el.children).forEach((child) => ro.observe(child));

    return () => {
      el.removeEventListener('scroll', handleScroll);
      ro.disconnect();
    };
  }, [updateFades]);

  return (
    <Box style={{ position: 'relative', flex: 1, minHeight: 0 }}>
      <Box
        ref={scrollRef}
        className="upload-scroll-area"
        style={{
          height: '100%',
          // `scroll` (not `auto`) keeps the scrollbar track reserved so the
          // styled scrollbar is always visible on browsers/OSes (e.g. macOS)
          // that otherwise hide overlay scrollbars when idle.
          overflowY: 'scroll',
          paddingRight: '4px',
        }}
      >
        {children}
      </Box>
      {/* Top shadow - hints at content above when scrolled */}
      <Box
        aria-hidden
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '12px',
          pointerEvents: 'none',
          boxShadow: 'inset 0 8px 8px -8px rgba(0, 0, 0, 0.35)',
          opacity: showTopFade ? 1 : 0,
          transition: 'opacity 0.15s ease',
        }}
      />
      {/* Bottom shadow - hints at more content below */}
      <Box
        aria-hidden
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: '14px',
          pointerEvents: 'none',
          boxShadow: 'inset 0 -10px 10px -8px rgba(0, 0, 0, 0.35)',
          opacity: showBottomFade ? 1 : 0,
          transition: 'opacity 0.15s ease',
        }}
      />
      {/* Floating chip - the most obvious "more below" cue */}
      <Flex
        align="center"
        justify="center"
        gap="1"
        onClick={() => {
          const el = scrollRef.current;
          if (!el) return;
          el.scrollBy({ top: el.clientHeight * 0.8, behavior: 'smooth' });
        }}
        style={{
          position: 'absolute',
          bottom: 6,
          left: '50%',
          transform: 'translateX(-50%)',
          padding: '2px 8px',
          borderRadius: '999px',
          backgroundColor: 'var(--slate-12)',
          color: 'var(--slate-1)',
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.25)',
          cursor: 'pointer',
          opacity: showBottomFade ? 0.95 : 0,
          pointerEvents: showBottomFade ? 'auto' : 'none',
          transition: 'opacity 0.15s ease',
        }}
      >
        <MaterialIcon name="keyboard_arrow_down" size={14} color="var(--slate-1)" />
        <Text size="1" weight="medium" style={{ color: 'var(--slate-1)' }}>
          More
        </Text>
      </Flex>
    </Box>
  );
}

interface UploadedItemProps {
  item: UploadFileItem;
  onRemove: (id: string) => void;
}

function UploadedItem({ item, onRemove }: UploadedItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <Flex
      align="center"
      justify="between"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: 'var(--space-3)',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Flex align="center" gap="3">
        <Box
          style={{
            // width: '32px',
            // height: '32px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: item.type === 'folder' ? 'var(--accent-a3)' : 'var(--slate-4)',
            borderRadius: 'var(--radius-1)',
          }}
        >
          {item.type === 'folder' ? (
            <MaterialIcon name="folder" size={16} />
          ) : (
            <FileIcon filename={item.name} size={16} />
          )}
        </Box>
        <Flex direction="column" gap="1">
          <Text
            size="2"
            style={{
              color: 'var(--slate-12)',
              maxWidth: '200px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.name}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {formatSize(item.size)}
          </Text>
        </Flex>
      </Flex>
      <IconButton
        variant="ghost"
        color="gray"
        size="1"
        onClick={() => onRemove(item.id)}
        style={{ opacity: isHovered ? 1 : 0.5 }}
      >
        <MaterialIcon name="close" size={16} color="var(--slate-11)" />
      </IconButton>
    </Flex>
  );
}

export interface UploadDataSidebarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (items: UploadFileItem[]) => void;
  isSaving?: boolean;
}

export function UploadDataSidebar({
  open,
  onOpenChange,
  onSave,
  isSaving = false,
}: UploadDataSidebarProps) {
  const [fileItems, setFileItems] = useState<UploadFileItem[]>([]);
  const [folderItems, setFolderItems] = useState<UploadFileItem[]>([]);
  const { maxFileSizeBytes, maxFileSizeMB } = useUploadLimits();

  const handleAddFiles = useCallback((items: UploadFileItem[]) => {
    setFileItems((prev) => [...prev, ...items]);
  }, []);

  const handleAddFolders = useCallback((items: UploadFileItem[]) => {
    setFolderItems((prev) => [...prev, ...items]);
  }, []);

  const handleRemoveFile = useCallback((id: string) => {
    setFileItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const handleRemoveFolder = useCallback((id: string) => {
    setFolderItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const handleSave = useCallback(() => {
    const allItems = [...fileItems, ...folderItems];
    if (allItems.length > 0) {
      onSave(allItems);
      // Reset state immediately after save
      // TODO: remove if after save any issues faced
      // --------------------------
      setFileItems([]);
      setFolderItems([]);
      // ------------------------
    }
  }, [fileItems, folderItems, onSave]);

  const handleOpenChange = useCallback(
    (isOpen: boolean) => {
      if (!isOpen) {
        setFileItems([]);
        setFolderItems([]);
      }
      onOpenChange(isOpen);
    },
    [onOpenChange]
  );

  const hasItems = fileItems.length > 0 || folderItems.length > 0;

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Content
        style={{
          position: 'fixed',
          top: 10,
          right: 10,
          bottom: 10,
          width: '37.5rem',
          maxWidth: '100vw',
          maxHeight: 'calc(100vh - 20px)',
          padding: 0,
          margin: 0,
          background: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          backdropFilter: 'blur(25px)',
          boxShadow: ' 0 20px 48px 0 rgba(0, 0, 0, 0.25)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          transform: 'none',
          animation: 'slideInFromRight 0.2s ease-out',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>Upload Data</Dialog.Title>
        </VisuallyHidden>
        {/* Header */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-4)',
            borderBottom: '1px solid var(--olive-3)',
            backdropFilter: 'blur(8px)',
            backgroundColor: 'var(--effects-translucent)',
          }}
        >
          <Flex align="center" gap="2">
            <MaterialIcon name="file_upload" size={24} color="var(--slate-12)" />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Upload Data
            </Text>
          </Flex>
          <Flex align="center" gap="3">
            <Button
              variant="surface"
              color="gray"
              size="2"
              onClick={() => window.open('https://docs.pipeshub.com', '_blank')}
            >
              <MaterialIcon name="open_in_new" size={14} color="var(--slate-11)" />
              Documentation
            </Button>
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={() => handleOpenChange(false)}
            >
              <MaterialIcon name="close" size={16} color="var(--slate-11)" />
            </IconButton>
          </Flex>
        </Flex>

        {/* Content */}
        <Box
          style={{
            flex: 1,
            display: 'grid',
            gridTemplateRows: '1fr auto 1fr',
            gap: '16px',
            overflowY: 'hidden',
            padding: 'var(--space-4)',
            backgroundColor: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
          }}
        >
          {/* Upload Files Section */}
          <Flex
            direction="column"
            gap="3"
            style={{
              height: '100%',
              minHeight: 0,
              padding: 'var(--space-4)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--olive-2)',
            }}
          >
            {/* Header */}
            <Flex align="start" justify="between" gap="2">
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  Upload Files
                </Text>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  You can upload files up to the limit of {maxFileSizeMB} MB
                </Text>
              </Flex>
              {fileItems.length > 0 && (
                <Text
                  size="1"
                  weight="medium"
                  style={{
                    color: 'var(--slate-12)',
                    backgroundColor: 'var(--olive-4)',
                    padding: '2px 8px',
                    borderRadius: '999px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {fileItems.length} {fileItems.length === 1 ? 'file' : 'files'}
                </Text>
              )}
            </Flex>
            <Box style={{ height: '1px', background: 'var(--olive-3)' }} />

            {/* File list - scrollable with overflow affordances */}
            {fileItems.length > 0 && (
              <ScrollableList>
                <Flex direction="column" gap="2" style={{ paddingBottom: '4px' }}>
                  {fileItems.map((item) => (
                    <UploadedItem key={item.id} item={item} onRemove={handleRemoveFile} />
                  ))}
                </Flex>
              </ScrollableList>
            )}

            {/* File drop zone - expands when empty, compact when has items */}
            <Box style={{ ...(fileItems.length === 0 ? { flex: 1 } : {}), ...(fileItems.length > 0 ? { minHeight: '88px' } : {}) }}>
              <DropZone type="file" onDrop={handleAddFiles} isEmpty={fileItems.length === 0} maxFileSizeBytes={maxFileSizeBytes} />
            </Box>
          </Flex>

          {/* Divider */}
          <Flex align="center" gap="3">
            <Box style={{ flex: 1, height: '1px', backgroundColor: 'var(--slate-6)' }} />
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              OR
            </Text>
            <Box style={{ flex: 1, height: '1px', backgroundColor: 'var(--slate-6)' }} />
          </Flex>

          {/* Upload Folders Section */}
          <Flex
            direction="column"
            gap="3"
            style={{
              height: '100%',
              minHeight: 0,
              padding: 'var(--space-4)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--olive-2)',
            }}
          >
            {/* Header */}
            <Flex align="start" justify="between" gap="2">
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  Upload Folders
                </Text>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  You can upload folders up to the limit of {maxFileSizeMB} MB
                </Text>
              </Flex>
              {folderItems.length > 0 && (
                <Text
                  size="1"
                  weight="medium"
                  style={{
                    color: 'var(--slate-12)',
                    backgroundColor: 'var(--olive-4)',
                    padding: '2px 8px',
                    borderRadius: '999px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {folderItems.length} {folderItems.length === 1 ? 'folder' : 'folders'}
                </Text>
              )}
            </Flex>
            <Box style={{ height: '1px', background: 'var(--olive-3)' }} />

            {/* Folder list - scrollable with overflow affordances */}
            {folderItems.length > 0 && (
              <ScrollableList>
                <Flex direction="column" gap="2" style={{ paddingBottom: '4px' }}>
                  {folderItems.map((item) => (
                    <UploadedItem key={item.id} item={item} onRemove={handleRemoveFolder} />
                  ))}
                </Flex>
              </ScrollableList>
            )}

            {/* Folder drop zone - expands when empty, compact when has items */}
            <Box style={{ ...(folderItems.length === 0 ? { flex: 1 } : {}), ...(folderItems.length > 0 ? { minHeight: '88px' } : {}) }}>
              <DropZone type="folder" onDrop={handleAddFolders} isEmpty={folderItems.length === 0} maxFileSizeBytes={maxFileSizeBytes} />
            </Box>
          </Flex>
        </Box>

        {/* Footer */}
        <Flex
          align="center"
          justify="end"
          gap="2"
          style={{
            padding: 'var(--space-2)',
            borderTop: '1px solid var(--olive-3)',
            background: 'var(--effects-translucent)',
          }}
        >
          <Button 
            variant="outline" 
            color="gray" size="2" 
            style={{
              cursor: 'pointer',
              borderRadius: 'var(--radius-2)'
            }} 
            onClick={() => handleOpenChange(false)}
          >
            Cancel
          </Button>
          <LoadingButton
            variant="solid"
            size="2"
            style={{
              backgroundColor: (!hasItems || isSaving) ? 'var(--slate-a3)' : 'var(--emerald-10)'
            }}
            disabled={!hasItems}
            loading={isSaving}
            loadingLabel="Saving..."
            onClick={handleSave}
          >
            Save
          </LoadingButton>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
