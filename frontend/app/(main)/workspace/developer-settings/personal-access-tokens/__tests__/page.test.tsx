import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (sel: (s: unknown) => unknown) => sel({}),
  selectIsProfileInitialized: () => true,
}));
vi.mock('../api', () => ({
  PatApi: {
    listTokens: vi.fn().mockResolvedValue({
      tokens: [{ id: 't1', name: 'No scopes token', createdAt: '2026-01-01', expiresAt: '2026-06-01' }],
    }),
    revokeToken: vi.fn(),
  },
}));

import PersonalAccessTokensPage from '../page';

afterEach(cleanup);

describe('personal access tokens page', () => {
  it('lists a token whose scopes are missing', async () => {
    installBrowserShims();
    renderInTheme(<PersonalAccessTokensPage />);
    expect(await screen.findByText('No scopes token')).toBeTruthy();
  });
});
