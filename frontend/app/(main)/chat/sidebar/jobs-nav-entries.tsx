'use client';

import { Flex } from '@radix-ui/themes';
import { usePathname, useSearchParams } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { selectAgentHarnessOn, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useNeedsYouCount, useWorkingJobsCount } from '@/lib/harness-jobs/inbox-store';
import { JOBS_HREF, jobsViewOf } from '@/app/(main)/jobs/url-params';
import { SidebarItem } from './sidebar-item';

const NAV_ICON_COLOR = 'var(--neutral-11)';

/** The amber count of asks waiting on the person, named in words; nothing at 0 (the entry stays). */
export function NeedsYouBadge({ count }: { count: number }) {
  const { t } = useTranslation();
  if (count <= 0) return null;
  return (
    <span
      role="img"
      data-testid="needs-you-badge"
      aria-label={t('needsYou.badgeAria', { count })}
      style={{
        flexShrink: 0,
        minWidth: 20,
        height: 20,
        padding: '0 6px',
        boxSizing: 'border-box',
        borderRadius: 999,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'var(--amber-9)',
        color: 'var(--amber-contrast)',
        fontSize: 12,
        fontWeight: 500,
        lineHeight: 1,
      }}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

/** The quiet gray count of the person's jobs at work (§4.3 "Jobs 2"), named in words; nothing at 0. */
export function WorkingJobsCount({ count }: { count: number }) {
  const { t } = useTranslation();
  if (count <= 0) return null;
  return (
    <span
      role="img"
      data-testid="working-jobs-count"
      aria-label={t('nav.jobsWorkingAria', { count })}
      style={{ flexShrink: 0, color: 'var(--neutral-11)', fontSize: 12, fontWeight: 400, lineHeight: 1 }}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

/**
 * "Needs you" and "Jobs" under New chat and Search (classic jobs D6): every sidebar that starts chats shows them,
 * whenever the harness is on (D1).
 */
export function JobsNavEntries() {
  const { t } = useTranslation();
  const harness = useFeatureFlagsStore(selectAgentHarnessOn);
  const waiting = useNeedsYouCount();
  const working = useWorkingJobsCount();
  const pathname = usePathname() ?? '';
  const params = useSearchParams();
  const isMobile = useIsMobile();
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  if (!harness) return null;

  const onJobs = pathname.startsWith('/jobs');
  const view = onJobs ? jobsViewOf(params, waiting) : null;
  const close = isMobile ? closeMobileSidebar : undefined;

  return (
    <>
      <SidebarItem
        icon={<MaterialIcon name="pan_tool" size={ICON_SIZE_DEFAULT} color={waiting > 0 ? 'var(--amber-11)' : NAV_ICON_COLOR} />}
        label={
          <Flex align="center" gap="2" style={{ minWidth: 0, width: '100%' }}>
            <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {t('nav.needsYou')}
            </span>
            <NeedsYouBadge count={waiting} />
          </Flex>
        }
        textColor={NAV_ICON_COLOR}
        href={JOBS_HREF.needsYou}
        onClick={close}
        isActive={view === 'needs-you'}
      />
      <SidebarItem
        icon={<MaterialIcon name="work_history" size={ICON_SIZE_DEFAULT} color={NAV_ICON_COLOR} />}
        label={
          <Flex align="center" gap="2" style={{ minWidth: 0, width: '100%' }}>
            <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {t('nav.jobs')}
            </span>
            <WorkingJobsCount count={working} />
          </Flex>
        }
        textColor={NAV_ICON_COLOR}
        href={JOBS_HREF.all}
        onClick={close}
        isActive={view === 'all'}
      />
    </>
  );
}
