import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => true, useIsBelowDesktop: () => true }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (sel: (s: unknown) => unknown) => sel({}),
  selectIsProfileInitialized: () => true,
}));
vi.mock('../api', () => ({
  PatApi: {
    listTokens: vi.fn().mockResolvedValue({
      tokens: [
        {
          id: 't1',
          name: 't5-phone token',
          scopes: ['a', 'b'],
          createdAt: '2026-01-01',
          expiresAt: '2026-06-01',
        },
      ],
    }),
    revokeToken: vi.fn(),
  },
}));

import PersonalAccessTokensPage from '../page';

afterEach(cleanup);

describe('personal access tokens page on a phone', () => {
  it('stacks each token as a card with its dates and Revoke, no sideways table', async () => {
    installBrowserShims();
    renderInTheme(<PersonalAccessTokensPage />);
    expect(await screen.findByText('t5-phone token')).toBeTruthy();
    // The column header row is the desktop table; a card labels each date instead.
    expect(screen.queryByText('Name')).toBeNull();
    expect(screen.getByText(/Created/)).toBeTruthy();
    expect(screen.getByText(/Expires/)).toBeTruthy();
    expect(screen.getByText(/Last used/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Revoke t5-phone token' })).toBeTruthy();
  });
});
