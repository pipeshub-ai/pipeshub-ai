'use client';

import React from 'react';
import NextLink from 'next/link';
import { Flex, Link, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { MessagePart } from '../../types';

/** The tool a durable run keeps a memory with (`harness_core.memories.REMEMBER_TOOL`), as the model calls it. */
const REMEMBER_TOOLS: ReadonlySet<string> = new Set(['remember', 'memory__remember']);

/** Whether the turn kept something in the user's memories: a `remember` call that completed. */
export function savedMemory(parts: readonly MessagePart[] | undefined): boolean {
  return (parts ?? []).some(
    (part) =>
      part.type === 'tool_call' &&
      part.status === 'completed' &&
      part.toolName !== undefined &&
      REMEMBER_TOOLS.has(part.toolName),
  );
}

/**
 * "Saved to your memories · Review" (UX3, R1 F17): the agent keeping
 * something about the user is said where it happens, with the way to see it.
 */
export function MemoryChip({ parts }: { parts: readonly MessagePart[] | undefined }) {
  const { t } = useTranslation();
  if (!savedMemory(parts)) return null;
  return (
    <Flex align="center" gap="1" data-testid="memory-chip" style={{ marginTop: 'var(--space-2)' }}>
      <MaterialIcon name="memory" size={14} color="var(--gray-11)" />
      <Text size="1" color="gray">
        {t('memoryChip.saved')} ·{' '}
      </Text>
      <Link asChild size="1">
        <NextLink href="/workspace/memories">{t('memoryChip.review')}</NextLink>
      </Link>
    </Flex>
  );
}
