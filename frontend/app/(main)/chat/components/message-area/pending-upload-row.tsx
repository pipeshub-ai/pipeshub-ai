'use client';

import React from 'react';
import { Box, Flex, Heading, Text } from '@radix-ui/themes';
import { Spinner } from '@/app/components/ui/spinner';
import { FileIcon } from '@/app/components/ui/file-icon';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { PendingChatUpload } from '@/chat/types';

interface PendingUploadRowProps {
  pending: PendingChatUpload;
}

/**
 * Inline placeholder rendered at the bottom of the message list while a
 * chat-attachment upload is in flight. Mirrors the question + attachment
 * chip layout used by `ChatResponse` so the swap from placeholder → real
 * user message after `threadRuntime.append` is visually seamless.
 *
 * Driven by the active slot's `pendingUpload`, which is set in
 * `chat-input-wrapper.tsx` before awaiting the multipart upload and cleared
 * once the assistant-ui runtime has appended the real user message (or the
 * upload errored out).
 */
export function PendingUploadRow({ pending }: PendingUploadRowProps) {
  const isMobile = useIsMobile();
  const fileCount = pending.files.length;
  const statusLabel =
    fileCount === 1
      ? 'Uploading attachment…'
      : `Uploading ${fileCount} attachments…`;

  return (
    <Box style={{ width: '100%' }} aria-live="polite">
      {/* Question row — same typography as ChatResponse so the placeholder
          aligns visually with the real user message that replaces it. */}
      {pending.question.trim().length > 0 && (
        <Box style={{ marginBottom: 'var(--space-3)' }}>
          <Heading
            size={isMobile ? '5' : '6'}
            weight="medium"
            style={{
              color: 'var(--slate-12)',
              lineHeight: 1.3,
              paddingTop: 'var(--space-3)',
            }}
          >
            {pending.question}
          </Heading>
        </Box>
      )}

      {/* Attachment chips with a spinner over the file icon. */}
      {fileCount > 0 && (
        <Flex
          align="center"
          gap="2"
          style={{
            marginBottom: 'var(--space-3)',
            overflowX: 'auto',
            overflowY: 'hidden',
          }}
          className="no-scrollbar"
        >
          {pending.files.map((file, idx) => (
            <Flex
              key={`${file.name}-${idx}`}
              align="center"
              gap="1"
              title={file.name}
              style={{
                flexShrink: 0,
                padding: 'var(--space-1) var(--space-2)',
                backgroundColor: 'var(--olive-a2)',
                border: '1px solid var(--olive-3)',
                borderRadius: 'var(--radius-1)',
                maxWidth: '200px',
              }}
            >
              <Box style={{ position: 'relative', width: 14, height: 14, flexShrink: 0 }}>
                <Box style={{ opacity: 0.45 }}>
                  <FileIcon
                    extension={getMimeTypeExtension(file.mimeType) || undefined}
                    filename={file.name}
                    size={14}
                    fallbackIcon="insert_drive_file"
                  />
                </Box>
                <Spinner
                  size={14}
                  thickness={1.5}
                  color="var(--slate-11)"
                  style={{
                    position: 'absolute',
                    inset: 0,
                  }}
                  ariaLabel={`Uploading ${file.name}`}
                />
              </Box>
              <Text
                size="1"
                style={{
                  color: 'var(--slate-11)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {file.name}
              </Text>
            </Flex>
          ))}
        </Flex>
      )}

      {/* Status line — matches StatusMessageComponent's restrained styling
          so it doesn't compete with the assistant's eventual response. */}
      <Flex align="center" gap="2" style={{ marginTop: 'var(--space-2)' }}>
        <Spinner size={14} color="var(--slate-11)" />
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {statusLabel}
        </Text>
      </Flex>
    </Box>
  );
}
