import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

let pathname = '/workspace/general';
vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
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
  { label: NAV.tenants, href: '/workspace/tenants' },
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
  pathname = '/workspace/general';
});

function renderSidebar() {
  return render(
    <Theme>
      <WorkspaceSidebar />
    </Theme>,
  );
}

describe('WorkspaceSidebar — Enterprise Edition pages', () => {
  it('shows an admin each Enterprise page with an EE tag, not a lock', () => {
    renderSidebar();

    for (const { label, href } of LOCKED) {
      // The tag is hidden from the link's name, so the link still reads as just its label.
      const link = linkTo(label);
      expect(link.getAttribute('href')).toBe(href);
      // The entry's own icon is hidden from the name too, so the tag is one of the hidden marks.
      const hidden = Array.from(link.querySelectorAll('[aria-hidden="true"]'), (el) => el.textContent);
      expect(hidden).toContain(en.common.enterprise.short);
      expect(hidden).not.toContain('lock');
    }
  });

  it('lists Groups, Tenants and SCIM in that order right under People', () => {
    renderSidebar();

    const people = screen.getByText(SECTIONS.people);
    expect(before(people, linkTo(NAV.groups))).toBe(true);
    expect(before(linkTo(NAV.groups), linkTo(NAV.tenants))).toBe(true);
    expect(before(linkTo(NAV.tenants), linkTo(NAV.scim))).toBe(true);
  });

  it('lists Groups, Tenants and SCIM under Overview, Audit logs last under Security, and Usage & billing before Developer settings', () => {
    renderSidebar();

    // The section and its one item share a name; the header is the one outside a link.
    const usageSection = screen.getAllByText(SECTIONS.usageAndCredits).find((el) => !el.closest('a'))!;
    const header = (title: string) => screen.getByText(title);
    const inOrder = [
      header(SECTIONS.overview), linkTo(NAV.groups), linkTo(NAV.tenants), linkTo(NAV.scim),
      header(SECTIONS.ai), linkTo(NAV.aiModels), linkTo(NAV.prompts), linkTo(NAV.webSearch),
      header(SECTIONS.integrations), linkTo(NAV.connectors), linkTo(NAV.actions), linkTo(NAV.mcpServers), linkTo(NAV.bots),
      header(SECTIONS.security), linkTo(NAV.authentication), linkTo(NAV.serviceAccounts), linkTo(NAV.mcpAccess), linkTo(NAV.auditLogs),
      header(SECTIONS.system), linkTo(NAV.mail), linkTo(NAV.services),
      usageSection, linkTo(NAV.usage),
      header(SECTIONS.developerSettings),
    ];
    for (let i = 1; i < inOrder.length; i++) {
      expect(before(inOrder[i - 1], inOrder[i]), inOrder[i].textContent ?? '').toBe(true);
    }
    // One item for usage, credits, billing and credit settings.
    expect(screen.queryByText('Credits')).toBeNull();
    expect(screen.queryByText('Billing')).toBeNull();
  });

  it('hides them from people who are not admins', () => {
    userState = { profile: { isAdmin: false } };
    renderSidebar();

    expect(screen.queryByText(SECTIONS.security)).toBeNull();
    for (const { label } of LOCKED) expect(screen.queryByText(label)).toBeNull();
    expect(screen.queryByTestId('enterprise-sidebar-card')).toBeNull();
    expect(screen.queryByText(en.workspace.enterprise.sidebarCard.title)).toBeNull();
  });

  it('shows an admin the way to the Enterprise Edition page below the list', () => {
    renderSidebar();

    const card = screen.getByTestId('enterprise-sidebar-card');
    // Below every section, so it never pushes a settings link out of place.
    expect(before(screen.getByText(SECTIONS.help), card)).toBe(true);
  });

  it('keeps showing them to an admin whatever the platform flags say', () => {
    flagsState = { flags: { ENABLE_MCP: false, ENABLE_ACTIONS: false, ENABLE_SKILLS: false } };
    renderSidebar();

    for (const { label } of LOCKED) expect(linkTo(label)).toBeTruthy();
  });
});

describe('WorkspaceSidebar: personal and workspace settings', () => {
  it('shows an admin only workspace items on a workspace page, with a way to the personal ones', () => {
    renderSidebar();
    expect(screen.queryByText(SECTIONS.personal)).toBeNull();
    expect(screen.queryByText(NAV.personalAccessTokens)).toBeNull();
    expect(linkTo(NAV.oauth2)).toBeTruthy();
    expect(linkTo(en.workspaceMenu.personalSettings).getAttribute('href')).toBe('/workspace/profile');
  });

  it('shows an admin only their own items on a personal page', () => {
    pathname = '/workspace/profile/';
    renderSidebar();
    expect(linkTo(NAV.profile) && linkTo(NAV.yourConnectors) && linkTo(NAV.personalAccessTokens)).toBeTruthy();
    for (const label of [NAV.general, NAV.aiModels, NAV.authentication, NAV.oauth2, NAV.usage, NAV.scim]) {
      expect(screen.queryByText(label), label).toBeNull();
    }
    expect(linkTo(en.workspaceMenu.workspaceSettings).getAttribute('href')).toBe('/workspace/general');
  });

  it('keeps the Enterprise Edition pages and card out of an admin\'s personal settings', () => {
    pathname = '/workspace/profile/';
    renderSidebar();
    for (const { label } of LOCKED) expect(screen.queryByText(label), label).toBeNull();
    expect(screen.queryByTestId('enterprise-sidebar-card')).toBeNull();
  });

  it.each(['/workspace/profile', '/workspace/general'])('lists every page a member can open on %s, and no workspace settings', (path) => {
    userState = { profile: { isAdmin: false } };
    pathname = path;
    renderSidebar();
    for (const label of [NAV.profile, NAV.yourConnectors, NAV.archivedChats, NAV.general, NAV.personalAccessTokens, NAV.oauth2]) {
      expect(linkTo(label), label).toBeTruthy();
    }
    expect(screen.getByText(SECTIONS.people)).toBeTruthy();
    for (const label of [en.workspaceMenu.workspaceSettings, en.workspaceMenu.personalSettings, NAV.aiModels, NAV.authentication, NAV.mail]) {
      expect(screen.queryByText(label), label).toBeNull();
    }
  });
});
