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
  /**
   * Unique per answer: names each tab and the panel it controls (`responseTabIds`). Without it the panels are the
   * caller's own, unnamed, and no tab names one.
   */
  idBase?: string;
  /** Below 1024px in a chat: the row spans the column with the triggers spread across it. */
  spread?: boolean;
}

/** The ids a tab and its panel share, so the answer's content can say which tab labels it. */
export const responseTabIds = (idBase: string, tab: ResponseTab) => ({
  tab: `${idBase}-tab-${tab}`,
  panel: `${idBase}-panel`,
});

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
 * An answer's own Answer, Sources and Citations tabs, where the chat has no column beside it for them: a WAI-ARIA
 * tab list (Radix Tabs), so they take one tab stop and the arrow keys, Home and End move among the tabs that have
 * something to show. The panel is the answer's own, drawn by the caller; with `idBase` the active tab names it and it
 * names the tab back (`responseTabIds`). `spread`, as the design's narrow chat has them; otherwise the row hugs its
 * triggers.
 */
export function ResponseTabs({
  activeTab,
  onTabChange,
  sourcesCount,
  citationCount,
  idBase,
  spread = false,
}: ResponseTabsProps) {
  const { t } = useTranslation();
  const trigger = (tab: ResponseTab) => {
    if (!idBase) return { 'aria-controls': undefined };
    const ids = responseTabIds(idBase, tab);
    return { id: ids.tab, 'aria-controls': tab === activeTab ? ids.panel : undefined };
  };
  return (
    // 8px under the row, so with the answer's own 16px it sits 24px below the tabs, as the design's does.
    <Tabs.Root value={activeTab} onValueChange={(value) => onTabChange(value as ResponseTab)} style={{ marginBottom: 'var(--space-2)' }}>
      <Tabs.List
        aria-label={t('chat.responseTabs')}
        className="response-tabs-scroll"
        style={{ width: spread ? '100%' : 'fit-content', justifyContent: spread ? 'space-between' : undefined }}
      >
        <Tabs.Trigger value="answer" {...trigger('answer')}>
          {t('chat.answer')}
        </Tabs.Trigger>
        <Tabs.Trigger value="sources" disabled={!sourcesCount} {...trigger('sources')}>
          <TabLabel label={t('chat.sources')} count={sourcesCount} />
        </Tabs.Trigger>
        <Tabs.Trigger value="citation" disabled={!citationCount} {...trigger('citation')}>
          <TabLabel label={t('chat.citations')} count={citationCount} />
        </Tabs.Trigger>
      </Tabs.List>
    </Tabs.Root>
  );
}
