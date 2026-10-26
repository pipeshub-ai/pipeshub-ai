import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const push = vi.fn();
const replace = vi.fn();
const setTokens = vi.fn();
const setUser = vi.fn();
const signUp = vi.fn();
const initAuth = vi.fn();
const signInWithPassword = vi.fn();
const fetchAndSetCurrentUser = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push }),
}));

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: object) => unknown) => fn({ isHydrated: true, setTokens, setUser }),
}));

vi.mock('@/lib/api/org-exists-public', () => ({
  getOrgExists: () => Promise.resolve({ exists: false }),
}));

vi.mock('../../api', () => ({
  AuthApi: {
    signUp: (payload: unknown) => signUp(payload),
    initAuth: () => initAuth(),
    signInWithPassword: (email: string, password: string) => signInWithPassword(email, password),
  },
}));

vi.mock('@/lib/auth/hydrate-user', () => ({
  fetchAndSetCurrentUser: () => fetchAndSetCurrentUser(),
}));
vi.mock('@/lib/store/toast-store', () => ({
  toast: {
    loading: vi.fn(() => 'toast-id'),
    dismiss: vi.fn(),
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));
vi.mock('@/lib/hooks/use-breakpoint', () => ({ useAuthWideLayout: () => false }));
vi.mock('@/app/components/ui/guest-guard', () => ({
  GuestGuard: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/auth-guard', () => ({ LoadingScreen: () => <div>loading</div> }));
vi.mock('../../components/auth-hero', () => ({ default: () => null }));
vi.mock('../../components/auth-title-section', () => ({ default: () => null }));
vi.mock('../../components/form-panel', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
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

import SignUpPage from '../signUpPage';

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const EMAIL = 'first.admin@example.com';
const PASSWORD = 'Str0ng-Passw0rd!';

async function signUpThroughTheForm() {
  render(
    <Theme>
      <SignUpPage />
    </Theme>,
  );
  const fill = (id: string, value: string) =>
    fireEvent.change(document.getElementById(id) as HTMLInputElement, { target: { value } });
  await waitFor(() => expect(document.getElementById('first-name')).not.toBeNull());
  fill('first-name', 'First');
  fill('last-name', 'Admin');
  fill('registered-name', 'Acme');
  fill('signup-email', EMAIL);
  fill('signup-password', PASSWORD);
  fill('signup-confirm-password', PASSWORD);
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Sign up' }));
}

describe('SignUpPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('ResizeObserver', NoopResizeObserver);
    signUp.mockResolvedValue(undefined);
    initAuth.mockResolvedValue({});
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('signs the new admin in and opens onboarding, since creating the org returns no session', async () => {
    signInWithPassword.mockResolvedValue({ accessToken: 'access', refreshToken: 'refresh' });

    await signUpThroughTheForm();

    await waitFor(() => expect(push).toHaveBeenCalledWith('/onboarding'));
    expect(signUp).toHaveBeenCalledWith(expect.objectContaining({ email: EMAIL, registeredName: 'Acme' }));
    expect(initAuth).toHaveBeenCalledTimes(1);
    expect(signInWithPassword).toHaveBeenCalledWith(EMAIL, PASSWORD);
    expect(setTokens).toHaveBeenCalledWith('access', 'refresh');
    expect(fetchAndSetCurrentUser).toHaveBeenCalledTimes(1);
    expect(toastSuccess).toHaveBeenCalledWith(en.auth.signUp.successToast);
  });

  it('sends the admin to the login page when the account exists but signing in fails', async () => {
    signInWithPassword.mockRejectedValue(new Error('Network Error'));

    await signUpThroughTheForm();

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login'));
    expect(setTokens).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    expect(toastSuccess).toHaveBeenCalledWith(en.auth.signUp.accountCreatedToast, {
      description: en.auth.signUp.accountCreatedDescription,
    });
  });

  it('treats a sign-in answer with no tokens as a failed sign-in', async () => {
    signInWithPassword.mockResolvedValue({ nextStep: 1 });

    await signUpThroughTheForm();

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login'));
    expect(setTokens).not.toHaveBeenCalled();
  });

  it('does not try to sign in when creating the org fails', async () => {
    signUp.mockRejectedValue({ response: { status: 400, data: { message: 'There is already an organization' } } });

    await signUpThroughTheForm();

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('There is already an organization'));
    expect(initAuth).not.toHaveBeenCalled();
    expect(signInWithPassword).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});
