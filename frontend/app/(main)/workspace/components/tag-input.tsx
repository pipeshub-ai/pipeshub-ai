'use client';

import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useFormFieldControl } from './form-field';

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
  /**
   * A non-blocking note for a tag, keyed by the tag's lowercase value. A valid tag
   * with a note turns amber (the value is allowed, but the reader should know).
   */
  warnings?: Record<string, string>;
  /** The line shown instead of each note when more than one tag has one. */
  warningsSummary?: string;
  /** When true, tags are read-only — no adding, removing, or editing */
  disabled?: boolean;
  /** Single-line-ish field for forms that stack several tag inputs (vs. the tall email-invite box). */
  compact?: boolean;
  /**
   * Values are free text that may contain spaces and commas (e.g. folder paths):
   * only Enter or Tab ends a tag, a paste splits on newlines, and duplicates are
   * compared case-sensitively.
   */
  freeText?: boolean;
}

/** What ends one address and starts the next. */
const SEPARATORS = /[\s,;]+/;

// ========================================
// Component
// ========================================

export function TagInput({
  tags,
  onTagsChange,
  placeholder = 'Enter a value',
  validate,
  error,
  warnings,
  warningsSummary,
  disabled = false,
  compact = false,
  freeText = false,
}: TagInputProps) {
  // No-op fallback when disabled or no handler provided
  const handleTagsChange = onTagsChange ?? (() => {});
  const [inputValue, setInputValue] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const [_editingTagId, setEditingTagId] = useState<string | null>(null);
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

  /** One tag per value, skipping blanks and anything already listed. */
  const addTags = useCallback(
    (values: string[]) => {
      const added: TagItem[] = [];
      for (const value of values) {
        const trimmed = value.trim();
        if (!trimmed) continue;
        if (
          tags.some((t) => isDuplicate(t.value, trimmed)) ||
          added.some((t) => isDuplicate(t.value, trimmed))
        ) {
          continue;
        }
        added.push({
          id: `tag-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          value: trimmed,
          isValid: validate ? validate(trimmed) === null : true,
        });
      }
      if (added.length > 0) handleTagsChange([...tags, ...added]);
    },
    [tags, onTagsChange, validate, isDuplicate]
  );

  /**
   * Turns typed or filled text into tags. Addresses are separated by white space,
   * commas or semicolons, so text that arrived without key presses (an IME, a
   * fill, an autofill) cannot leave a separator inside a tag. Free text splits
   * on nothing: it is one value.
   */
  const addTag = useCallback(
    (text: string) => {
      addTags(freeText ? [text] : text.split(SEPARATORS));
      setInputValue('');
      setEditingTagId(null);
    },
    [addTags, freeText]
  );

  const removeTag = useCallback(
    (id: string) => {
      handleTagsChange(tags.filter((t) => t.id !== id));
    },
    [tags, handleTagsChange]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      // Keys that confirm an IME candidate (CJK input) belong to the IME; Safari
      // reports the confirming Enter with isComposing false but keyCode 229.
      if (e.nativeEvent.isComposing || e.keyCode === 229) return;
      const endsTag = freeText
        ? e.key === 'Enter' || e.key === 'Tab'
        : e.key === 'Enter' || e.key === ',' || e.key === 'Tab' || e.key === ' ';
      if (endsTag) {
        e.preventDefault();
        addTag(inputValue);
      } else if (
        (e.key === 'Backspace' || e.key === 'ArrowLeft') &&
        inputValue === '' &&
        tags.length > 0
      ) {
        const lastTag = tags[tags.length - 1];
        if (e.key === 'Backspace' && lastTag.isHighlighted) {
          // Second backspace removes it
          removeTag(lastTag.id);
        } else if (!lastTag.isHighlighted) {
          // First backspace or left arrow highlights
          handleTagsChange(
            tags.map((t, i) =>
              i === tags.length - 1 ? { ...t, isHighlighted: true } : t
            )
          );
        }
      } else {
        // Clear highlight on any other key
        if (tags.some((t) => t.isHighlighted)) {
          handleTagsChange(tags.map((t) => ({ ...t, isHighlighted: false })));
        }
      }
    },
    [inputValue, tags, addTag, removeTag, onTagsChange, freeText]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLInputElement>) => {
      e.preventDefault();
      const pastedText = e.clipboardData.getData('text');
      addTags(pastedText.split(freeText ? /[\r\n]+/ : SEPARATORS));
    },
    [addTags, freeText]
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

  // Inside a FormField the label names the input; the error and note lines below
  // describe it, and it is marked invalid while an address is.
  const control = useFormFieldControl();
  const errorId = `${control.id ?? 'tag-input'}-tag-error`;
  const warningId = `${control.id ?? 'tag-input'}-tag-warning`;
  const hasInvalidTags = tags.some((t) => t.isValid === false);
  const hasError = !!error || hasInvalidTags;
  const borderColor = hasError
    ? 'var(--red-a8)'
    : isFocused
      ? 'var(--accent-a8)'
      : 'var(--slate-a5)';
  const borderWidth = isFocused || hasError ? 2 : 1;
  const paddingCompensation = isFocused || hasError ? 3 : 4;

  // A note only counts on a tag that is still listed and valid.
  const notes = tags.flatMap((tag) => {
    const note = tag.isValid === false ? undefined : warnings?.[tag.value.toLowerCase()];
    return note ? [note] : [];
  });
  const warningLine =
    notes.length === 0 ? undefined : notes.length === 1 ? notes[0] : (warningsSummary ?? notes[0]);

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
          {tags.map((tag) => (
            <TagPill
              key={tag.id}
              tag={tag}
              warning={warnings?.[tag.value.toLowerCase()]}
              onRemove={() => removeTag(tag.id)}
              onEdit={handleEditTag}
              disabled={disabled}
            />
          ))}

          {/* Input — hidden when disabled */}
          {!disabled && (
            <input
            ref={inputRef}
            type="text"
            id={control.id}
            aria-required={control['aria-required']}
            aria-invalid={errorMessage ? true : undefined}
            aria-describedby={
              [errorMessage ? errorId : null, warningLine ? warningId : null].filter(Boolean).join(' ') ||
              undefined
            }
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onFocus={() => setIsFocused(true)}
            onBlur={() => {
              setIsFocused(false);
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
        <Text id={errorId} size="1" style={{ color: 'var(--red-a11)', lineHeight: '16px' }}>
          {errorMessage}
        </Text>
      )}
      {warningLine && (
        <Text id={warningId} size="1" role="status" style={{ color: 'var(--amber-11)', lineHeight: '16px' }}>
          {warningLine}
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
  warning,
  onRemove,
  onEdit,
  disabled = false,
}: {
  tag: TagItem;
  /** A note about this value: the tag is allowed, and shown amber. */
  warning?: string;
  onRemove: () => void;
  onEdit: (id: string) => void;
  disabled?: boolean;
}) {
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
  } else if (warning) {
    bgColor = 'var(--amber-a3)';
    border = '1px solid var(--amber-a7)';
    textColor = 'var(--amber-11)';
    iconColor = 'var(--amber-11)';
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
        maxWidth: 280,
        cursor: disabled ? 'default' : 'pointer',
      }}
    >
      <Text
        size="2"
        weight="medium"
        style={{
          color: textColor,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
        title={warning ? `${tag.value}: ${warning}` : tag.value}
      >
        {tag.value}
      </Text>

      {!disabled && (isError ? (
        <Box
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="close" size={14} color={iconColor} />
        </Box>
      ) : (
        <Box
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="close" size={14} color={iconColor} />
        </Box>
      ))}
    </Flex>
  );
}

export type { TagInputProps };
