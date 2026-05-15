'use client';

import React, { useState, useRef, useCallback } from 'react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { Flex, Box, Text, Button, IconButton } from '@radix-ui/themes';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import type { UploadedFile } from '../chat/types';

type ChatMode = 'chat' | 'search';
type ChatInputVariant = 'full' | 'minimized';

interface ChatInputSettings {
  mode: ChatMode;
}

interface ChatInputProps {
  onSend?: (message: string, files?: UploadedFile[]) => void;
  settings?: ChatInputSettings;
  onModeChange?: (mode: ChatMode) => void;
  placeholder?: string;
  variant?: ChatInputVariant;
  expandable?: boolean;
  contextName?: string;
}

const SUPPORTED_FILE_TYPES = ['TXT', 'PDF', 'DOC', 'DOCX', 'CSV', 'PNG', 'JPEG', 'JPG', 'SVG'];
const ACCEPTED_MIME_TYPES = {
  'text/plain': 'TXT',
  'application/pdf': 'PDF',
  'application/msword': 'DOC',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'text/csv': 'CSV',
  'application/csv': 'CSV',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'image/svg+xml': 'SVG',
};
// Extension fallback for files with missing/generic MIME types (e.g. CSV/SVG).
const ACCEPTED_EXTENSIONS = ['txt', 'pdf', 'doc', 'docx', 'csv', 'png', 'jpeg', 'jpg', 'svg'];

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isFileTypeSupported(file: File): boolean {
  const mimeType = file.type;
  if (Object.keys(ACCEPTED_MIME_TYPES).includes(mimeType)) return true;
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  return ACCEPTED_EXTENSIONS.includes(ext);
}

export function ChatInput({
  onSend,
  settings = { mode: 'chat' },
  onModeChange,
  placeholder = 'Ask anything...',
  variant = 'full',
  expandable = false,
  contextName,
}: ChatInputProps) {
  const [message, setMessage] = useState('');
  const [showUploadArea, setShowUploadArea] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isExpanded, setIsExpanded] = useState(variant === 'full');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';

  const showFullUI = variant === 'full' || isExpanded;
  const minimizedPlaceholder = contextName
    ? `Ask in '${contextName}' anything...`
    : placeholder;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if ((message.trim() || uploadedFiles.length > 0) && onSend) {
      onSend(message, uploadedFiles.length > 0 ? uploadedFiles : undefined);
      setMessage('');
      setUploadedFiles([]);
      setShowUploadArea(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const processFiles = useCallback((files: FileList | File[]) => {
    const fileArray = Array.from(files);
    const validFiles = fileArray.filter(isFileTypeSupported);

    const newUploadedFiles: UploadedFile[] = validFiles.map((file) => ({
      id: `${file.name}-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`,
      file,
      name: file.name,
      size: file.size,
      type: file.type,
      status: 'uploaded' as const,
    }));

    setUploadedFiles((prev) => [...prev, ...newUploadedFiles]);
    if (newUploadedFiles.length > 0) {
      setShowUploadArea(false);
    }
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      processFiles(e.target.files);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processFiles(e.dataTransfer.files);
    }
  };

  const removeFile = (fileId: string) => {
    setUploadedFiles((prev) => prev.filter((f) => f.id !== fileId));
  };

  const toggleUploadArea = () => {
    setShowUploadArea((prev) => !prev);
  };

  const hasContent = message.trim() || uploadedFiles.length > 0;

  const handleExpand = () => {
    if (expandable && !isExpanded) {
      setIsExpanded(true);
    }
  };

  if (!showFullUI) {
    return (
      <Flex
        align="center"
        gap="2"
        style={{
          backgroundColor: isDark ? 'var(--slate-2)' : 'var(--slate-12)',
          borderRadius: 'var(--radius-full)',
          padding: '4px',
          boxShadow: '0 4px 16px rgba(0, 0, 0, 0.16)',
        }}
      >
        <Button variant="solid" size="2" radius="full" onClick={handleExpand}>
          <MaterialIcon name="chat" size={16} color="white" />
          Chat
        </Button>

        <Flex
          align="center"
          style={{
            backgroundColor: isDark ? 'var(--slate-3)' : 'var(--slate-11)',
            borderRadius: 'var(--radius-full)',
            padding: '8px 12px',
            minWidth: '240px',
          }}
        >
          <input
            type="text"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={handleExpand}
            placeholder={minimizedPlaceholder}
            style={{
              flex: 1,
              border: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
              color: isDark ? 'var(--slate-12)' : 'var(--slate-1)',
              fontSize: '14px',
              fontFamily: 'inherit',
            }}
          />

          <IconButton
            variant="solid"
            size="1"
            onClick={handleSubmit}
            disabled={!message.trim()}
            style={{
              marginLeft: '8px',
              backgroundColor: message.trim() ? 'var(--accent-9)' : 'var(--slate-10)',
            }}
          >
            <MaterialIcon name="arrow_upward" size={16} color="white" />
          </IconButton>
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex
      direction="column"
      gap="3"
      style={{
        width: '37.5rem',
        backdropFilter: 'blur(25px)',
        backgroundColor: 'var(--color-surface)',
        border: '1px solid var(--slate-3)',
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-3) var(--space-4)',
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* Upload Area */}
      {showUploadArea && (
        <Flex direction="column" gap="2">
          <Text size="2" style={{ color: 'var(--slate-12)' }}>Upload your File</Text>
          <Box
            style={{
              position: 'relative',
              border: `2px dashed ${isDragging ? 'var(--accent-9)' : 'var(--accent-6)'}`,
              borderRadius: 'var(--radius-3)',
              padding: 'var(--space-6)',
              transition: 'all 0.15s',
              backgroundColor: isDragging ? 'var(--accent-a3)' : 'transparent',
            }}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <Flex direction="column" align="center" gap="2">
              <MaterialIcon name="image" size={36} color="var(--slate-11)" />
              <Box style={{ textAlign: 'center' }}>
                <Text size="2" style={{ color: 'var(--slate-12)' }}>Drag & Drop</Text>
                <br />
                <Text size="2" style={{ color: 'var(--slate-11)' }}>or </Text>
                <Button
                  variant="ghost"
                  size="1"
                  onClick={() => fileInputRef.current?.click()}
                  style={{ padding: 0, color: 'var(--accent-9)' }}
                >
                  Browse
                </Button>
              </Box>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                Supports: {SUPPORTED_FILE_TYPES.join(', ')}
              </Text>
            </Flex>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={[
                ...Object.keys(ACCEPTED_MIME_TYPES),
                ...ACCEPTED_EXTENSIONS.map((e) => `.${e}`),
              ].join(',')}
              onChange={handleFileSelect}
              style={{ display: 'none' }}
            />
          </Box>
        </Flex>
      )}

      {/* Uploaded Files Preview */}
      {uploadedFiles.length > 0 && (
        <Flex wrap="wrap" gap="2">
          {uploadedFiles.map((file) => (
            <Flex
              key={file.id}
              align="center"
              gap="2"
              style={{
                padding: 'var(--space-2) var(--space-3)',
                backgroundColor: 'var(--slate-1)',
                border: '1px solid var(--slate-3)',
                borderRadius: 'var(--radius-3)',
                minWidth: '180px',
                maxWidth: '220px',
              }}
            >
              <FileIcon
                extension={getMimeTypeExtension(file.type) || undefined}
                filename={file.name}
                size={20}
                fallbackIcon="insert_drive_file"
              />
              <Box style={{ flex: 1, minWidth: 0 }}>
                <Text size="1" style={{ color: 'var(--slate-12)', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {file.name}
                </Text>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {formatFileSize(file.size)}
                </Text>
              </Box>
              <IconButton
                variant="ghost"
                size="1"
                onClick={() => removeFile(file.id)}
                style={{ flexShrink: 0 }}
              >
                <MaterialIcon name="close" size={14} color="var(--slate-11)" />
              </IconButton>
            </Flex>
          ))}
        </Flex>
      )}

      {/* Input field */}
      <textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        rows={1}
        style={{
          width: '100%',
          backgroundColor: 'transparent',
          outline: 'none',
          border: 'none',
          fontSize: 'var(--font-size-2)',
          color: 'var(--slate-11)',
          resize: 'none',
          minHeight: '24px',
          maxHeight: '120px',
          fontFamily: 'Manrope, sans-serif',
          height: 'auto',
          overflow: message.split('\n').length > 5 ? 'auto' : 'hidden',
        }}
        onInput={(e) => {
          const target = e.target as HTMLTextAreaElement;
          target.style.height = 'auto';
          target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
        }}
      />

      {/* Bottom controls */}
      <Flex align="center" justify="between">
        {/* Left side - Mode switcher */}
        <Flex
          align="center"
          style={{
            backgroundColor: 'var(--slate-1)',
            border: '1px solid var(--slate-3)',
            borderRadius: 'var(--radius-2)',
            padding: 'var(--space-1)',
          }}
        >
          {/* Chat mode button */}
          <Button
            variant={settings.mode === 'chat' ? 'soft' : 'ghost'}
            size="2"
            onClick={() => onModeChange?.('chat')}
          >
            <MaterialIcon name="smart_toy" size={16} color="var(--accent-9)" />
            <Text size="2" weight="medium" style={{ color: 'var(--accent-11)' }}>Chat</Text>
            <MaterialIcon name="expand_more" size={16} color="var(--accent-9)" />
          </Button>

          {/* Search button */}
          <IconButton
            variant={settings.mode === 'search' ? 'soft' : 'ghost'}
            size="2"
            onClick={() => onModeChange?.('search')}
          >
            <MaterialIcon name="search" size={16} color="var(--accent-9)" />
          </IconButton>
        </Flex>

        {/* Right side - Controls */}
        <Flex align="center" gap="1">
          {/* Action buttons */}
          <Flex align="center" style={{ padding: 'var(--space-1)', borderRadius: 'var(--radius-2)' }}>
            {['apps', 'tag', 'attach_file', 'mic'].map((iconName) => (
              <IconButton
                key={iconName}
                variant={iconName === 'attach_file' && showUploadArea ? 'soft' : 'ghost'}
                size="2"
                onClick={iconName === 'attach_file' ? toggleUploadArea : undefined}
              >
                <MaterialIcon name={iconName} size={16} color="var(--accent-9)" />
              </IconButton>
            ))}
          </Flex>

          {/* Send button */}
          <IconButton
            variant="solid"
            size="2"
            onClick={handleSubmit}
            disabled={!hasContent}
            style={{
              backgroundColor: hasContent ? 'var(--accent-9)' : 'var(--slate-a2)',
            }}
          >
            <MaterialIcon
              name="arrow_upward"
              size={16}
              color={hasContent ? 'var(--accent-contrast)' : 'var(--slate-11)'}
            />
          </IconButton>
        </Flex>
      </Flex>
    </Flex>
  );
}
