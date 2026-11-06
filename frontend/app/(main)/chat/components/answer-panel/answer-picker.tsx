'use client';

import React from 'react';
import { Box, Button, DropdownMenu, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface PickerAnswer {
  key: string;
  /** The question as plain text. */
  question: string;
}

/**
 * Which answer the column beside a chat describes, and the way to the others. With one answer, its question; with
 * several, "n / N", the question and a menu of them all: picking one scrolls the chat there and the column follows.
 * One line, truncated: the full question is the answer's heading, and each row's tooltip. The question takes the
 * free width, so the chevron stays at the trigger's right end however long the question is.
 */
export function AnswerPicker({
  answers,
  currentKey,
  onPick,
}: {
  answers: readonly PickerAnswer[];
  currentKey: string;
  onPick(key: string): void;
}) {
  const index = answers.findIndex((a) => a.key === currentKey);
  const question = answers[index]?.question ?? '';
  const many = answers.length > 1;
  const label = (
    <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
      {many && (
        <Text size="1" style={{ color: 'var(--text-secondary)', flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
          {index + 1} / {answers.length}
        </Text>
      )}
      <Text size="1" truncate align="left" style={{ color: 'var(--text-primary)', minWidth: 0, flex: 1 }}>
        {question}
      </Text>
      {many && (
        <span aria-hidden style={{ display: 'inline-flex', flexShrink: 0 }}>
          <MaterialIcon name="unfold_more" size={14} color="var(--text-secondary)" />
        </span>
      )}
    </Flex>
  );
  if (!many) {
    return (
      <Box title={question} data-testid="answer-picker" style={{ minWidth: 0, flex: 1, paddingInlineEnd: 8 }}>
        {label}
      </Box>
    );
  }
  return (
    // Not modal: a modal menu restyles the whole page to lock it, which is slow on a long chat.
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger>
        <Button
          variant="ghost"
          color="gray"
          size="1"
          title={question}
          data-testid="answer-picker"
          style={{
            flex: 1,
            minWidth: 0,
            // 28px, with no block padding on top of it (the button is content-box).
            height: 'calc(var(--space-6) - var(--space-1))',
            paddingBlock: 0,
            justifyContent: 'flex-start',
            overflow: 'hidden',
            margin: 0,
            marginInlineStart: 'calc(var(--space-1) * -1.5)',
            marginInlineEnd: 'var(--space-2)',
            paddingInline: 'calc(var(--space-1) * 1.5)',
          }}
        >
          {label}
        </Button>
      </DropdownMenu.Trigger>
      {/* Menus never scroll sideways: rows truncate inside a set width, and a long chat scrolls the list. */}
      <DropdownMenu.Content align="start" style={{ width: 320, maxHeight: 360 }}>
        {answers.map((a, i) => (
          <DropdownMenu.Item key={a.key} onSelect={() => onPick(a.key)} title={a.question} aria-current={i === index || undefined}>
            {/* A set width, not 100%: the menu sizes itself from its widest row, so a percentage would never truncate. */}
            <Flex align="center" gap="2" style={{ minWidth: 0, width: 280 }}>
              <Text size="1" style={{ color: 'var(--text-secondary)', width: 16, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
                {i + 1}
              </Text>
              <Text size="2" truncate style={{ minWidth: 0, flex: 1 }}>
                {a.question}
              </Text>
              {/* Kept on every row, so the questions end at one edge, ticked or not. */}
              <Box aria-hidden style={{ width: 14, flexShrink: 0, display: 'flex' }}>
                {i === index && <MaterialIcon name="check" size={14} color="var(--accent-11)" />}
              </Box>
            </Flex>
          </DropdownMenu.Item>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
