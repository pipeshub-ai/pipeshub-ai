'use client';

import React from 'react';
import { Badge, Flex, Tabs } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { ResponseTab } from '@/chat/types';

interface ResponseTabsProps {
  activeTab: ResponseTab;
  onTabChange: (tab: ResponseTab) => void;
  sourcesCount?: number;
  citationCount?: number;
  /** Below 1024px in a chat: the row spans the column with the triggers spread across it. */
  spread?: boolean;
}

/** A tab's label and, once there is one, its count. */
function TabLabel({ label, count }: { label: string; count?: number }) {
  return (
    <Flex as="span" align="center" gap="2" style={{ display: 'inline-flex' }}>
      <span>{label}</span>
      {count !== undefined && count > 0 && (
        <Badge size="1" variant="soft" radius="small">
          {count}
        </Badge>
      )}
    </Flex>
  );
}

/**
 * An answer's own Answer, Sources and Citations tabs, where the chat has no column beside it for them. Radix Tabs, so
 * they take one tab stop and the arrow keys move between them; the panels are the answer's own, drawn by the caller.
 * `spread`, as the design's narrow chat has them; otherwise the row hugs its triggers.
 */
export function ResponseTabs({
  activeTab,
  onTabChange,
  sourcesCount,
  citationCount,
  spread = false,
}: ResponseTabsProps) {
  const { t } = useTranslation();
  return (
    // 8px under the row, so with the answer's own 16px it sits 24px below the tabs, as the design's does.
    <Tabs.Root value={activeTab} onValueChange={(value) => onTabChange(value as ResponseTab)} style={{ marginBottom: 'var(--space-2)' }}>
      <Tabs.List
        className="response-tabs-scroll"
        style={{ width: spread ? '100%' : 'fit-content', justifyContent: spread ? 'space-between' : undefined }}
      >
        {/* The caller draws the panels outside these tabs, so no tab names a panel id that does not exist. */}
        <Tabs.Trigger value="answer" aria-controls={undefined}>
          {t('chat.answer')}
        </Tabs.Trigger>
        <Tabs.Trigger value="sources" disabled={!sourcesCount} aria-controls={undefined}>
          <TabLabel label={t('chat.sources')} count={sourcesCount} />
        </Tabs.Trigger>
        <Tabs.Trigger value="citation" disabled={!citationCount} aria-controls={undefined}>
          <TabLabel label={t('chat.citations')} count={citationCount} />
        </Tabs.Trigger>
      </Tabs.List>
    </Tabs.Root>
  );
}
