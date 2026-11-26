import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('next/navigation', () => ({
  usePathname: () => '/workspace/general',
}));

let userState = { profile: { isAdmin: false } as { isAdmin: boolean } | null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
  selectRefreshedIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

let flagsState: { flags: Record<string, boolean> | null } = { flags: null };
let harnessOn = true;
let engineRunsOn = true;
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: typeof flagsState) => unknown) => selector(flagsState),
  selectFeatureFlagsLoaded: (s: typeof flagsState) => s.flags !== null,
  selectMcpEnabled: (s: typeof flagsState) => s.flags?.ENABLE_MCP === true,
  selectActionsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_ACTIONS !== false,
  selectSkillsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_SKILLS !== false,
  selectScheduledRunsEnabled: () => false,
  selectAgentHarnessOn: () => harnessOn,
  selectAgentHarnessResumeEnabled: () => false,
  selectEngineRunsEnabled: () => engineRunsOn,
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

const PERSONAL_LABEL = en.workspace.sidebar.nav.aiAccounts;
const ADMIN_LABEL = en.workspace.sidebar.nav.aiSettings;

afterEach(() => {
  cleanup();
});

function renderSidebar() {
  return render(
    <Theme>
      <WorkspaceSidebar />
    </Theme>,
  );
}

/**
 * U2 SB-1: every user manages their own model keys; only admins manage the
 * organization's cloud accounts and access switches (the routes refuse others).
 */
describe('WorkspaceSidebar — AI accounts', () => {
  it('UX3 SE-4: hides the personal page while no worker runs engine runs (personal keys serve those only)', () => {
    engineRunsOn = false;
    userState = { profile: { isAdmin: true } };
    flagsState = { flags: {} };
    renderSidebar();
    expect(screen.queryByText(PERSONAL_LABEL)).toBeNull();
    expect(screen.getByText(ADMIN_LABEL)).toBeTruthy();
    engineRunsOn = true;
  });

  it('PR1-DK-2: hides both pages while the durable harness is off for the org (model accounts apply to engine runs only)', () => {
    harnessOn = false;
    engineRunsOn = false;
    userState = { profile: { isAdmin: true } };
    flagsState = { flags: {} };
    renderSidebar();
    expect(screen.queryByText(PERSONAL_LABEL)).toBeNull();
    expect(screen.queryByText(ADMIN_LABEL)).toBeNull();
    harnessOn = true;
    engineRunsOn = true;
  });

  it('shows "AI accounts" to everyone and "AI settings" to an administrator', () => {
    userState = { profile: { isAdmin: true } };
    flagsState = { flags: {} };
    renderSidebar();
    expect(screen.getByText(PERSONAL_LABEL).closest('a')?.getAttribute('href')).toMatch(/^\/workspace\/ai-accounts\/?$/);
    expect(screen.getByText(ADMIN_LABEL).closest('a')?.getAttribute('href')).toMatch(/^\/workspace\/ai-access\/?$/);
  });

  it('hides the organization page from someone who is not an administrator', () => {
    userState = { profile: { isAdmin: false } };
    flagsState = { flags: {} };
    renderSidebar();
    expect(screen.getByText(PERSONAL_LABEL)).toBeTruthy();
    expect(screen.queryByText(ADMIN_LABEL)).toBeNull();
  });
});
