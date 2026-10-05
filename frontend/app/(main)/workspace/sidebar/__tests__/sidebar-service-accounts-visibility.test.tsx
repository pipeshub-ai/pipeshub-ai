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

const SERVICE_ACCOUNTS_LABEL = en.workspace.sidebar.nav.serviceAccounts;

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
 * Creating a service account grants a machine its own view of the
 * organisation's documents, so the screen belongs with the people who
 * administer access. The API refuses a non-admin regardless; this keeps the
 * navigation honest about it rather than offering a page that will 403.
 */
describe('WorkspaceSidebar — Service accounts', () => {
  it('shows the entry to an administrator', () => {
    userState = { profile: { isAdmin: true } };
    flagsState = { flags: {} };

    renderSidebar();

    expect(screen.getByText(SERVICE_ACCOUNTS_LABEL)).toBeTruthy();
  });

  it('hides it from someone who is not an administrator', () => {
    userState = { profile: { isAdmin: false } };
    flagsState = { flags: {} };

    renderSidebar();

    expect(screen.queryByText(SERVICE_ACCOUNTS_LABEL)).toBeNull();
  });
});
