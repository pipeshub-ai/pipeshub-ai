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
  selectScheduledRunsEnabled: () => false,
  selectAgentHarnessOn: () => false,
  selectAgentHarnessResumeEnabled: () => false,
  selectEngineRunsEnabled: () => false,
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
const SECTIONS = en.workspace.sidebar.sections;
const LOCKED = [
  { label: NAV.groups, href: '/workspace/groups' },
  { label: NAV.scim, href: '/workspace/enterprise/scim' },
  { label: NAV.usage, href: '/workspace/enterprise/usage' },
  { label: NAV.auditLogs, href: '/workspace/enterprise/audit-logs' },
];

// A link's name may start with its icon's ligature text ("history Audit logs").
const linkTo = (label: string) => screen.getByRole('link', { name: new RegExp(`^(\\S+ )?${label}$`) });
/** True when `a` comes before `b` in the sidebar. */
const before = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

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
  it('shows an admin each Enterprise page with a lock', () => {
    renderSidebar();

    for (const { label, href } of LOCKED) {
      // The lock is hidden from the link's name, so the link still reads as just its label.
      const link = linkTo(label);
      expect(link.getAttribute('href')).toBe(href);
      // The entry's own icon is hidden from the name too, so the lock is one of the hidden marks.
      const hidden = Array.from(link.querySelectorAll('[aria-hidden="true"]'), (el) => el.textContent);
      expect(hidden).toContain('lock');
    }
  });

  it('lists SCIM under Groups, then the Usage & credits section after Workspace, then Audit logs under Security', () => {
    renderSidebar();

    const workspace = screen.getByText(SECTIONS.workspace);
    // The section and its one item share a name; the header is the one outside a link.
    const usageSection = screen.getAllByText(SECTIONS.usageAndCredits).find((el) => !el.closest('a'));
    const security = screen.getByText(SECTIONS.security);
    const developer = screen.getByText(SECTIONS.developerSettings);
    expect(usageSection).toBeTruthy();
    expect(before(linkTo(NAV.groups), linkTo(NAV.scim)) && before(linkTo(NAV.scim), workspace)).toBe(true);
    expect(before(linkTo(NAV.webSearch), usageSection!) && before(usageSection!, linkTo(NAV.usage))).toBe(true);
    expect(before(linkTo(NAV.usage), security) && before(security, linkTo(NAV.auditLogs))).toBe(true);
    expect(before(linkTo(NAV.auditLogs), developer)).toBe(true);
    // One item for usage, credits, billing and credit settings.
    expect(screen.queryByText('Credits')).toBeNull();
    expect(screen.queryByText('Billing')).toBeNull();
  });

  it('hides them from people who are not admins', () => {
    userState = { profile: { isAdmin: false } };
    renderSidebar();

    expect(screen.queryByText(SECTIONS.security)).toBeNull();
    for (const { label } of LOCKED) expect(screen.queryByText(label)).toBeNull();
  });

  it('keeps showing them to an admin whatever the platform flags say', () => {
    flagsState = { flags: { ENABLE_MCP: false, ENABLE_ACTIONS: false, ENABLE_SKILLS: false } };
    renderSidebar();

    for (const { label } of LOCKED) expect(linkTo(label)).toBeTruthy();
  });
});
