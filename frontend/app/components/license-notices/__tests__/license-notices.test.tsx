import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

const viewer = vi.hoisted(() => ({ isAdmin: null as boolean | null }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof viewer) => unknown) => selector(viewer),
  selectIsAdmin: (s: typeof viewer) => s.isAdmin,
}));

const served = vi.hoisted(() => ({ notice: undefined as unknown }));
vi.mock('../use-license-notice', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../use-license-notice')>()),
  useLicenseNotice: () => served.notice,
}));

import { LicenseBanners } from '../license-banners';
import { LimitedSourcesNote } from '../limited-sources-note';
import { ConnectorLicenseBadge } from '../connector-license-badge';
import { daysUntil, reminderStep, type LicenseNotice } from '../use-license-notice';

const DAY = 24 * 60 * 60 * 1000;
const inDays = (days: number) => new Date(Date.now() + days * DAY - 60_000).toISOString();

const notice = (over: Partial<LicenseNotice> = {}): LicenseNotice => ({
  upgradeGrace: null,
  permissionSync: true,
  notForProduction: false,
  connectors: { limited: 0, restoring: 0, items: [] },
  ...over,
});

const show = (ui: React.ReactElement) => render(<Theme>{ui}</Theme>);
const as = (role: 'admin' | 'member') => {
  viewer.isAdmin = role === 'admin';
};

beforeEach(() => {
  window.localStorage.clear();
  served.notice = undefined;
});
afterEach(() => cleanup());

describe('reminders at 14, 7, 3 and 1 days', () => {
  it('names the reminder a countdown is in', () => {
    expect([30, 15, 14, 8, 7, 4, 3, 2, 1].map(reminderStep)).toEqual([
      'start', 'start', '14', '14', '7', '7', '3', '3', '1',
    ]);
    expect(daysUntil(new Date(Date.now() + 2.5 * DAY).toISOString())).toBe(3);
  });
});

describe('LicenseBanners', () => {
  it('shows nothing with enforcement off and no key (the server says nothing applies)', () => {
    for (const role of ['admin', 'member'] as const) {
      as(role);
      served.notice = notice();
      const { container } = show(<LicenseBanners />);
      expect(container.textContent).toBe('');
      cleanup();
    }
  });

  it('during the grace: an admin gets a countdown and a link to the license page; a member nothing', () => {
    served.notice = notice({ upgradeGrace: { endsAt: inDays(10) } });
    as('admin');
    show(<LicenseBanners />);
    expect(screen.getByText(/limited to the people who created them in 10 days/)).toBeTruthy();
    expect(screen.getByText('Install a license key').closest('a')?.getAttribute('href')).toBe('/workspace/license/');
    cleanup();
    as('member');
    const { container } = show(<LicenseBanners />);
    expect(container.textContent).toBe('');
  });

  it('a dismissed countdown comes back at the next reminder', () => {
    as('admin');
    served.notice = notice({ upgradeGrace: { endsAt: inDays(10) } });
    show(<LicenseBanners />);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(/in 10 days/)).toBeNull();
    cleanup();
    show(<LicenseBanners />);
    expect(screen.queryByText(/in 10 days/)).toBeNull();
    cleanup();
    served.notice = notice({ upgradeGrace: { endsAt: inDays(7) } });
    show(<LicenseBanners />);
    expect(screen.getByText(/in 7 days/)).toBeTruthy();
  });

  it('limited: an admin is told how many and that nothing was deleted', () => {
    as('admin');
    served.notice = notice({
      permissionSync: false,
      connectors: {
        limited: 2,
        restoring: 0,
        items: [
          { id: 'a', name: 'Drive', type: 'DRIVE', state: 'limited', status: 'IDLE' },
          { id: 'b', name: 'Jira', type: 'JIRA', state: 'limited', status: 'IDLE' },
        ],
      },
    });
    show(<LicenseBanners />);
    expect(screen.getByText(/2 company-wide connectors are limited/)).toBeTruthy();
    expect(screen.getByText(/Nothing was deleted/)).toBeTruthy();
  });

  it('restoring: an admin sees each connector and its sync; a member a line with no names', () => {
    const restoring = notice({
      connectors: {
        limited: 0,
        restoring: 2,
        items: [
          { id: 'a', name: 'Drive', type: 'DRIVE', state: 'restoring', status: 'FULL_SYNCING' },
          { id: 'b', name: 'Jira', type: 'JIRA', state: 'restoring', status: 'QUEUED' },
        ],
      },
    });
    as('admin');
    served.notice = restoring;
    show(<LicenseBanners />);
    expect(screen.getByText(/2 connectors are running the full syncs/)).toBeTruthy();
    expect(screen.getByText('Drive: syncing · Jira: waiting to sync')).toBeTruthy();
    cleanup();
    as('member');
    served.notice = notice({ connectors: { limited: 0, restoring: 2, items: null } });
    const { container } = show(<LicenseBanners />);
    expect(container.textContent).toMatch(/Access to some company sources is being restored/);
    expect(container.textContent).not.toMatch(/Drive|Jira/);
  });

  it('a development key: "not for production" for everyone', () => {
    for (const role of ['admin', 'member'] as const) {
      as(role);
      served.notice = notice({ notForProduction: true });
      show(<LicenseBanners />);
      expect(screen.getByText(/It is not for production use/)).toBeTruthy();
      cleanup();
    }
  });
});

describe('LimitedSourcesNote', () => {
  it('tells a member their results may be shorter, without names, and says nothing to an admin', () => {
    served.notice = notice({ permissionSync: false, connectors: { limited: 1, restoring: 0, items: null } });
    as('member');
    show(<LimitedSourcesNote />);
    expect(screen.getByRole('status').textContent).toMatch(/aren't included in your results/);
    cleanup();
    as('admin');
    const { container } = show(<LimitedSourcesNote />);
    expect(container.textContent).toBe('');
  });

  it('says nothing to a member when no connector they lack is limited', () => {
    as('member');
    served.notice = notice();
    const { container } = show(<LimitedSourcesNote />);
    expect(container.textContent).toBe('');
  });
});

describe('ConnectorLicenseBadge', () => {
  const items = [
    { id: 'a', name: 'Drive', type: 'DRIVE', state: 'affected' as const, status: 'IDLE' },
    { id: 'b', name: 'Jira', type: 'JIRA', state: 'limited' as const, status: 'IDLE' },
    { id: 'c', name: 'Wiki', type: 'CONFLUENCE', state: 'restoring' as const, status: 'SYNCING' },
  ];

  it('marks each listed connector for an admin', () => {
    as('admin');
    served.notice = notice({ upgradeGrace: { endsAt: inDays(5) }, connectors: { limited: 1, restoring: 1, items } });
    for (const [id, label] of [['a', 'Will be limited'], ['b', 'Limited'], ['c', 'Restoring access']]) {
      show(<ConnectorLicenseBadge connectorId={id} />);
      expect(screen.getByText(label)).toBeTruthy();
      cleanup();
    }
  });

  it('shows nothing on an unlisted connector (storage and database ones never are), nor to a member', () => {
    as('admin');
    served.notice = notice({ connectors: { limited: 1, restoring: 1, items } });
    const unlisted = show(<ConnectorLicenseBadge connectorId="s3" />);
    expect(unlisted.container.textContent).toBe('');
    cleanup();
    as('member');
    const member = show(<ConnectorLicenseBadge connectorId="b" />);
    expect(member.container.textContent).toBe('');
  });
});
