'use client';

import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
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

  const addTag = useCallback(
    (value: string) => {
      const trimmed = value.trim();
      if (!trimmed) return;

      // Check for duplicates
      if (tags.some((t) => t.value.toLowerCase() === trimmed.toLowerCase())) {
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
    [tags, onTagsChange, validate]
  );

  const removeTag = useCallback(
    (id: string) => {
      handleTagsChange(tags.filter((t) => t.id !== id));
    },
    [tags, handleTagsChange]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter' || e.key === ',' || e.key === 'Tab' || e.key === ' ') {
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
    [inputValue, tags, addTag, removeTag, onTagsChange]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLInputElement>) => {
      e.preventDefault();
      const pastedText = e.clipboardData.getData('text');
      // Split by commas, spaces, newlines
      const values = pastedText.split(/[,\s\n]+/).filter((v) => v.trim());
      const newTags: TagItem[] = [];

      for (const val of values) {
        const trimmed = val.trim();
        if (!trimmed) continue;
        if (
          tags.some((t) => t.value.toLowerCase() === trimmed.toLowerCase()) ||
          newTags.some((t) => t.value.toLowerCase() === trimmed.toLowerCase())
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
    [tags, handleTagsChange, validate]
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
          minHeight: disabled ? undefined : 136,
          maxHeight: disabled ? undefined : 164,
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
              padding: '4px',
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
        padding: '4px 8px',
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
        title={tag.value}
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
