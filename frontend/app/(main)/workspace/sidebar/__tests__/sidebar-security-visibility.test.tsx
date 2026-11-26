/**
 * Workspace settings › Security is an admin section holding Authentication; it holds no Data
 * classification entry of its own (an extension point may add one through `workspaceNavExtras`).
 * The sidebar links no audit search or file-downloads page: this build has none.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

let pathname = '/workspace/general';
vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
}));

let userState = { profile: { isAdmin: false } as { isAdmin: boolean } | null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
  selectRefreshedIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

let harness = false;
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: object) => unknown) => selector({}),
  selectFeatureFlagsLoaded: () => true,
  selectMcpEnabled: () => false,
  selectActionsEnabled: () => true,
  selectSkillsEnabled: () => true,
  selectScheduledRunsEnabled: () => false,
  selectAgentHarnessOn: () => harness,
  selectAgentHarnessResumeEnabled: () => false,
  selectEngineRunsEnabled: () => false,
}));

// The sidebar's entries as this repository defines them, whatever extras `@/config` a build supplies.
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  workspaceNavExtras: (await import('../workspace-nav-extras')).workspaceNavExtras,
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => {} },
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

const SECURITY = en.workspace.sidebar.sections.security;
const AUTH = en.workspace.sidebar.nav.authentication;
const NO_PAGE_HERE = ['/workspace/audit-search', '/workspace/file-downloads', '/workspace/controls'];

afterEach(cleanup);

const renderSidebar = () => render(<Theme><WorkspaceSidebar /></Theme>);
const securityHeading = () => screen.queryByText(SECURITY);
const expectNoLinkToMissingPages = () => {
  for (const route of NO_PAGE_HERE) expect(document.querySelector(`a[href^="${route}"]`), route).toBeNull();
};

describe('WorkspaceSidebar — Security', () => {
  it('an admin with the harness on finds Authentication under Security, and no Data classification', () => {
    harness = true;
    pathname = '/workspace/general';
    userState = { profile: { isAdmin: true } };
    renderSidebar();
    expect(securityHeading()).toBeTruthy();
    expect(screen.getByText(AUTH).closest('a')?.getAttribute('href')).toMatch(/^\/workspace\/authentication\/?$/);
    expect(document.querySelector('a[href^="/workspace/data-classification"]')).toBeNull();
  });

  it('links no audit search, file downloads or controls page, with every section open', () => {
    harness = true;
    pathname = '/workspace/authentication';
    userState = { profile: { isAdmin: true } };
    renderSidebar();
    for (const toggle of screen.queryAllByRole('button', { expanded: false })) fireEvent.click(toggle);
    expectNoLinkToMissingPages();
  });

  it('with the harness off an admin sees Security with Authentication', () => {
    harness = false;
    pathname = '/workspace/authentication';
    userState = { profile: { isAdmin: true } };
    renderSidebar();
    expect(screen.getByText(AUTH)).toBeTruthy();
    expectNoLinkToMissingPages();
  });

  it('a member never sees the Security section', () => {
    harness = true;
    pathname = '/workspace/general';
    userState = { profile: { isAdmin: false } };
    renderSidebar();
    expect(securityHeading()).toBeNull();
    expect(screen.queryByText(AUTH)).toBeNull();
  });
});
