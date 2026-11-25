/**
 * The workspace sidebar renders the entries `workspaceNavExtras` (config.ts)
 * adds, with the built-in visibility rules: admin-only, behind a flag.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('next/navigation', () => ({ usePathname: () => '/workspace/general' }));

let userState = { profile: { isAdmin: true } as { isAdmin: boolean } | null };
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
  selectActionsEnabled: () => false,
  selectSkillsEnabled: () => false,
  selectScheduledRunsEnabled: () => false,
  selectAgentHarnessOn: () => harness,
  selectAgentHarnessResumeEnabled: () => false,
  selectEngineRunsEnabled: () => false,
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => {} },
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('@/config', () => ({
  workspaceNavExtras: [
    { section: 'workspace', icon: 'star', labelKey: 'extra.admin', route: '/workspace/extra-admin', adminOnly: true },
    { section: 'personal', icon: 'star', labelKey: 'extra.flagged', route: '/workspace/extra-flagged', requiresFlag: 'agentHarness' },
    { section: 'workspace', icon: 'star', labelKey: 'extra.mail', route: '/workspace/mail' },
  ],
}));

import WorkspaceSidebar from '../index';

afterEach(cleanup);

const renderSidebar = () => render(<Theme><WorkspaceSidebar /></Theme>);
const hrefOf = (label: string) => screen.getByText(label).closest('a')?.getAttribute('href');

describe('WorkspaceSidebar — entries an edition adds', () => {
  it('an admin finds an added admin entry, linked to its route', () => {
    harness = false;
    userState = { profile: { isAdmin: true } };
    renderSidebar();
    expect(hrefOf('extra.admin')).toMatch(/^\/workspace\/extra-admin\/?$/);
  });

  it('an added entry for a built-in route replaces that entry', () => {
    userState = { profile: { isAdmin: true } };
    renderSidebar();
    expect(hrefOf('extra.mail')).toMatch(/^\/workspace\/mail\/?$/);
    expect(screen.queryByText('workspace.sidebar.nav.mail')).toBeNull();
  });

  it('a member does not see an added admin entry', () => {
    userState = { profile: { isAdmin: false } };
    renderSidebar();
    expect(screen.queryByText('extra.admin')).toBeNull();
  });

  it('an added entry behind a flag shows only with the flag on', () => {
    userState = { profile: { isAdmin: false } };
    harness = false;
    renderSidebar();
    expect(screen.queryByText('extra.flagged')).toBeNull();
    cleanup();
    harness = true;
    renderSidebar();
    expect(hrefOf('extra.flagged')).toMatch(/^\/workspace\/extra-flagged\/?$/);
  });
});
