/**
 * Guards the workspace sidebar against losing or misplacing an entry, for example in a merge.
 *
 * - Each scope lists exactly the links below, for an admin and for a member. Removing,
 *   moving or adding an entry fails here until this list is updated on purpose.
 * - No page is listed in both Personal and Workspace settings.
 * - Every settings page under /workspace is either linked from the sidebar or named in
 *   NOT_IN_SIDEBAR with the way people reach it, so a new page cannot ship without a way in.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

let pathname = '/workspace/general';
vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
}));

vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => false,
}));

let isAdmin = true;
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { profile: { isAdmin: boolean } }) => unknown) => selector({ profile: { isAdmin } }),
  selectIsAdmin: (s: { profile: { isAdmin: boolean } }) => s.profile.isAdmin,
  selectRefreshedIsAdmin: (s: { profile: { isAdmin: boolean } }) => s.profile.isAdmin,
}));

/** Every flag that gates a sidebar entry, on or off together. */
let flagsOn = true;
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: () => unknown) => selector(),
  selectFeatureFlagsLoaded: () => true,
  selectMcpEnabled: () => flagsOn,
  selectActionsEnabled: () => flagsOn,
  selectSkillsEnabled: () => flagsOn,
  selectScheduledRunsEnabled: () => flagsOn,
  selectAgentHarnessOn: () => flagsOn,
  selectAgentHarnessResumeEnabled: () => flagsOn,
  selectEngineRunsEnabled: () => flagsOn,
}));

import WorkspaceSidebar from '../index';

afterEach(() => {
  cleanup();
  pathname = '/workspace/general';
  isAdmin = true;
  flagsOn = true;
});

const WORKSPACE_PAGE = '/workspace/general';
const PERSONAL_PAGE = '/workspace/profile';
/** Links every scope has besides its entries: back to the app, and the switch to the other settings. The icon's name comes first in a link's text. */
const FRAME_LABELS = new Set([
  en.workspace.sidebar.backToApp,
  en.workspaceMenu.workspaceSettings,
  en.workspaceMenu.personalSettings,
]);

/** The scope's entries, in sidebar order, with People opened. */
function entriesOn(page: string): string[] {
  pathname = page;
  render(<Theme><WorkspaceSidebar /></Theme>);
  const people = screen.queryByRole('button', { name: new RegExp(en.workspace.sidebar.sections.people) });
  if (people && !document.querySelector('a[href^="/workspace/users"]')) fireEvent.click(people);
  const hrefs = screen
    .getAllByRole('link')
    .filter((a) => ![...FRAME_LABELS].some((label) => (a.textContent ?? '').trim().endsWith(label)))
    .map((a) => (a.getAttribute('href') ?? '').replace(/\/$/, ''));
  cleanup();
  return hrefs;
}

const FLAGGED = {
  ai: ['/workspace/ai-access'],
  personal: ['/workspace/ai-accounts', '/workspace/schedules', '/workspace/memories'],
  integrations: ['/workspace/actions/team', '/workspace/mcp-servers/team'],
  yours: ['/workspace/actions/personal', '/workspace/mcp-servers/personal', '/workspace/skills/personal'],
};

const ADMIN_WORKSPACE = [
  '/workspace/general', '/workspace/labs', '/workspace/users', '/workspace/teams',
  '/workspace/groups', '/workspace/tenants', '/workspace/enterprise/scim',
  '/workspace/ai-models', ...FLAGGED.ai, '/workspace/prompts', '/workspace/web-search',
  '/workspace/connectors/team', '/workspace/actions/team', '/workspace/mcp-servers/team', '/workspace/bots',
  '/workspace/authentication', '/workspace/service-accounts', '/workspace/mcp-access', '/workspace/enterprise/audit-logs',
  '/workspace/mail', '/workspace/services',
  '/workspace/enterprise/usage',
  '/workspace/developer-settings/oauth2',
  '/workspace/enterprise',
];

const ADMIN_PERSONAL = [
  '/workspace/profile', ...FLAGGED.personal,
  '/workspace/connectors/personal', ...FLAGGED.yours,
  '/workspace/archived-chats',
  '/workspace/developer-settings/personal-access-tokens',
];

const MEMBER = [
  '/workspace/profile', ...FLAGGED.personal,
  '/workspace/connectors/personal', ...FLAGGED.yours,
  '/workspace/archived-chats',
  '/workspace/general', '/workspace/users', '/workspace/teams',
  '/workspace/developer-settings/oauth2', '/workspace/developer-settings/personal-access-tokens',
];

const without = (list: string[], removed: string[]) => list.filter((route) => !removed.includes(route));
const ALL_FLAGGED = Object.values(FLAGGED).flat();

/**
 * Settings pages with no sidebar entry of their own, and how people reach them. A new page goes
 * in the sidebar, or here with its way in.
 */
const NOT_IN_SIDEBAR: Record<string, string> = {
  '/workspace': 'redirects to General',
  '/workspace/actions': 'redirects to team or personal actions',
  '/workspace/connectors': 'redirects to team or personal connectors',
  '/workspace/approvals': 'opened from the knowledge base publications panel',
  '/workspace/groups/permissions': 'opened from Groups',
  '/workspace/tenants/users': 'opened from Tenants',
  '/workspace/tenants/groups': 'opened from Tenants',
  '/workspace/oauth-apps': 'an edition page slot (config-pages)',
  '/workspace/orgs': 'an edition page slot (config-pages)',
  '/workspace/orgs/users': 'an edition page slot (config-pages)',
  '/workspace/orgs/groups': 'an edition page slot (config-pages)',
  '/workspace/orgs/feature-flags': 'an edition page slot (config-pages)',
};

function workspacePageRoutes(): string[] {
  const root = path.resolve(__dirname, '../..');
  const routes: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name === 'page.tsx') {
        const rel = path.relative(root, path.dirname(full)).split(path.sep).join('/');
        routes.push(rel ? `/workspace/${rel}` : '/workspace');
      }
    }
  };
  walk(root);
  return routes.sort();
}

describe('WorkspaceSidebar: no entry lost or misplaced', () => {
  it.each([
    ['on', true],
    ['off', false],
  ])('lists every entry of Workspace settings for an admin, flags %s', (_label, on) => {
    flagsOn = on;
    const expected = on ? ADMIN_WORKSPACE : without(ADMIN_WORKSPACE, ALL_FLAGGED);
    expect(entriesOn(WORKSPACE_PAGE)).toEqual(expected);
  });

  it.each([
    ['on', true],
    ['off', false],
  ])('lists every entry of Personal settings for an admin, flags %s', (_label, on) => {
    flagsOn = on;
    const expected = on ? ADMIN_PERSONAL : without(ADMIN_PERSONAL, ALL_FLAGGED);
    expect(entriesOn(PERSONAL_PAGE)).toEqual(expected);
  });

  it.each([WORKSPACE_PAGE, PERSONAL_PAGE])('lists the same entries for a member on %s', (page) => {
    isAdmin = false;
    expect(entriesOn(page)).toEqual(MEMBER);
  });

  it('never lists a page in both Personal and Workspace settings', () => {
    const workspace = new Set(entriesOn(WORKSPACE_PAGE));
    expect(entriesOn(PERSONAL_PAGE).filter((route) => workspace.has(route))).toEqual([]);
  });

  it('lists each entry once', () => {
    for (const page of [WORKSPACE_PAGE, PERSONAL_PAGE]) {
      const entries = entriesOn(page);
      expect(entries.filter((route, i) => entries.indexOf(route) !== i), page).toEqual([]);
    }
  });

  it('gives every settings page a way in', () => {
    const linked = new Set([...entriesOn(WORKSPACE_PAGE), ...entriesOn(PERSONAL_PAGE)]);
    const unreachable = workspacePageRoutes().filter((route) => !linked.has(route) && !(route in NOT_IN_SIDEBAR));
    expect(unreachable).toEqual([]);
  });

  it('names only real pages that the sidebar does not link', () => {
    const pages = new Set(workspacePageRoutes());
    const linked = new Set([...entriesOn(WORKSPACE_PAGE), ...entriesOn(PERSONAL_PAGE)]);
    for (const route of Object.keys(NOT_IN_SIDEBAR)) {
      expect(pages.has(route), `${route} is not a page`).toBe(true);
      expect(linked.has(route), `${route} is in the sidebar`).toBe(false);
    }
  });

  it('labels every entry it lists', () => {
    pathname = WORKSPACE_PAGE;
    render(<Theme><WorkspaceSidebar /></Theme>);
    for (const link of screen.getAllByRole('link')) {
      expect(link.textContent?.trim(), link.getAttribute('href') ?? '').not.toMatch(/^workspace\.sidebar\./);
    }
    expect(screen.getByText(en.workspace.sidebar.sections.developerSettings)).toBeTruthy();
  });
});
