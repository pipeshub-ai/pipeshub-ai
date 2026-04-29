'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useInlineRename } from '../utils/use-inline-rename';

interface InlineEditableNameProps {
  connectorId: string;
  name: string;
  /** Font size for the text and input (CSS value). Defaults to '14px'. */
  fontSize?: string;
  /** Radix Text `size` prop for the display text. Defaults to '2'. */
  textSize?: '1' | '2' | '3';
  /** Radix Text `weight` for the display text. Defaults to 'regular'. */
  textWeight?: 'regular' | 'medium' | 'bold';
  /** Edit icon size in px. Defaults to 14. */
  iconSize?: number;
  /** Max width constraint for the input. Defaults to '100%'. */
  maxWidth?: string;
  /** Whether to truncate the display text. Defaults to false. */
  truncate?: boolean;
}

export function InlineEditableName({
  connectorId,
  name,
  fontSize = '14px',
  textSize = '2',
  textWeight = 'regular',
  iconSize = 14,
  maxWidth = '100%',
  truncate = false,
}: InlineEditableNameProps) {
  const {
    isEditing,
    editValue,
    isBusy,
    inputRef,
    measureRef,
    inputWidth,
    startEditing,
    saveRename,
    handleKeyDown,
    handleChange,
  } = useInlineRename({ connectorId, currentName: name });

  return (
    <Flex align="center" style={{ position: 'relative', maxWidth, minWidth: 0 }}>
      {/* Hidden span for measuring text width */}
      <span
        ref={measureRef}
        style={{
          position: 'absolute',
          visibility: 'hidden',
          whiteSpace: 'pre',
          font: 'inherit',
          fontSize,
          padding: 'var(--space-1) var(--space-2)',
        }}
      />

      {isEditing ? (
        <input
          ref={inputRef}
          value={editValue}
          onChange={handleChange}
          onBlur={() => void saveRename()}
          onKeyDown={handleKeyDown}
          onClick={(e) => e.stopPropagation()}
          disabled={isBusy}
          style={{
            font: 'inherit',
            fontSize,
            color: 'var(--gray-12)',
            backgroundColor: 'var(--color-surface)',
            border: '1px solid var(--accent-8)',
            borderRadius: 'var(--radius-1)',
            padding: 'var(--space-1) var(--space-2)',
            outline: 'none',
            width: inputWidth ? `${inputWidth}px` : 'auto',
            minWidth: '60px',
            maxWidth: '100%',
            boxSizing: 'border-box',
          }}
        />
      ) : (
        <Flex
          align="center"
          gap="2"
          onClick={(e) => {
            e.stopPropagation();
            startEditing();
          }}
          style={{
            cursor: 'pointer',
            minHeight: 28,
            minWidth: 0,
            maxWidth: '100%',
          }}
        >
          <Text
            size={textSize}
            weight={textWeight}
            style={{
              color: 'var(--gray-12)',
              ...(truncate
                ? {
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }
                : {}),
            }}
          >
            {name}
          </Text>
          <MaterialIcon
            name="edit"
            size={iconSize}
            color="var(--gray-9)"
            style={{ flexShrink: 0 }}
          />
        </Flex>
      )}
    </Flex>
  );
}
