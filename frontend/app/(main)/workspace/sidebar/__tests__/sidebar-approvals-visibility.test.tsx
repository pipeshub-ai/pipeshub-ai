import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('next/navigation', () => ({
  usePathname: () => '/workspace/general',
}));

let userState = { profile: { isAdmin: false } as { isAdmin: boolean } | null };
let harnessOn = true;
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
  selectRefreshedIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: { flags: Record<string, boolean> }) => unknown) => selector({ flags: {} }),
  selectFeatureFlagsLoaded: () => true,
  selectMcpEnabled: () => false,
  selectActionsEnabled: () => true,
  selectSkillsEnabled: () => true,
  selectScheduledRunsEnabled: () => false,
  selectAgentHarnessOn: () => harnessOn,
  selectAgentHarnessResumeEnabled: () => false,
  selectEngineRunsEnabled: () => false,
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

afterEach(() => {
  cleanup();
});

const renderSidebar = () =>
  render(
    <Theme>
      <WorkspaceSidebar />
    </Theme>,
  );

const linkTo = (route: RegExp) =>
  Array.from(document.querySelectorAll('a')).find((a) => route.test(a.getAttribute('href') ?? ''));

/**
 * P2.11e SB-2: a person decides their own runs' asks in the conversation and the Inbox, and says whether an action
 * of unknown outcome happened from its card. The built-in sidebar lists no page of org approval rules, no queue of
 * other people's approvals and no console of other people's unknown effects (an edition may add them), for admins
 * and members, with the harness on or off.
 */
describe('WorkspaceSidebar — approvals', () => {
  for (const on of [true, false]) {
    for (const isAdmin of [true, false]) {
      it(`lists no org approval pages (harness ${on ? 'on' : 'off'}, ${isAdmin ? 'admin' : 'member'})`, () => {
        harnessOn = on;
        userState = { profile: { isAdmin } };
        renderSidebar();
        expect(linkTo(/^\/workspace\/approval-policy\/?$/)).toBeUndefined();
        expect(linkTo(/^\/workspace\/approvals\/?$/)).toBeUndefined();
        expect(linkTo(/^\/workspace\/unknown-effects\/?$/)).toBeUndefined();
        harnessOn = true;
      });
    }
  }
});
