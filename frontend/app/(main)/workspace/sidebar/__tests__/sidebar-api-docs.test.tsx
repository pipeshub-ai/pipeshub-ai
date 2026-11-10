import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
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

let userState = { profile: { isAdmin: false } as { isAdmin: boolean } | null };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof userState) => unknown) => selector(userState),
  selectIsAdmin: (s: typeof userState) => s.profile?.isAdmin ?? null,
}));

let flagsState: { flags: Record<string, boolean> | null } = { flags: null };
vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: typeof flagsState) => unknown) => selector(flagsState),
  selectFeatureFlagsLoaded: (s: typeof flagsState) => s.flags !== null,
  selectMcpEnabled: (s: typeof flagsState) => s.flags?.ENABLE_MCP !== false,
  selectActionsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_ACTIONS !== false,
  selectSkillsEnabled: (s: typeof flagsState) => s.flags?.ENABLE_SKILLS !== false,
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => undefined },
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

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('WorkspaceSidebar — API documentation', () => {
  it.each(['https://api.pipeshub.test', 'https://api.pipeshub.test/'])('opens the API reference on %s in a new tab', (base) => {
    userState = { profile: { isAdmin: false } };
    flagsState = { flags: {} };
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', base);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);

    render(
      <Theme>
        <WorkspaceSidebar />
      </Theme>,
    );
    fireEvent.click(screen.getByText(en.workspace.sidebar.nav.apiDocumentation));

    expect(open).toHaveBeenCalledWith('https://api.pipeshub.test/api/v1/docs', '_blank', 'noopener,noreferrer');
  });
});
