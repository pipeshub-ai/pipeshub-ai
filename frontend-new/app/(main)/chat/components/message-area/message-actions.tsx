'use client';

import React, { useState, useCallback, useRef } from 'react';
import { Flex, Box, Text, IconButton, Popover, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import {
  stripMarkdownAndCitations,
  formatChatMode,
} from '@/lib/utils/formatters';
import type { ModelInfo } from '@/chat/types';
import type { CitationMaps } from './response-tabs/citations';
import { useCommandStore } from '@/lib/store/command-store';
import { toast } from '@/lib/store/toast-store';
import { useSpeechSynthesis } from '@/lib/hooks/use-speech-synthesis';

// ========================================
// Types
// ========================================

type FeedbackValue = 'like' | 'dislike';

interface FeedbackInfo {
  value?: FeedbackValue;
}

interface MessageActionsProps {
  /** The raw markdown content of the message */
  content: string;
  /** Citation maps for resolving [N] markers in copied markdown */
  citationMaps?: CitationMaps;
  /** Model info for displaying mode + model labels */
  modelInfo?: ModelInfo;
  /** Feedback state from API */
  feedbackInfo?: FeedbackInfo;
  /** Whether the message is currently streaming */
  isStreaming?: boolean;
  /** Backend _id of the bot_response (used for regenerate) */
  messageId?: string;
  /** The original question text (used for regenerate to populate input) */
  question?: string;
  /** Whether this is the last bot message in the conversation */
  isLastMessage?: boolean;
}

/**
 * Replace [N] citation markers in markdown with [recordName](webUrl) links
 * using the resolved citation data. Markers without a usable URL are removed.
 */
function resolveMarkdownCitations(text: string, citationMaps?: CitationMaps): string {
  if (!citationMaps) return text;
  return text.replace(/\[{1,2}(\d+)\]{1,2}/g, (_match, numStr) => {
    const chunkIndex = parseInt(numStr, 10);
    const citationId = citationMaps.citationsOrder[chunkIndex];
    const citation = citationId ? citationMaps.citations[citationId] : undefined;
    if (!citation) return '';
    if (citation.webUrl && !citation.hideWeburl) {
      const name = citation.recordName.replace(/\.[^/.]+$/, '');
      return `[${name}](${citation.webUrl})`;
    }
    return '';
  });
}

// ========================================
// Component
// ========================================

export function MessageActions({
  content,
  citationMaps,
  modelInfo,
  feedbackInfo,
  isStreaming = false,
  messageId,
  question,
  isLastMessage = false,
}: MessageActionsProps) {
  const [feedback, setFeedback] = useState<FeedbackValue | undefined>(
    feedbackInfo?.value,
  );
  const [copyPopoverOpen, setCopyPopoverOpen] = useState(false);
  const [copiedTooltipOpen, setCopiedTooltipOpen] = useState(false);
  const [copiedMessage, setCopiedMessage] = useState('');
  const copiedTooltipTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [likeHovered, setLikeHovered] = useState(false);
  const [dislikeHovered, setDislikeHovered] = useState(false);
  const [readAloudHovered, setReadAloudHovered] = useState(false);
  const { t, i18n } = useTranslation();

  const { isSpeaking, isSupported: isTtsSupported, speak, stop: stopSpeech } = useSpeechSynthesis({
    lang: i18n.language,
    onError: () => {
      toast.error(t('chat.ttsNotSupported'));
    },
  });

  const handleReadAloud = useCallback(() => {
    if (isSpeaking) {
      stopSpeech();
    } else {
      const plainText = stripMarkdownAndCitations(content);
      speak(plainText);
    }
  }, [isSpeaking, stopSpeech, content, speak]);

  const handleFeedback = useCallback((value: FeedbackValue) => {
    setFeedback((prev) => {
      const newValue = prev === value ? undefined : value;
      if (newValue === 'like' || newValue === 'dislike') {
        toast.success(t('chat.thankYouForFeedback'), {
          description: t('chat.feedbackHelpsImprove'),
        });
      }
      return newValue;
    });
  }, [t]);

  const handleRegenerate = useCallback(() => {
    if (!messageId) return;
    useCommandStore.getState().dispatch('showRegenBar', { messageId, text: question });
  }, [messageId, question]);

  const copyToClipboard = useCallback(
    async (text: string, message: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopyPopoverOpen(false);
        setCopiedMessage(message);
        setCopiedTooltipOpen(true);

        // Auto-dismiss tooltip after 2s
        if (copiedTooltipTimerRef.current) clearTimeout(copiedTooltipTimerRef.current);
        copiedTooltipTimerRef.current = setTimeout(() => {
          setCopiedTooltipOpen(false);
        }, 2000);
      } catch {
        // Clipboard API may fail in some contexts
      }
    },
    [],
  );

  const handleCopyMarkdown = useCallback(() => {
    const resolved = resolveMarkdownCitations(content, citationMaps);
    copyToClipboard(resolved, t('chat.copiedAsMarkdown'));
  }, [content, citationMaps, copyToClipboard, t]);

  const handleCopyText = useCallback(() => {
    const plainText = stripMarkdownAndCitations(content);
    copyToClipboard(plainText, t('chat.copiedAsText'));
  }, [content, copyToClipboard, t]);

  // Don't show actions while streaming — placed after all hooks
  if (isStreaming) return null;

  const chatModeLabel = formatChatMode(modelInfo?.chatMode);
  const modelName = modelInfo?.modelName || '';

  return (
    <>
      <Flex
        align="center"
        justify="between"
        style={{
          width: '100%',
          marginTop: 'var(--space-1)',
          paddingBottom: 'var(--space-4)',
          animation: 'msgActionsIn 150ms ease-out both',
        }}
      >
      {/* ── Left: Action buttons ── */}
      <Flex align="center" gap="1">
        {/* Thumbs up */}
        <Tooltip content={t('chat.like')} side="top">
          <IconButton
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => handleFeedback('like')}
            onMouseEnter={() => setLikeHovered(true)}
            onMouseLeave={() => setLikeHovered(false)}
            style={{
              borderRadius: 'var(--radius-1)',
              margin: 0,
              cursor: 'pointer',
              backgroundColor: likeHovered ? 'var(--slate-a3)' : 'transparent',
            }}
          >
            <MaterialIcon
              name={feedback === 'like' ? 'thumb_up' : 'thumb_up_off_alt'}
              size={ICON_SIZES.SECONDARY}
              variant={feedback === 'like' ? 'filled' : 'outlined'}
              color={feedback === 'like' ? 'var(--emerald-11)' : 'var(--slate-11)'}
            />
          </IconButton>
        </Tooltip>

        {/* Thumbs down */}
        <Tooltip content={t('chat.dislike')} side="top">
          <IconButton
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => handleFeedback('dislike')}
            onMouseEnter={() => setDislikeHovered(true)}
            onMouseLeave={() => setDislikeHovered(false)}
            style={{
              borderRadius: 'var(--radius-1)',
              margin: 0,
              cursor: 'pointer',
              backgroundColor: dislikeHovered ? 'var(--slate-a3)' : 'transparent',
            }}
          >
            <MaterialIcon
              name={
                feedback === 'dislike'
                  ? 'thumb_down'
                  : 'thumb_down_off_alt'
              }
              size={ICON_SIZES.SECONDARY}
              variant={feedback === 'dislike' ? 'filled' : 'outlined'}
              color={feedback === 'dislike' ? 'var(--red-11)' : 'var(--slate-11)'}
            />
          </IconButton>
        </Tooltip>

        {/* Copy with popover & copied tooltip */}
        <Tooltip
          content={copiedTooltipOpen ? copiedMessage : t('chat.copy')}
          open={copiedTooltipOpen ? true : undefined}
          side="top"
          align="center"
          delayDuration={0}
        >
          <Box style={{ display: 'inline-flex', position: 'relative' }}>
            <Popover.Root
              open={copyPopoverOpen}
              onOpenChange={setCopyPopoverOpen}
            >
              <Popover.Trigger>
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="2"
                  style={{
                    margin: 0,
                    cursor: 'pointer',
                    color: 'var(--slate-9)',
                    borderRadius: 'var(--radius-1)',
                  }}
                >
                  <Box
                    style={{
                      display: 'grid',
                      placeItems: 'center',
                    }}
                  >
                    <MaterialIcon
                      name="content_copy"
                      size={ICON_SIZES.SECONDARY}
                      color="var(--slate-11)"
                      style={{
                        gridArea: '1 / 1',
                        transition: 'opacity 0.2s ease, transform 0.2s ease',
                        opacity: copiedTooltipOpen ? 0 : 1,
                        transform: copiedTooltipOpen ? 'scale(0.5)' : 'scale(1)',
                      }}
                    />
                    <MaterialIcon
                      name="check"
                      size={ICON_SIZES.SECONDARY}
                      color="var(--slate-11)"
                      style={{
                        gridArea: '1 / 1',
                        transition: 'opacity 0.2s ease, transform 0.2s ease',
                        opacity: copiedTooltipOpen ? 1 : 0,
                        transform: copiedTooltipOpen ? 'scale(1)' : 'scale(0.5)',
                      }}
                    />
                  </Box>
                </IconButton>
              </Popover.Trigger>

              <Popover.Content
                side="bottom"
                align="start"
                size="1"
                style={{
                  padding: 'var(--space-1)',
                  borderRadius: 'var(--radius-1)',
                  border: '1px solid var(--olive-3)',
                  background: 'var(--olive-2)',
                  backdropFilter: 'blur(25px)',
                  gap: 'var(--space-1)',
                }}
              >
                <Flex direction="column" gap="1">
                  <CopyOption
                    label={t('chat.markdownWithCitations')}
                    onClick={handleCopyMarkdown}
                  />
                  <CopyOption
                    label={t('chat.onlyTextWithoutCitations')}
                    onClick={handleCopyText}
                  />
                </Flex>
              </Popover.Content>
            </Popover.Root>
          </Box>
        </Tooltip>

        {/* Regenerate - only show for the last message */}
        {isLastMessage && (
          <Tooltip content={t('chat.regenerate')} side="top">
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              disabled={!messageId}
              onClick={handleRegenerate}
              style={{
                margin: 0,
                cursor: messageId ? 'pointer' : 'default',
                color: 'var(--slate-9)',
                borderRadius: 'var(--radius-1)',
              }}
            >
              <MaterialIcon name="refresh" size={ICON_SIZES.PRIMARY} color="var(--slate-11)" />
            </IconButton>
          </Tooltip>
        )}

        {/* Read aloud */}
        {isTtsSupported && (
          <Tooltip content={isSpeaking ? t('chat.stopReading') : t('chat.readAloud')} side="top">
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={handleReadAloud}
              onMouseEnter={() => setReadAloudHovered(true)}
              onMouseLeave={() => setReadAloudHovered(false)}
              style={{
                margin: 0,
                cursor: 'pointer',
                borderRadius: 'var(--radius-1)',
                backgroundColor: isSpeaking ? 'var(--accent-a4)' : readAloudHovered ? 'var(--slate-a3)' : 'transparent',
                color: isSpeaking ? 'var(--accent-11)' : 'var(--slate-9)',
              }}
            >
              <MaterialIcon
                name={isSpeaking ? 'stop' : 'volume_up'}
                size={ICON_SIZES.MINIMAL}
                color={isSpeaking ? 'var(--accent-11)' : 'var(--slate-11)'}
              />
            </IconButton>
          </Tooltip>
        )}
      </Flex>

      {/* ── Right: Model info labels ── */}
      <Flex align="center">
        {/* Chat mode label */}
        {chatModeLabel && (
          <Flex
            align="center"
            justify="center"
            style={{
              height: '24px',
              padding: '0 var(--space-2)',
              borderRadius: 'var(--radius-1)',
            }}
          >
            <Text
              size="1"
              style={{
                color: 'var(--slate-11)',
                lineHeight: 'var(--line-height-1)',
                whiteSpace: 'nowrap',
              }}
            >
              {chatModeLabel}
            </Text>
          </Flex>
        )}

        {/* Model name with icon */}
        {modelName && (
          <Flex
            align="center"
            justify="center"
            gap="1"
            style={{
              height: '24px',
              padding: '0 var(--space-2)',
              borderRadius: 'var(--radius-1)',
            }}
          >
            <MaterialIcon
              name="memory"
              size={ICON_SIZES.PRIMARY}
              color="var(--slate-11)"
            />
            <Text
              size="1"
              style={{
                color: 'var(--slate-11)',
                lineHeight: 'var(--line-height-1)',
                whiteSpace: 'nowrap',
              }}
            >
              {modelName}
            </Text>
          </Flex>
        )}
      </Flex>
      </Flex>

    </>
  );
}

// ========================================
// Sub-components
// ========================================

interface CopyOptionProps {
  label: string;
  onClick: () => void;
}

function CopyOption({ label, onClick }: CopyOptionProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Box
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        height: '24px',
        paddingLeft: 'var(--space-2)',
        paddingRight: 'var(--space-2)',
        borderRadius: 'var(--radius-1)',
        cursor: 'pointer',
        backgroundColor: isHovered ? 'var(--slate-a3)' : 'transparent',
        transition: 'background-color 0.1s ease',
        width: '100%',
      }}
    >
      <Text
        size="1"
        style={{
          color: 'var(--slate-11)',
          whiteSpace: 'nowrap',
          lineHeight: '16px',
          letterSpacing: '0.04px',
        }}
      >
        {label}
      </Text>
    </Box>
  );
}
