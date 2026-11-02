'use client';

import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

export interface TagItem {
  id: string;
  value: string;
  isValid?: boolean;
  isHighlighted?: boolean;
  isEditing?: boolean;
}

interface TagInputProps {
  /** Current tags */
  tags: TagItem[];
  /** Callback when tags change. Optional when disabled. */
  onTagsChange?: (tags: TagItem[]) => void;
  /** Placeholder text when no tags are present */
  placeholder?: string;
  /** Validate a value before creating a tag. Return null if valid, error string otherwise. */
  validate?: (value: string) => string | null;
  /** Error message to display below */
  error?: string;
  /** When true, tags are read-only — no adding, removing, or editing */
  disabled?: boolean;
  /** Single-line-ish field for forms that stack several tag inputs (vs. the tall email-invite box). */
  compact?: boolean;
  /**
   * Values are free text that may contain spaces and commas (e.g. folder paths):
   * only Enter ends a tag, a paste splits on newlines, and duplicates are
   * compared case-sensitively.
   */
  freeText?: boolean;
  /** Id of the text input, so a `<label htmlFor>` names it. */
  id?: string;
}

// ========================================
// Component
// ========================================

export function TagInput({
  tags,
  onTagsChange,
  placeholder = 'Enter a value',
  validate,
  error,
  disabled = false,
  compact = false,
  freeText = false,
  id,
}: TagInputProps) {
  // No-op fallback when disabled or no handler provided
  const handleTagsChange = onTagsChange ?? (() => {});
  const [inputValue, setInputValue] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const [_editingTagId, setEditingTagId] = useState<string | null>(null);
  // Kept here, not on the tags: a parent that rebuilds its tags from plain values drops the flag,
  // and the second Backspace would never remove the chip.
  const [lastHighlighted, setLastHighlighted] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when tags change
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [tags]);

  const isDuplicate = useCallback(
    (a: string, b: string) => (freeText ? a === b : a.toLowerCase() === b.toLowerCase()),
    [freeText]
  );

  const addTag = useCallback(
    (value: string) => {
      const trimmed = value.trim();
      if (!trimmed) return;

      // Check for duplicates
      if (tags.some((t) => isDuplicate(t.value, trimmed))) {
        setInputValue('');
        return;
      }

      const isValid = validate ? validate(trimmed) === null : true;
      const newTag: TagItem = {
        id: `tag-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        value: trimmed,
        isValid,
      };

      handleTagsChange([...tags, newTag]);
      setInputValue('');
      setEditingTagId(null);
    },
    [tags, onTagsChange, validate, isDuplicate]
  );

  const removeTag = useCallback(
    (tagId: string) => {
      setLastHighlighted(false);
      handleTagsChange(tags.filter((t) => t.id !== tagId));
    },
    [tags, handleTagsChange]
  );

  // The × that was clicked leaves the DOM: focus the next chip's ×, else the input.
  const removeTagFromButton = (index: number) => {
    removeTag(tags[index].id);
    setTimeout(() => {
      const next = containerRef.current?.querySelectorAll<HTMLElement>('[data-tag-remove]')[index];
      (next ?? inputRef.current)?.focus();
    }, 0);
  };

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      // Keys that confirm an IME candidate (CJK input) belong to the IME; Safari
      // reports the confirming Enter with isComposing false but keyCode 229.
      if (e.nativeEvent.isComposing || e.keyCode === 229) return;
      // Tab is not here: it moves focus on, and leaving the field turns its text into a tag (onBlur).
      const endsTag = freeText
        ? e.key === 'Enter'
        : e.key === 'Enter' || e.key === ',' || e.key === ' ';
      if (endsTag) {
        e.preventDefault();
        addTag(inputValue);
      } else if (
        (e.key === 'Backspace' || e.key === 'ArrowLeft') &&
        inputValue === '' &&
        tags.length > 0
      ) {
        // The first Backspace (or left arrow) highlights the last tag, the second Backspace removes it.
        if (e.key === 'Backspace' && lastHighlighted) removeTag(tags[tags.length - 1].id);
        else setLastHighlighted(true);
      } else if (lastHighlighted && e.key !== 'Tab' && e.key !== 'Shift') {
        setLastHighlighted(false);
      }
    },
    [inputValue, tags, addTag, removeTag, freeText, lastHighlighted]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLInputElement>) => {
      e.preventDefault();
      const pastedText = e.clipboardData.getData('text');
      const values = pastedText.split(freeText ? /[\r\n]+/ : /[,\s\n]+/).filter((v) => v.trim());
      const newTags: TagItem[] = [];

      for (const val of values) {
        const trimmed = val.trim();
        if (!trimmed) continue;
        if (
          tags.some((t) => isDuplicate(t.value, trimmed)) ||
          newTags.some((t) => isDuplicate(t.value, trimmed))
        ) {
          continue;
        }

        const isValid = validate ? validate(trimmed) === null : true;
        newTags.push({
          id: `tag-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          value: trimmed,
          isValid,
        });
      }

      if (newTags.length > 0) {
        handleTagsChange([...tags, ...newTags]);
      }
    },
    [tags, handleTagsChange, validate, freeText, isDuplicate]
  );

  // Handle click-to-edit on a pill
  const handleEditTag = useCallback(
    (id: string) => {
      const tag = tags.find((t) => t.id === id);
      if (!tag) return;

      // Put tag value in input and remove the tag
      setInputValue(tag.value);
      setEditingTagId(id);
      handleTagsChange(tags.filter((t) => t.id !== id));

      // Focus input after state update
      setTimeout(() => inputRef.current?.focus(), 0);
    },
    [tags, handleTagsChange]
  );

  const handleContainerClick = () => {
    if (!disabled) inputRef.current?.focus();
  };

  const hasInvalidTags = tags.some((t) => t.isValid === false);
  const hasError = !!error || hasInvalidTags;
  const borderColor = hasError
    ? 'var(--red-a8)'
    : isFocused
      ? 'var(--accent-a8)'
      : 'var(--slate-a5)';
  const borderWidth = isFocused || hasError ? 2 : 1;
  const paddingCompensation = isFocused || hasError ? 3 : 4;

  // Build the error message
  const errorMessage = error || (hasInvalidTags
    ? 'The email you added is invalid. Please type a valid email.'
    : undefined);

  return (
    <Flex direction="column" gap="1">
      <Box
        ref={containerRef}
        onClick={handleContainerClick}
        style={{
          backgroundColor: disabled ? 'var(--olive-2)' : 'var(--color-surface)',
          border: `${borderWidth}px solid ${borderColor}`,
          borderRadius: 'var(--radius-2)',
          padding: paddingCompensation,
          minHeight: disabled ? undefined : compact ? 40 : 136,
          maxHeight: disabled ? undefined : compact ? 88 : 164,
          overflowX: 'hidden',
          overflowY: 'auto',
          cursor: disabled ? 'default' : 'text',
          boxSizing: 'border-box',
          opacity: disabled ? 0.8 : 1,
        }}
      >
        <Flex
          wrap="wrap"
          gap="1"
          align="start"
          style={{ minHeight: disabled ? undefined : '100%' }}
        >
          {/* Tags */}
          {tags.map((tag, i) => (
            <TagPill
              key={tag.id}
              tag={lastHighlighted && i === tags.length - 1 ? { ...tag, isHighlighted: true } : tag}
              onRemove={() => removeTagFromButton(i)}
              onEdit={handleEditTag}
              disabled={disabled}
            />
          ))}

          {/* Input — hidden when disabled */}
          {!disabled && (
            <input
            ref={inputRef}
            id={id}
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onFocus={() => setIsFocused(true)}
            onBlur={() => {
              setIsFocused(false);
              setLastHighlighted(false);
              // Convert remaining text to tag on blur
              if (inputValue.trim()) {
                addTag(inputValue);
              }
            }}
            placeholder={tags.length === 0 ? placeholder : ''}
            style={{
              border: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
              fontSize: 14,
              lineHeight: '20px',
              fontFamily: 'var(--default-font-family)',
              color: 'var(--slate-12)',
              flex: 1,
              minWidth: 120,
              padding: 'var(--space-1)',
            }}
          />
          )}
        </Flex>
      </Box>

      {errorMessage && (
        <Text size="1" style={{ color: 'var(--red-a11)', lineHeight: '16px' }}>
          {errorMessage}
        </Text>
      )}
    </Flex>
  );
}

// ========================================
// TagPill subcomponent
// ========================================

function TagPill({
  tag,
  onRemove,
  onEdit,
  disabled = false,
}: {
  tag: TagItem;
  onRemove: () => void;
  onEdit: (id: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const isError = tag.isValid === false;
  const isHighlighted = tag.isHighlighted === true;
  const isEditing = tag.isEditing === true;

  let bgColor = 'var(--accent-a3)';
  let border = 'none';
  let textColor = 'var(--accent-12)';
  let iconColor = 'var(--accent-12)';

  if (isError) {
    bgColor = 'var(--red-a3)';
    border = 'none';
    textColor = 'var(--slate-12)';
    iconColor = 'var(--red-9)';
  } else if (isHighlighted) {
    bgColor = 'var(--accent-surface)';
    border = '1px solid var(--accent-a7)';
  } else if (isEditing) {
    bgColor = 'transparent';
    border = '1px dashed var(--accent-a8)';
  }

  if (isEditing) {
    // In edit mode, the pill is not shown — handled by parent
    return null;
  }

  return (
    <Flex
      align="center"
      gap="1"
      onClick={(e) => {
        e.stopPropagation();
        if (!disabled) onEdit(tag.id);
      }}
      style={{
        backgroundColor: bgColor,
        border,
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-1) var(--space-2)',
        flexShrink: 0,
        whiteSpace: 'nowrap',
        // Never wider than the field, so the × stays on screen in a narrow one.
        maxWidth: '100%',
        cursor: disabled ? 'default' : 'pointer',
      }}
    >
      <Text
        size="2"
        weight="medium"
        style={{
          color: textColor,
          minWidth: 0,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
        title={tag.value}
      >
        {tag.value}
      </Text>

      {!disabled && (
        <IconButton
          type="button"
          size="1"
          variant="ghost"
          color="gray"
          data-tag-remove=""
          aria-label={t('workspace.connectors.schemaForm.removeTagAriaLabel', { tag: tag.value })}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          style={{ margin: 0, width: 'var(--space-4)', height: 'var(--space-4)', flexShrink: 0 }}
        >
          <MaterialIcon name="close" size={14} color={iconColor} />
        </IconButton>
      )}
    </Flex>
  );
}

export type { TagInputProps };
