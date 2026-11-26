/**
 * BETA: the agent harness is in beta, so each of its workspace entries carries the Beta badge where it is shown, and
 * none of them is shown while the harness is off.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('next/navigation', () => ({
  usePathname: () => '/workspace/general',
}));

vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => false,
}));

vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { profile: { isAdmin: boolean } }) => unknown) => selector({ profile: { isAdmin: true } }),
  selectIsAdmin: (s: { profile: { isAdmin: boolean } }) => s.profile.isAdmin,
  selectRefreshedIsAdmin: (s: { profile: { isAdmin: boolean } }) => s.profile.isAdmin,
}));

let harnessOn = true;
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: { flags: Record<string, boolean> }) => unknown) =>
    selector({ flags: { ENABLE_SKILLS: false } }),
  selectFeatureFlagsLoaded: () => true,
  selectMcpEnabled: () => true,
  selectActionsEnabled: () => true,
  selectSkillsEnabled: () => false,
  selectScheduledRunsEnabled: () => harnessOn,
  selectAgentHarnessOn: () => harnessOn,
  selectAgentHarnessResumeEnabled: () => harnessOn,
  selectEngineRunsEnabled: () => harnessOn,
}));

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

const HARNESS_ENTRIES = [
  en.workspace.sidebar.nav.aiSettings,
  en.workspace.sidebar.nav.aiAccounts,
  en.workspace.sidebar.nav.schedules,
  en.workspace.sidebar.nav.memories,
];

afterEach(() => {
  cleanup();
  harnessOn = true;
});

const renderSidebar = () => render(<Theme><WorkspaceSidebar /></Theme>);
const entry = (label: string) => screen.getByText(label).closest('a') as HTMLElement;

describe('WorkspaceSidebar — the agent harness entries are Beta', () => {
  it.each(HARNESS_ENTRIES)('"%s" carries the Beta badge', (label) => {
    renderSidebar();
    expect(within(entry(label)).getByText(en.common.beta.label)).toBeTruthy();
  });

  it('marks no other workspace entry', () => {
    renderSidebar();
    expect(screen.getAllByText(en.common.beta.label)).toHaveLength(HARNESS_ENTRIES.length);
    expect(within(entry(en.workspace.sidebar.nav.aiModels)).queryByText(en.common.beta.label)).toBeNull();
  });

  it('shows no harness entry and no badge for it while the harness is off', () => {
    harnessOn = false;
    renderSidebar();
    for (const label of HARNESS_ENTRIES) expect(screen.queryByText(label)).toBeNull();
    expect(screen.queryByText(en.common.beta.label)).toBeNull();
  });
});
