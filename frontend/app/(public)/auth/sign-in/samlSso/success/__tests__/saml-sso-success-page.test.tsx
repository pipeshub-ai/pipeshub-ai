import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';

import { SAML_WEB_VERIFIER_KEY } from '@/lib/auth/saml-web-signin';

const replace = vi.fn();
const setTokens = vi.fn();
const logout = vi.fn();
const exchangeSamlCode = vi.fn();
const fetchAndSetCurrentUser = vi.fn();

vi.mock('next/navigation', () => ({ useRouter: () => ({ replace }) }));
vi.mock('@/config', () => ({
  useAuthStore: (select: (s: unknown) => unknown) =>
    select({ isHydrated: true, setTokens, logout }),
}));
vi.mock('@/lib/auth/hydrate-user', () => ({
  fetchAndSetCurrentUser: () => fetchAndSetCurrentUser(),
}));
vi.mock('@/app/(public)/api', () => ({
  AuthApi: { exchangeSamlCode: (code: string, verifier?: string) => exchangeSamlCode(code, verifier) },
}));
vi.mock('@/app/components/ui/auth-guard', () => ({ LoadingScreen: () => <div>loading</div> }));
vi.mock('@/app/(public)/auth/desktop-handoff-notice', () => ({ default: () => null }));

async function renderPage(search: string) {
  window.history.replaceState({}, '', `/auth/sign-in/samlSso/success${search}`);
  // The page keeps a module-level "already ran" flag, so each test needs a fresh copy.
  vi.resetModules();
  const { default: SamlSsoSuccessPage } = await import('../samlSsoSuccessPage');
  return render(<SamlSsoSuccessPage />);
}

describe('SamlSsoSuccessPage (browser sign-in)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    fetchAndSetCurrentUser.mockResolvedValue(true);
  });

  afterEach(() => {
    cleanup();
  });

  it('redeems the code with the stored verifier and signs the user in', async () => {
    sessionStorage.setItem(SAML_WEB_VERIFIER_KEY, 'verifier-1');
    exchangeSamlCode.mockResolvedValue({ accessToken: 'at', refreshToken: 'rt' });

    await renderPage('?code=abc');

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'));
    expect(exchangeSamlCode).toHaveBeenCalledWith('abc', 'verifier-1');
    expect(setTokens).toHaveBeenCalledWith('at', 'rt');
    expect(sessionStorage.getItem(SAML_WEB_VERIFIER_KEY)).toBeNull();
  });

  it('redeems an IdP-initiated code without a stored verifier', async () => {
    exchangeSamlCode.mockResolvedValue({ accessToken: 'at', refreshToken: 'rt' });

    await renderPage('?code=abc');

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'));
    expect(exchangeSamlCode).toHaveBeenCalledWith('abc', undefined);
    expect(setTokens).toHaveBeenCalledWith('at', 'rt');
  });

  it('sends the user back to login when the code is missing', async () => {
    sessionStorage.setItem(SAML_WEB_VERIFIER_KEY, 'verifier-1');

    await renderPage('');

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login?error=saml_sso'));
    expect(exchangeSamlCode).not.toHaveBeenCalled();
    expect(setTokens).not.toHaveBeenCalled();
  });

  it('sends the user back to login when the code is rejected', async () => {
    sessionStorage.setItem(SAML_WEB_VERIFIER_KEY, 'verifier-1');
    exchangeSamlCode.mockRejectedValue(new Error('401'));

    await renderPage('?code=abc');

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login?error=saml_sso'));
    expect(logout).toHaveBeenCalled();
    expect(setTokens).not.toHaveBeenCalled();
  });
});
