'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Flex, Box, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useTranslation } from 'react-i18next';
import type { ActiveMessageAction } from '@/chat/types';

interface MessageActionIndicatorProps {
  /** The active message action (regenerate or editQuery). */
  action: NonNullable<ActiveMessageAction>;
  /** Called to dismiss / close the indicator bar. */
  onDismiss: () => void;
  /** Called when the user presses Enter or clicks Send inside the panel. */
  onSubmit: (editedText?: string) => void;
}

/**
 * Slim indicator bar rendered inside ChatInputExpansionPanel when a
 * message action (Regenerate / Edit Query) is active.
 *
 * - **Regenerate**: shows a chip label + X dismiss. No textarea.
 * - **Edit Query**: shows a chip label + X dismiss. Clicking the chip
 *   expands an inline textarea pre-filled with the original query.
 *
 * Enter inside the textarea (Edit Query) or the parent ChatInput's
 * send button both route through `onSubmit`.
 */
export function MessageActionIndicator({
  action,
  onDismiss,
  onSubmit,
}: MessageActionIndicatorProps) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(false);
  const [editText, setEditText] = useState(
    action.type === 'editQuery' ? action.text : '',
  );
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [chipHovered, setChipHovered] = useState(false);

  // Auto-focus and auto-resize textarea when expanded
  useEffect(() => {
    if (isExpanded && textareaRef.current) {
      textareaRef.current.focus();
      // Move cursor to end
      const len = textareaRef.current.value.length;
      textareaRef.current.setSelectionRange(len, len);
      // Auto-resize
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [isExpanded]);

  // Reset expanded state if action changes
  useEffect(() => {
    setIsExpanded(false);
    setEditText(action.type === 'editQuery' ? action.text : '');
  }, [action]);

  const handleChipClick = useCallback(() => {
    if (action.type === 'editQuery' && !isExpanded) {
      setIsExpanded(false);
    }
  }, [action.type, isExpanded]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        onSubmit(editText);
      }
    },
    [editText, onSubmit],
  );

  const label =
    action.type === 'regenerate'
      ? t('chat.regenerateResponse', 'Regenerate response')
      : t('chat.editQuery', 'Edit Query');

  return (
    <Flex direction="column" gap="2" style={{ width: '100%' }}>
      {/* Chip bar: label + dismiss */}
      <Flex align="center" justify="between" style={{ minHeight: 'var(--space-6)' }}>
        <Box
          onClick={handleChipClick}
          onMouseEnter={() => setChipHovered(true)}
          onMouseLeave={() => setChipHovered(false)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--space-1)',
            padding: '4px 10px',
            borderRadius: 'var(--radius-2)',
            backgroundColor: chipHovered && action.type === 'editQuery'
              ? 'var(--olive-4)'
              : 'var(--olive-3)',
            cursor: action.type === 'editQuery' ? 'pointer' : 'default',
            transition: 'background-color 0.15s ease',
          }}
        >
          <Text
            size="2"
            weight="medium"
            style={{ color: 'var(--slate-12)', whiteSpace: 'nowrap' }}
          >
            {label}
          </Text>
        </Box>

        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onDismiss}
          style={{ margin: 0, cursor: 'pointer', flexShrink: 0 }}
        >
          <MaterialIcon
            name="close"
            size={ICON_SIZES.SECONDARY}
            color="var(--slate-11)"
          />
        </IconButton>
      </Flex>

      {/* Expanded textarea for Edit Query */}
      {action.type === 'editQuery' && isExpanded && (
        <textarea
          ref={textareaRef}
          value={editText}
          onChange={(e) => {
            setEditText(e.target.value);
            // Auto-resize
            const target = e.target;
            target.style.height = 'auto';
            target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
          }}
          onKeyDown={handleKeyDown}
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
            overflow: editText.split('\n').length > 5 ? 'auto' : 'hidden',
          }}
        />
      )}
    </Flex>
  );
}
