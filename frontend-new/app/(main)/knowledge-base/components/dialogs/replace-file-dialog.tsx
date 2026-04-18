'use client';

import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Dialog, Flex, Box, Text, Button, IconButton, VisuallyHidden } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import type { KnowledgeHubNode } from '../../types';

// File type to MIME type mapping
const FILE_TYPE_MIME_MAP: Record<string, string[]> = {
  PDF: ['application/pdf'],
  DOCX: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  DOC: ['application/msword'],
  TXT: ['text/plain'],
  PNG: ['image/png'],
  JPG: ['image/jpeg'],
  JPEG: ['image/jpeg'],
  XLS: ['application/vnd.ms-excel'],
  XLSX: ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
  PPT: ['application/vnd.ms-powerpoint'],
  PPTX: ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
  CSV: ['text/csv'],
  JSON: ['application/json'],
  MD: ['text/markdown'],
};

const MAX_FILE_SIZE_MB = 30;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

interface ReplaceFileDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  item: KnowledgeHubNode | null;
  onReplace: (item: KnowledgeHubNode, newFile: File) => void;
  isReplacing?: boolean;
}

function formatSize(bytes: number | undefined): string {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ReplaceFileDialog({
  open,
  onOpenChange,
  item,
  onReplace,
  isReplacing = false,
}: ReplaceFileDialogProps) {
  const [replacementFile, setReplacementFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isCurrentFileHovered, setIsCurrentFileHovered] = useState(false);
  const [isReplacementFileHovered, setIsReplacementFileHovered] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Get accepted MIME types based on current file type
  const fileType = item?.extension?.toUpperCase() || 'PDF';
  const acceptedMimeTypes = FILE_TYPE_MIME_MAP[fileType] || FILE_TYPE_MIME_MAP['PDF'];

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setReplacementFile(null);
      setIsDragOver(false);
    }
  }, [open]);

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

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);

      const files = e.dataTransfer.files;
      if (files.length > 0) {
        const file = files[0];
        if (file.size <= MAX_FILE_SIZE_BYTES && acceptedMimeTypes.includes(file.type)) {
          setReplacementFile(file);
        }
      }
    },
    [acceptedMimeTypes]
  );

  const handleClick = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        const file = files[0];
        if (file.size <= MAX_FILE_SIZE_BYTES && acceptedMimeTypes.includes(file.type)) {
          setReplacementFile(file);
        }
      }
      e.target.value = '';
    },
    [acceptedMimeTypes]
  );

  const handleSave = useCallback(() => {
    if (item && replacementFile) {
      onReplace(item, replacementFile);
    }
  }, [item, replacementFile, onReplace]);

  const handleOpenChange = useCallback(
    (isOpen: boolean) => {
      if (!isOpen) {
        setReplacementFile(null);
        setIsDragOver(false);
      }
      onOpenChange(isOpen);
    },
    [onOpenChange]
  );

  const isSaveDisabled = !replacementFile || isReplacing;

  if (!item) return null;

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
          // background: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          boxShadow: '0 20px 48px 0 rgba(0, 0, 0, 0.25)',
          // backdropFilter: 'blur(25px)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          transform: 'none',
          animation: 'slideInFromRight 0.2s ease-out',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>Replace File</Dialog.Title>
        </VisuallyHidden>

        {/* Header */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '8px 8px 8px 16px',
            borderBottom: '1px solid var(--olive-3)',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
          }}
        >
          <Flex align="center" gap="2">
            <MaterialIcon name="drive_folder_upload" size={24} color="var(--slate-12)" />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Replace File
            </Text>
          </Flex>
          <IconButton
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => handleOpenChange(false)}
          >
            <MaterialIcon name="close" size={16} color="var(--slate-11)" />
          </IconButton>
        </Flex>

        {/* Content */}
        <Box
          className="no-scrollbar"
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            padding: '16px',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(25px)',
          }}
        >
          <Flex direction="column" gap="5" style={{ flex: 1 }}>
            {/* Name Field */}
            <Flex direction="column" gap="2">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                Name
              </Text>
              <Box
                style={{
                  padding: '4px 12px',
                  backgroundColor: 'var(--slate-1)',
                  borderRadius: 'var(--radius-2)',
                  border: '1px solid var(--olive-3)',
                }}
              >
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {item.name}
                </Text>
              </Box>
            </Flex>

            {/* Current File Section */}
            <Flex direction="column" gap="3" style={{ padding: 'var(--space-4)', borderRadius: 'var(--radius-2)', border: '1px solid var(--olive-3)', background: 'var(--olive-2)' }}>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                Current File
              </Text>
              <Box style={{ height: '1px', background: 'var(--olive-3)' }} />
              <Flex
                align="center"
                justify="between"
                onMouseEnter={() => setIsCurrentFileHovered(true)}
                onMouseLeave={() => setIsCurrentFileHovered(false)}
                style={{
                  padding: 'var(--space-3)',
                  backgroundColor: isCurrentFileHovered ? 'var(--olive-3)' : 'var(--olive-2)',
                  border: '1px solid var(--olive-3)',
                }}
              >
                <Flex align="center" gap="3">
                  <Box
                    style={{
                      width: '36px',
                      height: '36px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      backgroundColor: 'var(--slate-4)',
                      borderRadius: 'var(--radius-2)',
                    }}
                  >
                    <FileIcon
                      extension={item.extension}
                      size={18}
                      fallbackIcon="insert_drive_file"
                    />
                  </Box>
                  <Flex direction="column" gap="1">
                    <Text
                      size="2"
                      style={{
                        color: 'var(--slate-12)',
                        maxWidth: '240px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {item.name}
                    </Text>
                    <Text size="1" style={{ color: 'var(--slate-9)' }}>
                      {formatSize(item.sizeInBytes ?? undefined)}
                    </Text>
                  </Flex>
                </Flex>
              </Flex>

              {/* Info Banner */}
              <Flex
                align="center"
                gap="3"
                style={{
                  padding: 'var(--space-3)',
                  background: 'var(--accent-3)',
                  border: '1px solid var(--olive-4)',
                }}
              >
                <Flex style={{padding: 'var(--space-2)', borderRadius: 'var(--radius-1)', background: 'var(--slate-a2)'}}>
                <MaterialIcon name="info" size={16} color="var(--accent-11)" />
                </Flex>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  You can only upload {fileType} files (.{fileType.toLowerCase()}) to replace this file
                </Text>
              </Flex>
            </Flex>

            {/* Replace File Section */}
            <Flex
              direction="column"
              gap="3"
              style={{
                flex: replacementFile ? 0 : 1,
                padding: 'var(--space-4)',
                borderRadius: 'var(--radius-2)',
                border: '1px solid var(--olive-2)',
                background: 'var(--olive-2)',
              }}
            >
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  Replace File
                </Text>
                <Text size="1" style={{ color: 'var(--slate-9)' }}>
                  You can upload files up to the limit of {MAX_FILE_SIZE_MB} MB
                </Text>
              </Flex>
              <Box style={{ height: '1px', background: 'var(--olive-3)' }} />

              {/* Replacement File Item */}
              {replacementFile && (
                <Flex
                  align="center"
                  justify="between"
                  onMouseEnter={() => setIsReplacementFileHovered(true)}
                  onMouseLeave={() => setIsReplacementFileHovered(false)}
                  style={{
                    padding: '12px',
                    backgroundColor: isReplacementFileHovered ? 'var(--slate-3)' : 'var(--slate-2)',
                    borderRadius: 'var(--radius-2)',
                    border: '1px solid var(--slate-5)',
                  }}
                >
                  <Flex align="center" gap="3">
                    <Box
                      style={{
                        width: '36px',
                        height: '36px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        backgroundColor: 'var(--slate-4)',
                        borderRadius: 'var(--radius-2)',
                      }}
                    >
                      <FileIcon
                        extension={fileType}
                        size={18}
                        fallbackIcon="insert_drive_file"
                      />
                    </Box>
                    <Flex direction="column" gap="1">
                      <Text
                        size="2"
                        style={{
                          color: 'var(--slate-12)',
                          maxWidth: '240px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {replacementFile.name}
                      </Text>
                      <Text size="1" style={{ color: 'var(--slate-9)' }}>
                        {formatSize(replacementFile.size)}
                      </Text>
                    </Flex>
                  </Flex>
                  <IconButton
                    variant="ghost"
                    color="gray"
                    size="1"
                    style={{ opacity: isReplacementFileHovered ? 1 : 0.5 }}
                    onClick={() => setReplacementFile(null)}
                  >
                    <MaterialIcon name="close" size={16} color="var(--slate-11)" />
                  </IconButton>
                </Flex>
              )}

              {/* Drop Zone */}
              {!replacementFile && (
                <Flex 
                  style={{
                    padding: 'var(--space-3)',
                    borderRadius: 'var(--radius-1)',
                    background: 'var(--olive-2)',
                    border: '1px solid var(--olive-3)',
                    backdropFilter: 'blur(25px)',
                    flex: 1,
                  }}
                >
                <Box
                  onClick={handleClick}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  style={{
                    border: `1px dashed ${isDragOver ? 'var(--accent-9)' : 'var(--slate-7)'}`,
                    borderRadius: 'var(--radius-2)',
                    background: isDragOver ? 'var(--accent-a2)' : 'rgba(255, 255, 255, 0.00)',
                    padding: '32px',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <input
                    ref={inputRef}
                    type="file"
                    style={{ display: 'none' }}
                    accept={acceptedMimeTypes.join(',')}
                    onChange={handleInputChange}
                  />
                  <Flex direction="column" align="center" gap="1">
                    <MaterialIcon
                      name="add"
                      size={24}
                      color={isDragOver ? 'var(--accent-9)' : 'var(--slate-9)'}
                    />
                    <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                      Upload
                    </Text>
                    <Text size="1" weight="light" style={{ color: 'var(--slate-12)' }}>
                      Supports: Only {fileType}
                    </Text>
                  </Flex>
                </Box>
                </Flex>
              )}
            </Flex>
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
            backdropFilter: 'blur(8px)',
          }}
        >
          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={() => handleOpenChange(false)}
            disabled={isReplacing}
            style={{
              borderRadius: 'var(--radius-2)',
              // border: '1px solid var(--slate-a8)',
            }}
          >
            Cancel
          </Button>
          <LoadingButton
            variant={isSaveDisabled ? 'soft' : 'solid'}
            size="2"
            onClick={handleSave}
            disabled={!replacementFile}
            loading={isReplacing}
            loadingLabel="Saving..."
            style={{backgroundColor: isSaveDisabled ? 'var(--slate-a3)' : 'var(--emerald-10)'}}
          >
            Save
          </LoadingButton>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
