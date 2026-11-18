import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('next/navigation', () => ({
  usePathname: () => '/workspace/general',
}));

// jsdom has no matchMedia; these tests cover the desktop sidebar.
vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => false,
}));

// Both stores persist to localStorage, which jsdom here doesn't implement; stand in for the
// selector surface the sidebar and useEnterprisePrompt read.
let userState = { profile: { isAdmin: true } as { isAdmin: boolean } | null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
  // The profile here has finished loading, so the refreshed role is the stored one.
  selectRefreshedIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

let flagsState: { flags: Record<string, boolean> | null } = { flags: {} };
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: typeof flagsState) => unknown) => selector(flagsState),
  selectFeatureFlagsLoaded: (s: typeof flagsState) => s.flags !== null,
  selectMcpEnabled: (s: typeof flagsState) => s.flags?.ENABLE_MCP !== false,
  selectActionsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_ACTIONS !== false,
  selectSkillsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_SKILLS !== false,
}));

// Spread the real module so exports the import graph needs (initReactI18next) stay defined.
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({
    t: (key: string) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

import WorkspaceSidebar from '../index';

const NAV = en.workspace.sidebar.nav;
const LOCKED = [
  { label: NAV.groups, href: '/workspace/groups' },
  { label: NAV.scim, href: '/workspace/enterprise/scim' },
  { label: NAV.auditLogs, href: '/workspace/enterprise/audit-logs' },
  { label: NAV.usage, href: '/workspace/enterprise/usage' },
  { label: NAV.credits, href: '/workspace/enterprise/credits' },
  { label: NAV.billing, href: '/workspace/enterprise/billing' },
  { label: NAV.creditSettings, href: '/workspace/enterprise/credit-settings' },
];

afterEach(() => {
  cleanup();
  userState = { profile: { isAdmin: true } };
  flagsState = { flags: {} };
});

function renderSidebar() {
  return render(
    <Theme>
      <WorkspaceSidebar />
    </Theme>,
  );
}

describe('WorkspaceSidebar — Enterprise Edition pages', () => {
  it('shows an admin Groups, the Security pages and the Usage & credits pages, each with a lock', () => {
    renderSidebar();

    expect(screen.getByText(en.workspace.sidebar.sections.security)).toBeTruthy();
    expect(screen.getByText(en.workspace.sidebar.sections.usageAndCredits)).toBeTruthy();
    for (const { label, href } of LOCKED) {
      // The lock is hidden from the link's name, so the link still reads as just its label.
      const link = screen.getByRole('link', { name: new RegExp(`^(\\S+ )?${label}$`) });
      expect(link.getAttribute('href')).toBe(href);
      expect(link.querySelector('[aria-hidden="true"]')?.textContent).toBe('lock');
    }
  });

  it('hides them from people who are not admins', () => {
    userState = { profile: { isAdmin: false } };
    renderSidebar();

    expect(screen.queryByText(en.workspace.sidebar.sections.security)).toBeNull();
    expect(screen.queryByText(en.workspace.sidebar.sections.usageAndCredits)).toBeNull();
    for (const { label } of LOCKED) expect(screen.queryByText(label)).toBeNull();
  });

  it('keeps showing them to an admin whatever the platform flags say', () => {
    flagsState = { flags: { ENABLE_MCP: false, ENABLE_ACTIONS: false, ENABLE_SKILLS: false } };
    renderSidebar();

    for (const { label } of LOCKED) {
      expect(screen.getByRole('link', { name: new RegExp(`^(\\S+ )?${label}$`) })).toBeTruthy();
    }
  });
});
