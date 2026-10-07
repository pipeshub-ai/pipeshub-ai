import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('next/navigation', () => ({
  usePathname: () => '/workspace/general',
}));

// user-store and feature-flags-store both persist to real `localStorage`
// (via zustand `persist`/`createJSONStorage`), which jsdom in this test
// environment doesn't implement — swap in trivial in-memory stores exposing
// just the selector surface `WorkspaceSidebar` reads, controlled directly
// via module-level mutable state per test.
let userState = { profile: { isAdmin: false } as { isAdmin: boolean } | null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

let flagsState: { flags: Record<string, boolean> | null } = { flags: null };
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: typeof flagsState) => unknown) => selector(flagsState),
  selectFeatureFlagsLoaded: (s: typeof flagsState) => s.flags !== null,
  selectMcpEnabled: (s: typeof flagsState) => s.flags?.ENABLE_MCP === true,
  selectActionsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_ACTIONS !== false,
  selectSkillsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_SKILLS !== false,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split('.');
      let cur: unknown = en;
      for (const part of parts) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) {
          return key;
        }
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

import WorkspaceSidebar from '../index';

const YOUR_SKILLS_LABEL = en.workspace.sidebar.nav.yourSkills;
const BETA_LABEL = en.common.beta.label;

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

describe('WorkspaceSidebar — Your Skills (Beta, flag-gated)', () => {
  it('shows "Your Skills" with a Beta badge when ENABLE_SKILLS is on', () => {
    userState = { profile: { isAdmin: false } };
    flagsState = { flags: { ENABLE_SKILLS: true } };

    renderSidebar();

    expect(screen.getByText(YOUR_SKILLS_LABEL)).toBeTruthy();
    expect(screen.getByText(BETA_LABEL)).toBeTruthy();
  });

  it('shows "Your Skills" by default before flags load (defaults to enabled)', () => {
    userState = { profile: { isAdmin: false } };
    flagsState = { flags: null };

    renderSidebar();

    expect(screen.getByText(YOUR_SKILLS_LABEL)).toBeTruthy();
  });

  it('hides "Your Skills" once an admin has explicitly turned ENABLE_SKILLS off', () => {
    userState = { profile: { isAdmin: false } };
    flagsState = { flags: { ENABLE_SKILLS: false } };

    renderSidebar();

    expect(screen.queryByText(YOUR_SKILLS_LABEL)).toBeNull();
  });
});
