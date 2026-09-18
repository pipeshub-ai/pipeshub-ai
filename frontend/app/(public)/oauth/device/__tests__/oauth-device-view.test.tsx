import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const PHISHING_WARNING = en.oauthDevice.phishingWarning;
const UNREVIEWED_APP = en.oauthConsent.unreviewedApp;

const replace = vi.fn();
let searchParamsGet: (key: string) => string | null = () => null;
let searchParamsToString = () => '';
let authState = { isHydrated: true, isAuthenticated: true };

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => ({
    get: (key: string) => searchParamsGet(key),
    toString: () => searchParamsToString(),
  }),
}));

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: typeof authState) => unknown) => fn(authState),
}));

const post = vi.fn();
vi.mock('@/lib/api', () => ({
  apiClient: {
    post: (...args: unknown[]) => post(...args),
  },
}));

vi.mock('@/lib/api/api-error', () => ({
  extractApiErrorMessage: () => '',
  processError: () => ({ message: 'error' }),
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

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => null,
}));

vi.mock('@/app/components/ui/loading-button', () => ({
  LoadingButton: ({
    children,
    onClick,
    loading,
    ...rest
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    loading?: boolean;
  }) =>
    React.createElement(
      'button',
      { type: 'button', onClick, disabled: loading, ...rest },
      children,
    ),
}));

import { OAuthDeviceView } from '../oauth-device-view';

const h = React.createElement;

function renderView() {
  return render(h(Theme, null, h(OAuthDeviceView, null)));
}

const firstPartyConsent = {
  requiresConsent: true,
  consentData: {
    app: { name: 'PipesHub agent', isDynamic: false },
    scopes: [{ name: 'user:read', description: 'Read users', category: 'User' }],
    user: { email: 'u@e.com', name: 'Test' },
  },
};

beforeEach(() => {
  replace.mockClear();
  post.mockReset();
  searchParamsGet = () => null;
  searchParamsToString = () => '';
  authState = { isHydrated: true, isAuthenticated: true };
});
afterEach(() => cleanup());

describe('OAuthDeviceView', () => {
  it('keeps the phishing warning copy that tells people to close a sent code', () => {
    expect(PHISHING_WARNING).toMatch(/started this yourself/i);
    expect(PHISHING_WARNING).toMatch(/someone sent you this code/i);
    expect(PHISHING_WARNING).toMatch(/close this page/i);
  });

  it('redirects unauthenticated users to login with returnTo', async () => {
    authState = { isHydrated: true, isAuthenticated: false };
    searchParamsToString = () => 'user_code=ABCD-EFGH';
    renderView();
    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith(
        '/login?returnTo=' + encodeURIComponent('/oauth/device?user_code=ABCD-EFGH'),
      );
    });
  });

  it('shows the phishing warning on the code-entry screen', () => {
    renderView();
    expect(screen.getByText(PHISHING_WARNING)).toBeTruthy();
    expect(screen.queryByText(UNREVIEWED_APP)).toBeNull();
  });

  it('shows the phishing warning on Allow and hides unreviewedApp for first-party apps', async () => {
    searchParamsGet = (key: string) => (key === 'user_code' ? 'ABCD-EFGH' : null);
    post.mockResolvedValue({ data: firstPartyConsent });
    renderView();
    await waitFor(() => {
      expect(screen.getByText('PipesHub agent')).toBeTruthy();
    });
    expect(screen.getByText(PHISHING_WARNING)).toBeTruthy();
    expect(screen.queryByText(UNREVIEWED_APP)).toBeNull();
    expect(screen.getByText(en.oauthConsent.allow)).toBeTruthy();
  });

  it('shows unreviewedApp only for dynamically registered apps', async () => {
    searchParamsGet = (key: string) => (key === 'user_code' ? 'ABCD-EFGH' : null);
    post.mockResolvedValue({
      data: {
        requiresConsent: true,
        consentData: {
          app: { name: 'Cursor', isDynamic: true },
          scopes: [{ name: 'user:read', description: '', category: 'User' }],
          user: { email: 'u@e.com' },
        },
      },
    });
    renderView();
    await waitFor(() => {
      expect(screen.getByText(UNREVIEWED_APP)).toBeTruthy();
    });
    expect(screen.getByText(PHISHING_WARNING)).toBeTruthy();
  });

  it('looks up a typed code and posts Allow to the consent API', async () => {
    post.mockResolvedValueOnce({ data: firstPartyConsent }).mockResolvedValueOnce({
      data: { ok: true, consent: 'granted' },
    });
    renderView();
    fireEvent.change(screen.getByLabelText(en.oauthDevice.codeLabel), {
      target: { value: 'ABCD-EFGH' },
    });
    fireEvent.click(screen.getByRole('button', { name: en.oauthDevice.continue }));
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        '/api/v1/oauth2/device/verify',
        { user_code: 'ABCD-EFGH' },
        { suppressErrorToast: true },
      );
    });
    fireEvent.click(screen.getByRole('button', { name: en.oauthConsent.allow }));
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        '/api/v1/oauth2/device/consent',
        { user_code: 'ABCD-EFGH', consent: 'granted' },
        { suppressErrorToast: true },
      );
    });
    expect(screen.getByText(en.oauthDevice.doneTitle)).toBeTruthy();
  });
});
