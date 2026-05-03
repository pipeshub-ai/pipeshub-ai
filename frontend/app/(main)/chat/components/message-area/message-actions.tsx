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
import type { ModelInfo, AppliedFilters } from '@/chat/types';
import type { CitationMaps } from './response-tabs/citations';
import { useCommandStore } from '@/lib/store/command-store';
import { toast } from '@/lib/store/toast-store';
import { useChatSpeechSynthesis } from '@/lib/hooks/use-chat-speech-synthesis';
import { ChatApi, type FeedbackPayload } from '../../api';
import { useChatStore } from '../../store';

// ========================================
// Types & Constants
// ========================================

type FeedbackValue = 'like' | 'dislike';

interface FeedbackCategory {
  value: string;
  i18nKey: string;
}

const LIKE_CATEGORIES: FeedbackCategory[] = [
  { value: 'excellent_answer', i18nKey: 'chat.feedbackCategoryExcellentAnswer' },
  { value: 'well_explained', i18nKey: 'chat.feedbackCategoryWellExplained' },
  { value: 'helpful_citations', i18nKey: 'chat.feedbackCategoryHelpfulCitations' },
  { value: 'other', i18nKey: 'chat.feedbackCategoryOther' },
];

const DISLIKE_CATEGORIES: FeedbackCategory[] = [
  { value: 'incorrect_information', i18nKey: 'chat.feedbackCategoryIncorrectInfo' },
  { value: 'missing_information', i18nKey: 'chat.feedbackCategoryMissingInfo' },
  { value: 'irrelevant_information', i18nKey: 'chat.feedbackCategoryIrrelevantInfo' },
  { value: 'unclear_explanation', i18nKey: 'chat.feedbackCategoryUnclearExplanation' },
  { value: 'poor_citations', i18nKey: 'chat.feedbackCategoryPoorCitations' },
  { value: 'other', i18nKey: 'chat.feedbackCategoryOther' },
];

const OTHER_VALUE = 'other';

interface MessageActionsProps {
  /** The raw markdown content of the message */
  content: string;
  /** Citation maps for resolving [N] markers in copied markdown */
  citationMaps?: CitationMaps;
  /** Model info for displaying mode + model labels */
  modelInfo?: ModelInfo;
  /** Whether the message is currently streaming */
  isStreaming?: boolean;
  /** Backend _id of the bot_response (used for regenerate) */
  messageId?: string;
  /** The original question text (used for regenerate to populate input) */
  question?: string;
  /** Whether this is the last bot message in the conversation */
  isLastMessage?: boolean;
  /** Filters that were active when this message was originally sent */
  appliedFilters?: AppliedFilters;
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

async function submitFeedbackToApi(
  messageId: string,
  payload: FeedbackPayload,
): Promise<void> {
  const state = useChatStore.getState();
  const slot = state.activeSlotId ? state.slots[state.activeSlotId] : null;
  const convId = slot?.convId;
  if (!convId) return;

  const agentId = slot?.threadAgentId;
  if (agentId) {
    await ChatApi.submitAgentFeedback(agentId, convId, messageId, payload);
  } else {
    await ChatApi.submitFeedback(convId, messageId, payload);
  }
}

// ========================================
// Component
// ========================================

export function MessageActions({
  content,
  citationMaps,
  modelInfo,
  isStreaming = false,
  messageId,
  question,
  isLastMessage = false,
  appliedFilters,
}: MessageActionsProps) {
  const [feedbackGiven, setFeedbackGiven] = useState<FeedbackValue | null>(null);
  const [copyPopoverOpen, setCopyPopoverOpen] = useState(false);
  const [copiedTooltipOpen, setCopiedTooltipOpen] = useState(false);
  const [copiedMessage, setCopiedMessage] = useState('');
  const copiedTooltipTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [readAloudHovered, setReadAloudHovered] = useState(false);
  const { t, i18n } = useTranslation();

  // ── Like popover state ──
  const [likeOpen, setLikeOpen] = useState(false);
  const [likeShowOther, setLikeShowOther] = useState(false);
  const [likeComment, setLikeComment] = useState('');
  const [likeSubmitting, setLikeSubmitting] = useState(false);

  // ── Dislike popover state ──
  const [dislikeOpen, setDislikeOpen] = useState(false);
  const [dislikeShowOther, setDislikeShowOther] = useState(false);
  const [dislikeComment, setDislikeComment] = useState('');
  const [dislikeSubmitting, setDislikeSubmitting] = useState(false);

  const switchingRef = useRef(false);

  const { isSpeaking, isSupported: isTtsSupported, speak, stop: stopSpeech } = useChatSpeechSynthesis({
    lang: i18n.language,
    onError: (error) => {
      if (error === 'not-supported') {
        toast.error(t('chat.ttsNotSupported'));
      } else {
        toast.error(t('chat.ttsFailed'));
      }
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

  // ── Thumbs up ──
  const handleLikeClick = useCallback(() => {
    if (!messageId) return;
    if (dislikeOpen) {
      switchingRef.current = true;
      setDislikeOpen(false);
    }
    setLikeShowOther(false);
    setLikeComment('');
    setLikeOpen(true);
  }, [messageId, dislikeOpen]);

  const handleLikeChipClick = useCallback(async (cat: FeedbackCategory) => {
    if (!messageId) return;
    if (cat.value === OTHER_VALUE) {
      setLikeShowOther(true);
      return;
    }
    setFeedbackGiven('like');
    setLikeOpen(false);
    try {
      await submitFeedbackToApi(messageId, { isHelpful: true, categories: [cat.value] });
      toast.success(t('chat.thankYouForFeedback'), { description: t('chat.feedbackHelpsImprove') });
    } catch {
      toast.error(t('chat.feedbackError', 'Failed to submit feedback'));
    }
  }, [messageId, t]);

  const handleLikeOtherSubmit = useCallback(async () => {
    if (!messageId) return;
    setLikeSubmitting(true);
    try {
      await submitFeedbackToApi(messageId, {
        isHelpful: true,
        categories: [OTHER_VALUE],
        ...(likeComment.trim() ? { comments: { positive: likeComment.trim() } } : {}),
      });
      setFeedbackGiven('like');
      setLikeOpen(false);
      toast.success(t('chat.thankYouForFeedback'), { description: t('chat.feedbackHelpsImprove') });
    } catch {
      toast.error(t('chat.feedbackError', 'Failed to submit feedback'));
    } finally {
      setLikeSubmitting(false);
    }
  }, [messageId, likeComment, t]);

  const handleLikePopoverClose = useCallback(() => {
    if (switchingRef.current) {
      switchingRef.current = false;
      return;
    }
    setLikeShowOther(false);
    setLikeComment('');
    setLikeOpen(false);
  }, []);

  // ── Thumbs down ──
  const handleDislikeClick = useCallback(() => {
    if (!messageId) return;
    if (likeOpen) {
      switchingRef.current = true;
      setLikeOpen(false);
    }
    setDislikeShowOther(false);
    setDislikeComment('');
    setDislikeOpen(true);
  }, [messageId, likeOpen]);

  const handleDislikeChipClick = useCallback(async (cat: FeedbackCategory) => {
    if (!messageId) return;
    if (cat.value === OTHER_VALUE) {
      setDislikeShowOther(true);
      return;
    }
    setFeedbackGiven('dislike');
    setDislikeOpen(false);
    try {
      await submitFeedbackToApi(messageId, { isHelpful: false, categories: [cat.value] });
      toast.success(t('chat.thankYouForFeedback'), { description: t('chat.feedbackHelpsImprove') });
    } catch {
      toast.error(t('chat.feedbackError', 'Failed to submit feedback'));
    }
  }, [messageId, t]);

  const handleDislikeOtherSubmit = useCallback(async () => {
    if (!messageId) return;
    setDislikeSubmitting(true);
    try {
      await submitFeedbackToApi(messageId, {
        isHelpful: false,
        categories: [OTHER_VALUE],
        ...(dislikeComment.trim() ? { comments: { negative: dislikeComment.trim() } } : {}),
      });
      setFeedbackGiven('dislike');
      setDislikeOpen(false);
      toast.success(t('chat.thankYouForFeedback'), { description: t('chat.feedbackHelpsImprove') });
    } catch {
      toast.error(t('chat.feedbackError', 'Failed to submit feedback'));
    } finally {
      setDislikeSubmitting(false);
    }
  }, [messageId, dislikeComment, t]);

  const handleDislikePopoverClose = useCallback(() => {
    if (switchingRef.current) {
      switchingRef.current = false;
      return;
    }
    setDislikeShowOther(false);
    setDislikeComment('');
    setDislikeOpen(false);
  }, []);

  const handleRegenerate = useCallback(() => {
    if (!messageId) return;
    useCommandStore.getState().dispatch('showRegenBar', { messageId, text: question, appliedFilters });
  }, [messageId, question, appliedFilters]);

  const copyToClipboard = useCallback(
    async (text: string, message: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopyPopoverOpen(false);
        setCopiedMessage(message);
        setCopiedTooltipOpen(true);

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
        {/* ── Thumbs up with optional comment popover ── */}
        {messageId && (
          <Popover.Root open={likeOpen} onOpenChange={(open) => { if (!open) handleLikePopoverClose(); }}>
            <Tooltip content={t('chat.like', 'Helpful')} side="top">
              <Popover.Trigger>
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="2"
                  onClick={handleLikeClick}
                  style={{
                    borderRadius: 'var(--radius-1)',
                    margin: 0,
                    cursor: 'pointer',
                  }}
                >
                  <MaterialIcon
                    name={feedbackGiven === 'like' ? 'thumb_up' : 'thumb_up_off_alt'}
                    size={ICON_SIZES.SECONDARY}
                    variant={feedbackGiven === 'like' ? 'filled' : 'outlined'}
                    color={feedbackGiven === 'like' ? 'var(--jade-11)' : 'var(--slate-11)'}
                  />
                </IconButton>
              </Popover.Trigger>
            </Tooltip>
            <Popover.Content
              side="top"
              align="start"
              style={{
                padding: '12px 14px',
                borderRadius: '10px',
                border: '1px solid var(--gray-a4)',
                background: 'var(--color-background)',
                width: 'min(360px, calc(100vw - 32px))',
                maxWidth: '360px',
                boxShadow: '0 2px 12px rgba(0, 0, 0, 0.06), 0 1px 3px rgba(0, 0, 0, 0.04)',
                overflow: 'hidden',
              }}
            >
              {likeShowOther ? (
                <Flex direction="column" gap="2">
                  <Flex align="center" gap="1">
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={() => setLikeShowOther(false)}
                      style={{ cursor: 'pointer', flexShrink: 0 }}
                    >
                      <MaterialIcon name="arrow_back" size={16} color="var(--slate-11)" />
                    </IconButton>
                    <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
                      {t('chat.feedbackLikeTitle', 'What did you like about this response? (optional)')}
                    </Text>
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={handleLikePopoverClose}
                      style={{ cursor: 'pointer', flexShrink: 0 }}
                    >
                      <MaterialIcon name="close" size={16} color="var(--slate-9)" />
                    </IconButton>
                  </Flex>
                  <Box style={{ position: 'relative' }}>
                    <textarea
                      value={likeComment}
                      onChange={(e) => setLikeComment(e.target.value)}
                      placeholder={t('chat.feedbackOtherPlaceholder', 'Add specific details...')}
                      rows={3}
                      autoFocus
                      style={{
                        width: '100%',
                        padding: '10px 36px 10px 12px',
                        borderRadius: '8px',
                        border: '1px solid var(--gray-a5)',
                        background: 'var(--gray-a2)',
                        color: 'var(--slate-12)',
                        fontSize: '13px',
                        lineHeight: '1.5',
                        fontFamily: 'inherit',
                        resize: 'none',
                        outline: 'none',
                        boxSizing: 'border-box',
                      }}
                      onFocus={(e) => { e.target.style.borderColor = 'var(--gray-a7)'; }}
                      onBlur={(e) => { e.target.style.borderColor = 'var(--gray-a5)'; }}
                    />
                    <IconButton
                      variant="solid"
                      color="gray"
                      highContrast
                      size="1"
                      disabled={likeSubmitting}
                      onClick={handleLikeOtherSubmit}
                      style={{
                        position: 'absolute',
                        right: '8px',
                        bottom: '14px',
                        cursor: likeSubmitting ? 'default' : 'pointer',
                        borderRadius: '50%',
                      }}
                    >
                      <MaterialIcon name="arrow_upward" size={14} color="var(--gray-1)" />
                    </IconButton>
                  </Box>
                </Flex>
              ) : (
                <Flex direction="column" gap="2">
                  <Flex align="center" justify="between">
                    <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                      {t('chat.feedbackLikeTitle', 'What did you like about this response? (optional)')}
                    </Text>
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={handleLikePopoverClose}
                      style={{ cursor: 'pointer', flexShrink: 0, marginLeft: '4px' }}
                    >
                      <MaterialIcon name="close" size={16} color="var(--slate-9)" />
                    </IconButton>
                  </Flex>
                  <Flex wrap="wrap" gap="2">
                    {LIKE_CATEGORIES.map((cat) => (
                      <FeedbackChip
                        key={cat.value}
                        label={t(cat.i18nKey)}
                        selected={false}
                        onClick={() => handleLikeChipClick(cat)}
                      />
                    ))}
                  </Flex>
                </Flex>
              )}
            </Popover.Content>
          </Popover.Root>
        )}

        {/* ── Thumbs down with category chips + optional text ── */}
        {messageId && (
          <Popover.Root open={dislikeOpen} onOpenChange={(open) => { if (!open) handleDislikePopoverClose(); }}>
            <Tooltip content={t('chat.dislike', 'Not helpful')} side="top">
              <Popover.Trigger>
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="2"
                  onClick={handleDislikeClick}
                  style={{
                    borderRadius: 'var(--radius-1)',
                    margin: 0,
                    cursor: 'pointer',
                  }}
                >
                  <MaterialIcon
                    name={feedbackGiven === 'dislike' ? 'thumb_down' : 'thumb_down_off_alt'}
                    size={ICON_SIZES.SECONDARY}
                    variant={feedbackGiven === 'dislike' ? 'filled' : 'outlined'}
                    color={feedbackGiven === 'dislike' ? 'var(--red-11)' : 'var(--slate-11)'}
                  />
                </IconButton>
              </Popover.Trigger>
            </Tooltip>
            <Popover.Content
              side="top"
              align="start"
              style={{
                padding: '12px 14px',
                borderRadius: '10px',
                border: '1px solid var(--gray-a4)',
                background: 'var(--color-background)',
                width: 'min(360px, calc(100vw - 32px))',
                maxWidth: '360px',
                boxShadow: '0 2px 12px rgba(0, 0, 0, 0.06), 0 1px 3px rgba(0, 0, 0, 0.04)',
                overflow: 'hidden',
              }}
            >
              {dislikeShowOther ? (
                <Flex direction="column" gap="2">
                  <Flex align="center" gap="1">
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={() => setDislikeShowOther(false)}
                      style={{ cursor: 'pointer', flexShrink: 0 }}
                    >
                      <MaterialIcon name="arrow_back" size={16} color="var(--slate-11)" />
                    </IconButton>
                    <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
                      {t('chat.feedbackDislikeTitle', "What didn't you like about this response?")}
                    </Text>
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={handleDislikePopoverClose}
                      style={{ cursor: 'pointer', flexShrink: 0 }}
                    >
                      <MaterialIcon name="close" size={16} color="var(--slate-9)" />
                    </IconButton>
                  </Flex>
                  <Box style={{ position: 'relative' }}>
                    <textarea
                      value={dislikeComment}
                      onChange={(e) => setDislikeComment(e.target.value)}
                      placeholder={t('chat.feedbackDislikeOtherPlaceholder', 'Please describe the issue...')}
                      rows={3}
                      autoFocus
                      style={{
                        width: '100%',
                        padding: '10px 36px 10px 12px',
                        borderRadius: '8px',
                        border: '1px solid var(--gray-a5)',
                        background: 'var(--gray-a2)',
                        color: 'var(--slate-12)',
                        fontSize: '13px',
                        lineHeight: '1.5',
                        fontFamily: 'inherit',
                        resize: 'none',
                        outline: 'none',
                        boxSizing: 'border-box',
                      }}
                      onFocus={(e) => { e.target.style.borderColor = 'var(--gray-a7)'; }}
                      onBlur={(e) => { e.target.style.borderColor = 'var(--gray-a5)'; }}
                    />
                    <IconButton
                      variant="solid"
                      color="gray"
                      highContrast
                      size="1"
                      disabled={dislikeSubmitting}
                      onClick={handleDislikeOtherSubmit}
                      style={{
                        position: 'absolute',
                        right: '8px',
                        bottom: '14px',
                        cursor: dislikeSubmitting ? 'default' : 'pointer',
                        borderRadius: '50%',
                      }}
                    >
                      <MaterialIcon name="arrow_upward" size={14} color="var(--gray-1)" />
                    </IconButton>
                  </Box>
                </Flex>
              ) : (
                <Flex direction="column" gap="2">
                  <Flex align="center" justify="between">
                    <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
                      {t('chat.feedbackDislikeTitle', "What didn't you like about this response?")}
                    </Text>
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={handleDislikePopoverClose}
                      style={{ cursor: 'pointer', flexShrink: 0, marginLeft: '4px' }}
                    >
                      <MaterialIcon name="close" size={16} color="var(--slate-9)" />
                    </IconButton>
                  </Flex>
                  <Flex wrap="wrap" gap="2">
                    {DISLIKE_CATEGORIES.map((cat) => (
                      <FeedbackChip
                        key={cat.value}
                        label={t(cat.i18nKey)}
                        selected={false}
                        onClick={() => handleDislikeChipClick(cat)}
                      />
                    ))}
                  </Flex>
                </Flex>
              )}
            </Popover.Content>
          </Popover.Root>
        )}

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
        {isLastMessage && messageId &&(
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

interface FeedbackChipProps {
  label: string;
  selected: boolean;
  onClick: () => void;
}

function FeedbackChip({ label, selected, onClick }: FeedbackChipProps) {
  const [hovered, setHovered] = useState(false);

  return (
    <Box
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '6px 14px',
        borderRadius: '16px',
        border: `1px solid ${selected ? 'var(--accent-8)' : 'var(--gray-a5)'}`,
        background: selected
          ? 'var(--accent-3)'
          : hovered
            ? 'var(--gray-a3)'
            : 'transparent',
        cursor: 'pointer',
        transition: 'all 0.12s ease',
        userSelect: 'none',
      }}
    >
      <Text
        size="2"
        weight="medium"
        style={{
          color: selected ? 'var(--accent-11)' : 'var(--slate-12)',
          whiteSpace: 'nowrap',
          fontSize: '13px',
        }}
      >
        {label}
      </Text>
    </Box>
  );
}
