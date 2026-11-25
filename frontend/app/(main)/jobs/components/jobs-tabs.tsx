'use client';

import { Flex, Tabs, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { MOBILE_HAMBURGER_GUTTER_PX } from '@/app/components/sidebar';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';
import type { JobsView } from '../url-params';
import { JobsList } from './jobs-list';
import { NeedsYouList } from './needs-you-list';

/**
 * The page's two questions as two Radix tabs, with the URL as their source (D6): what is waiting on me, and what is
 * running. Only the tab showing is mounted, so each reads only when it is opened.
 */
export function JobsTabs({ view, waiting, onViewChange }: { view: JobsView; waiting: number; onViewChange: (view: JobsView) => void }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  return (
    <Tabs.Root value={view} onValueChange={(v) => onViewChange(v as JobsView)} style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <Flex
        align="center"
        gap="2"
        style={{
          minHeight: 44,
          padding: isMobile ? `0 var(--space-2) 0 ${MOBILE_HAMBURGER_GUTTER_PX}px` : '0 var(--space-3)',
          borderBottom: '1px solid var(--olive-3)',
          flexShrink: 0,
        }}
      >
        <SidebarExpandButton placement="inline" />
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-2)', fontWeight: 'var(--font-weight-medium)', color: 'var(--slate-12)', whiteSpace: 'nowrap' }}>
          {t('jobsPage.title')}
        </h1>
        <Tabs.List aria-label={t('jobsPage.tabsLabel')} size="2" style={{ boxShadow: 'none', marginInlineStart: 'var(--space-3)' }}>
          <Tabs.Trigger
            value="needs-you"
            style={{ minHeight: 44 }}
          >
            {t('jobsPage.tab.needsYou')}
            {/* The pill's digits would join the name ("Needs you3"), and an aria-label would hide the visible text
                from voice control (axe label-content-name-mismatch); the count is said in words after the label. */}
            {waiting > 0 && <VisuallyHidden>{`(${t('needsYou.badgeAria', { count: waiting })})`}</VisuallyHidden>}
            {waiting > 0 && (
              <span
                aria-hidden
                style={{
                  marginInlineStart: 6,
                  minWidth: 18,
                  height: 18,
                  padding: '0 5px',
                  boxSizing: 'border-box',
                  borderRadius: 999,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: 'var(--amber-9)',
                  color: 'var(--amber-contrast)',
                  fontSize: 11,
                  fontWeight: 500,
                }}
              >
                {waiting > 99 ? '99+' : waiting}
              </span>
            )}
          </Tabs.Trigger>
          <Tabs.Trigger value="all" style={{ minHeight: 44 }}>
            {t('jobsPage.tab.jobs')}
          </Tabs.Trigger>
        </Tabs.List>
      </Flex>
      <div className="no-scrollbar" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        <div style={{ maxWidth: 880, margin: '0 auto', padding: isMobile ? 'var(--space-4) var(--space-3)' : 'var(--space-5) var(--space-5)' }}>
          <Tabs.Content value="needs-you">
            <NeedsYouList />
          </Tabs.Content>
          <Tabs.Content value="all">
            <JobsList />
          </Tabs.Content>
        </div>
      </div>
    </Tabs.Root>
  );
}
