import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const getOrgExists = vi.fn();
const invalidateOrgExistsCache = vi.fn();
const initAuth = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: { isHydrated: boolean }) => unknown) => fn({ isHydrated: true }),
}));

vi.mock('@/lib/api/org-exists-public', () => ({
  getOrgExists: () => getOrgExists(),
  invalidateOrgExistsCache: () => invalidateOrgExistsCache(),
}));

vi.mock('../../api', () => ({
  AuthApi: { initAuth: () => initAuth() },
}));

vi.mock('@/lib/electron', () => ({ isElectron: () => false }));
vi.mock('@/lib/store/auth-store', () => ({ requestElectronServerUrlChange: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock('@/lib/hooks/use-breakpoint', () => ({ useAuthWideLayout: () => false }));
vi.mock('@/app/components/ui/guest-guard', () => ({
  GuestGuard: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/auth-guard', () => ({ LoadingScreen: () => <div>loading</div> }));
vi.mock('../../components/auth-hero', () => ({ default: () => null }));
vi.mock('../../components/form-panel', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('../../forms', () => ({
  SingleProvider: ({ method }: { method: string }) => <div>single:{method}</div>,
  MultipleProviders: () => <div>multiple</div>,
}));

vi.mock('react-i18next', () => ({
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

import LoginPage from '../loginPage';

function renderPage() {
  return render(
    <Theme>
      <LoginPage />
    </Theme>,
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    getOrgExists.mockReset();
    invalidateOrgExistsCache.mockReset();
    initAuth.mockReset();
  });

  afterEach(() => cleanup());

  it('shows an error with retry, not a password form, when the server is unreachable', async () => {
    getOrgExists.mockRejectedValueOnce(new Error('Network Error'));
    renderPage();

    await screen.findByText(en.auth.login.loadFailedTitle);
    expect(screen.queryByText('single:password')).toBeNull();

    getOrgExists.mockResolvedValueOnce({ exists: true });
    initAuth.mockResolvedValueOnce({ allowedMethods: ['samlSso'], authProviders: {} });
    fireEvent.click(screen.getByRole('button', { name: en.auth.login.retry }));

    await waitFor(() => expect(screen.getByText('single:samlSso')).toBeTruthy());
    expect(invalidateOrgExistsCache).toHaveBeenCalledTimes(1);
  });
});
