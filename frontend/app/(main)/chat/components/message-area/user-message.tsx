'use client';

import React, { useState, useCallback, useRef } from 'react';
import { Button, IconButton, Tooltip } from '@radix-ui/themes';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { AppliedFilters } from '../applied-filters';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useCommandStore } from '@/lib/store/command-store';
import { useChatStore } from '../../store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import type { AppliedFilters as AppliedFiltersData, AttachmentRef } from '../../types';
import { FileIcon } from '@/app/components/ui/file-icon';
import { getMimeTypeExtension } from '@/lib/utils/file-icon-utils';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import {
  isPresentationFile,
  isDocxFile,
  isLegacyWordDocFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';

const QUESTION_CHAR_LIMIT = 250;

function formatMessageTime(isoString: string): string {
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return '';
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const timeStr = date.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
  if (isToday) return timeStr;
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

interface UserMessageProps {
  /** The user's question / message text */
  question: string;
  /** ISO timestamp of when the user sent this query */
  createdAt?: string;
  /** Attachments uploaded with this user query (PDF / JPEG / PNG). */
  attachments?: AttachmentRef[];
  /** Collections attached to this message (e.g. KB filters the user selected) — reserved for future display, currently only affects spacing. */
  collections?: Array<{ id: string; name: string }>;
  appliedFilters?: AppliedFiltersData;
  /** Backend _id of the bot_response message (used to route the edit command) */
  messageId?: string;
  /** Whether the paired assistant answer is currently streaming — hides the edit affordance mid-stream */
  isStreaming?: boolean;
}

/**
 * Renders a single user turn as a distinct, right-aligned chat bubble —
 * the industry-standard pattern (ChatGPT / Claude / Gemini) for separating
 * the user's message from the assistant's response. Replaces the previous
 * "question as document heading" layout.
 */
export function UserMessage({
  question,
  createdAt,
  attachments,
  collections,
  appliedFilters,
  messageId,
  isStreaming = false,
}: UserMessageProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [isHovered, setIsHovered] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setPreviewFile = useChatStore((s) => s.setPreviewFile);
  const setPreviewMode = useChatStore((s) => s.setPreviewMode);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(question);
      setCopied(true);
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard API may fail in some contexts */
    }
  }, [question]);

  const isQuestionLong = question.length > QUESTION_CHAR_LIMIT;
  const displayedQuestion = isExpanded || !isQuestionLong
    ? question
    : question.slice(0, QUESTION_CHAR_LIMIT).trimEnd() + '…';

  const handleEditQuery = useCallback(() => {
    if (!messageId || isStreaming) return;
    useCommandStore.getState().dispatch('showEditQuery', {
      messageId,
      text: question,
    });
  }, [messageId, question, isStreaming]);

  const handleAttachmentPreview = useCallback(
    async (att: AttachmentRef) => {
      setPreviewFile({
        id: att.recordId,
        name: att.recordName,
        url: '',
        type: att.mimeType,
        isLoading: true,
        hideFileDetails: true,
        showDownload: true,
      });
      setPreviewMode('sidebar');

      try {
        const streamAsPdf =
          isPresentationFile(att.mimeType, att.recordName) ||
          isLegacyWordDocFile(att.mimeType, att.recordName);
        const streamOptions = streamAsPdf ? { convertTo: 'application/pdf' } : undefined;
        const blob = await KnowledgeBaseApi.streamRecord(att.recordId, streamOptions);
        const resolvedType = resolvePreviewMimeAfterStream(
          att.mimeType,
          att.recordName,
          blob,
          !!streamOptions,
        );
        const isDocx = isDocxFile(att.mimeType, att.recordName, att.recordName, att.extension, att.extension);
        const url = isDocx ? '' : URL.createObjectURL(blob);
        setPreviewFile({
          id: att.recordId,
          name: att.recordName,
          url,
          blob: isDocx ? blob : undefined,
          type: resolvedType,
          isLoading: false,
          previewRenderable: true,
          hideFileDetails: true,
          showDownload: true,
        });
      } catch (error) {
        setPreviewFile({
          id: att.recordId,
          name: att.recordName,
          url: '',
          type: att.mimeType,
          error: error instanceof Error ? error.message : 'Failed to load file',
          isLoading: false,
          hideFileDetails: true,
          showDownload: true,
        });
      }
    },
    [setPreviewFile, setPreviewMode],
  );

  const hasFilters = Boolean(
    appliedFilters && (appliedFilters.apps.length > 0 || appliedFilters.kb.length > 0)
  );
  const hasAttachments = Boolean(attachments && attachments.length > 0);
  const hasCollections = Boolean(collections && collections.length > 0);

  return (
    <Flex
      direction="column"
      align="end"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        marginBottom: hasCollections || hasFilters ? 'var(--space-3)' : 'var(--space-4)',
      }}
    >
      {/* Applied filter chips — right-aligned above the bubble */}
      {hasFilters && (
        <Box style={{ marginBottom: 'var(--space-2)', maxWidth: isMobile ? '100%' : '80%' }}>
          <AppliedFilters appliedFilters={appliedFilters!} />
        </Box>
      )}

      {/* Attachment chips — right-aligned above the bubble */}
      {hasAttachments && (
        <Flex
          align="center"
          gap="2"
          justify="end"
          wrap="wrap"
          style={{
            marginBottom: 'var(--space-2)',
            maxWidth: isMobile ? '100%' : '80%',
          }}
        >
          {attachments!.map((att) => (
            <Flex
              key={att.virtualRecordId || att.recordId}
              align="center"
              gap="1"
              role="button"
              title={att.recordName}
              onClick={() => handleAttachmentPreview(att)}
              style={{
                flexShrink: 0,
                padding: 'var(--space-1) var(--space-2)',
                backgroundColor: 'var(--olive-a2)',
                border: '1px solid var(--olive-3)',
                borderRadius: 'var(--radius-1)',
                maxWidth: '200px',
                cursor: 'pointer',
                transition: 'background-color 0.15s',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--olive-a4)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--olive-a2)';
              }}
            >
              <FileIcon
                extension={getMimeTypeExtension(att.mimeType) || att.extension || undefined}
                filename={att.recordName}
                size={14}
                fallbackIcon="insert_drive_file"
              />
              <Text
                size="1"
                style={{
                  color: 'var(--slate-11)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {att.recordName}
              </Text>
            </Flex>
          ))}
        </Flex>
      )}

      {/* Message bubble */}
      <Box
        style={{
          backgroundColor: 'var(--olive-a3)',
          borderRadius: 'var(--radius-4)',
          padding: 'var(--space-3) var(--space-4)',
          maxWidth: isMobile ? '92%' : '80%',
        }}
      >
        <Text
          as="p"
          size="2"
          weight="medium"
          style={{
            color: 'var(--slate-12)',
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {displayedQuestion}
        </Text>

        {isQuestionLong && (
          <Button
            color="gray"
            size="2"
            onClick={() => setIsExpanded((prev) => !prev)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '2px',
              marginTop: 'var(--space-2)',
              cursor: 'pointer',
              color: 'var(--slate-11)',
              background: 'none',
              padding: 0,
              fontFamily: 'inherit',
              height: 'auto',
            }}
          >
            {isExpanded ? 'Show less' : 'Show more'}
            <MaterialIcon
              name={isExpanded ? 'keyboard_arrow_up' : 'keyboard_arrow_down'}
              size={ICON_SIZES.PRIMARY}
            />
          </Button>
        )}
      </Box>

      {/* Timestamp + action affordances — below the bubble, revealed on hover */}
      <Flex align="center" gap="2" style={{ marginTop: 'var(--space-1)', minHeight: '20px' }}>
        <Flex
          align="center"
          gap="1"
          style={{
            opacity: isHovered ? 1 : 0,
            transition: 'opacity 0.15s ease',
            pointerEvents: isHovered ? 'auto' : 'none',
          }}
        >
          <Tooltip content={copied ? t('chat.copied', 'Copied!') : t('chat.copy', 'Copy')} side="bottom">
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={handleCopy}
              aria-label="Copy message"
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon
                name={copied ? 'check' : 'content_copy'}
                size={ICON_SIZES.SECONDARY}
                color="var(--slate-9)"
              />
            </IconButton>
          </Tooltip>
          {!isStreaming && messageId && (
            <Tooltip content={t('chat.editMessage', 'Edit message')} side="bottom">
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={handleEditQuery}
                aria-label="Edit message"
                style={{ cursor: 'pointer' }}
              >
                <MaterialIcon name="edit" size={ICON_SIZES.SECONDARY} color="var(--slate-9)" />
              </IconButton>
            </Tooltip>
          )}
        </Flex>
        {createdAt && (
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {formatMessageTime(createdAt)}
          </Text>
        )}
      </Flex>
    </Flex>
  );
}
