import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';

const replace = vi.fn();
const setTokens = vi.fn();
const logout = vi.fn();
const exchangeSamlWebCode = vi.fn();
const fetchAndSetCurrentUser = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
}));

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: object) => unknown) => fn({ isHydrated: true, setTokens, logout }),
}));

vi.mock('@/app/(public)/api', () => ({
  AuthApi: { exchangeSamlWebCode: (code: string) => exchangeSamlWebCode(code) },
}));

vi.mock('@/lib/auth/hydrate-user', () => ({
  fetchAndSetCurrentUser: () => fetchAndSetCurrentUser(),
}));

vi.mock('@/app/components/ui/auth-guard', () => ({ LoadingScreen: () => <div>loading</div> }));
vi.mock('@/app/(public)/auth/desktop-handoff-notice', () => ({ default: () => null }));

const CODE = 'a'.repeat(64);

// The page guards against Strict Mode re-runs with module state, so each test loads it afresh.
async function renderPage() {
  vi.resetModules();
  const { default: SamlSsoSuccessPage } = await import('../samlSsoSuccessPage');
  return render(<SamlSsoSuccessPage />);
}

describe('SamlSsoSuccessPage', () => {
  beforeEach(() => {
    [replace, setTokens, logout, exchangeSamlWebCode, fetchAndSetCurrentUser].forEach((m) => m.mockReset());
    fetchAndSetCurrentUser.mockResolvedValue(true);
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState(null, '', '/');
  });

  it('exchanges the code from the fragment, stores the tokens and strips the fragment', async () => {
    window.history.replaceState(null, '', `/auth/sign-in/samlSso/success#code=${CODE}`);
    exchangeSamlWebCode.mockResolvedValue({ accessToken: 'at', refreshToken: 'rt' });

    await renderPage();

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'));
    expect(exchangeSamlWebCode).toHaveBeenCalledWith(CODE);
    expect(setTokens).toHaveBeenCalledWith('at', 'rt');
    expect(window.location.hash).toBe('');
  });

  it('sends the user back to login when there is no code or the exchange fails', async () => {
    window.history.replaceState(null, '', '/auth/sign-in/samlSso/success');
    await renderPage();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login?error=saml_sso'));
    expect(exchangeSamlWebCode).not.toHaveBeenCalled();

    cleanup();
    replace.mockReset();
    window.history.replaceState(null, '', `/auth/sign-in/samlSso/success#code=${CODE}`);
    exchangeSamlWebCode.mockRejectedValue(new Error('401'));
    await renderPage();
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login?error=saml_sso'));
    expect(setTokens).not.toHaveBeenCalled();
  });
});
