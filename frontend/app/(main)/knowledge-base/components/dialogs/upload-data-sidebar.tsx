'use client';

import React, { useState, useRef, useCallback, useEffect, useLayoutEffect } from 'react';
import { Flex, Box, Text, Button, IconButton, Dialog, VisuallyHidden } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { useUploadLimits } from '@/lib/hooks/use-upload-limits';

function createUploadItemId(prefix: 'file' | 'folder'): string {
  const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return `${prefix}-${cryptoApi.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const allEntries: FileSystemEntry[] = [];
    const readBatch = () => {
      reader.readEntries((entries) => {
        if (entries.length === 0) {
          resolve(allEntries);
        } else {
          allEntries.push(...entries);
          readBatch();
        }
      }, reject);
    };
    readBatch();
  });
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
}

function DropZone({ type, onDrop, isEmpty }: DropZoneProps) {
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
      const items: UploadFileItem[] = Array.from(files).map((file) => ({
        id: createUploadItemId('file'),
        name: file.name,
        size: file.size,
        type: 'file' as const,
        file,
      }));

      if (items.length > 0) {
        onDrop(items);
      }
    },
    [onDrop]
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
              const reader = dirEntry.createReader();
              const entries = await readAllEntries(reader);
              for (const childEntry of entries) {
                if (childEntry.isFile) {
                  const fileEntry = childEntry as FileSystemFileEntry;
                  const file = await new Promise<File>((res, rej) => {
                    fileEntry.file((f) => res(f), rej);
                  });
                  const relativePath = currentPath
                    ? `${currentPath}/${file.name}`
                    : file.name;
                  folderFiles.push({ file, relativePath });
                  totalSize += file.size;
                } else if (childEntry.isDirectory) {
                  const newPath = currentPath
                    ? `${currentPath}/${childEntry.name}`
                    : childEntry.name;
                  await readDirectory(childEntry as FileSystemDirectoryEntry, newPath);
                }
              }
            };

            await readDirectory(entry as FileSystemDirectoryEntry);

            if (folderFiles.length > 0) {
              folderItems.push({
                id: createUploadItemId('folder'),
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
            const relativePath = pathParts.slice(1).join('/');

            if (!folderMap.has(folderName)) {
              folderMap.set(folderName, []);
            }
            folderMap.get(folderName)?.push({ file, relativePath });
          });

          const items: UploadFileItem[] = [];
          folderMap.forEach((folderFiles, folderName) => {
            if (folderFiles.length === 0) return;
            const totalSize = folderFiles.reduce((sum, f) => sum + f.file.size, 0);
            items.push({
              id: createUploadItemId('folder'),
              name: folderName,
              size: totalSize,
              type: 'folder',
              filesWithPaths: folderFiles,
            });
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
      : { multiple: true };

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
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flex: 1,
      }}
    >
      <input
        ref={inputRef}
        type="file"
        data-testid={`upload-input-${type}`}
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
              {type === 'file' ? 'Add files' : 'Add a folder'}
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
    const canScroll = scrollHeight > clientHeight + 1;
    setShowTopFade(canScroll && scrollTop > 4);
    setShowBottomFade(canScroll && scrollTop + clientHeight < scrollHeight - 4);
  }, []);

  useLayoutEffect(() => {
    updateFades();
  }, [children, updateFades]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(updateFades);
    ro.observe(el);
    el.addEventListener('scroll', updateFades, { passive: true });
    return () => {
      ro.disconnect();
      el.removeEventListener('scroll', updateFades);
    };
  }, [updateFades]);

  return (
    <Box style={{ position: 'relative', flex: 1, minHeight: 0 }}>
      {showTopFade && (
        <Box
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            height: '20px',
            pointerEvents: 'none',
            zIndex: 1,
            boxShadow: 'inset 0 12px 12px -8px rgba(0, 0, 0, 0.12)',
          }}
        />
      )}
      <Box
        ref={scrollRef}
        className="no-scrollbar"
        style={{
          flex: 1,
          minHeight: 0,
          maxHeight: '100%',
          overflowY: 'auto',
          overflowX: 'hidden',
        }}
      >
        {children}
      </Box>
      {showBottomFade && (
        <>
          <Box
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: '28px',
              pointerEvents: 'none',
              zIndex: 1,
              boxShadow: 'inset 0 -14px 14px -8px rgba(0, 0, 0, 0.14)',
            }}
          />
          <Flex
            justify="center"
            style={{
              position: 'absolute',
              bottom: '6px',
              left: 0,
              right: 0,
              pointerEvents: 'none',
              zIndex: 2,
            }}
          >
            <Text
              size="1"
              style={{
                color: 'var(--slate-11)',
                backgroundColor: 'var(--olive-4)',
                padding: '2px 8px',
                borderRadius: '999px',
                border: '1px solid var(--olive-5)',
              }}
            >
              More below
            </Text>
          </Flex>
        </>
      )}
    </Box>
  );
}

interface UploadedItemProps {
  item: UploadFileItem;
  onRemove: (id: string) => void;
}

function UploadedItem({ item, onRemove }: UploadedItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      justify="between"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: 'var(--space-2) var(--space-3)',
        background: 'var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-4)',
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
        <Box
          style={{
            width: '28px',
            height: '28px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: item.type === 'folder' ? 'var(--accent-a3)' : 'var(--slate-4)',
            borderRadius: 'var(--radius-1)',
            flexShrink: 0,
          }}
        >
          {item.type === 'folder' ? (
            <MaterialIcon name="folder" size={16} color="var(--accent-9)" />
          ) : (
            <FileIcon filename={item.name} size={16} />
          )}
        </Box>
        <Flex direction="column" gap="0" style={{ minWidth: 0 }}>
          <Text
            size="2"
            style={{
              color: 'var(--slate-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={item.name}
          >
            {item.name}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {item.type === 'folder'
              ? `${item.filesWithPaths?.length ?? 0} files`
              : formatBytes(item.size)}
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
  const { maxFileSizeMB } = useUploadLimits();

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
      setFileItems([]);
      setFolderItems([]);
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
        onInteractOutside={(event) => event.preventDefault()}
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
            <Flex align="start" justify="between" gap="2">
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  Upload Files
                </Text>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  Up to {maxFileSizeMB} MB per file; type and size are validated on upload
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

            {fileItems.length > 0 && (
              <ScrollableList>
                <Flex direction="column" gap="2" style={{ paddingBottom: '4px' }}>
                  {fileItems.map((item) => (
                    <UploadedItem key={item.id} item={item} onRemove={handleRemoveFile} />
                  ))}
                </Flex>
              </ScrollableList>
            )}

            <Box style={{ ...(fileItems.length === 0 ? { flex: 1 } : {}), ...(fileItems.length > 0 ? { minHeight: '88px' } : {}) }}>
              <DropZone type="file" onDrop={handleAddFiles} isEmpty={fileItems.length === 0} />
            </Box>
          </Flex>

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
            <Flex align="start" justify="between" gap="2">
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  Upload Folders
                </Text>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  Each file is validated on upload (max {maxFileSizeMB} MB per file)
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

            {folderItems.length > 0 && (
              <ScrollableList>
                <Flex direction="column" gap="2" style={{ paddingBottom: '4px' }}>
                  {folderItems.map((item) => (
                    <UploadedItem key={item.id} item={item} onRemove={handleRemoveFolder} />
                  ))}
                </Flex>
              </ScrollableList>
            )}

            <Box style={{ ...(folderItems.length === 0 ? { flex: 1 } : {}), ...(folderItems.length > 0 ? { minHeight: '88px' } : {}) }}>
              <DropZone type="folder" onDrop={handleAddFolders} isEmpty={folderItems.length === 0} />
            </Box>
          </Flex>
        </Box>

        {/* Footer */}
        <Flex
          align="center"
          justify="end"
          gap="3"
          style={{
            padding: 'var(--space-3) var(--space-4)',
            borderTop: '1px solid var(--olive-3)',
            backgroundColor: 'var(--effects-translucent)',
          }}
        >
          <Button variant="soft" color="gray" size="2" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <LoadingButton
            variant="solid"
            size="2"
            onClick={handleSave}
            disabled={!hasItems}
            loading={isSaving}
          >
            Save
          </LoadingButton>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
