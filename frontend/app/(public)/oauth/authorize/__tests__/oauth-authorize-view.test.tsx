import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const NOT_GRANTED_HEADING = en.oauthConsent.notGrantedHeading;
const OFFLINE_ACCESS_WARNING = en.oauthConsent.notGrantedOfflineAccess;
const OWNER_HINT = en.oauthConsent.notGrantedOwnerHint;

const params = new URLSearchParams({
  client_id: 'cid',
  redirect_uri: 'https://claude.ai/api/mcp/auth_callback',
  scope: 'offline_access kb:read agent:read',
  state: 's1',
});

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => params,
}));

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: { isHydrated: boolean; isAuthenticated: boolean }) => unknown) =>
    fn({ isHydrated: true, isAuthenticated: true }),
}));

const get = vi.fn();
vi.mock('@/lib/api', () => ({
  apiClient: {
    get: (...args: unknown[]) => get(...args),
    post: vi.fn(),
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

import { OAuthAuthorizeView } from '../oauth-authorize-view';

const h = React.createElement;

function consentResponse(
  notGrantedScopes: { name: string; description: string; category: string }[],
  isDynamic = false,
) {
  return {
    data: {
      requiresConsent: true,
      consentData: {
        app: { name: 'Claude', isDynamic },
        scopes: [{ name: 'kb:read', description: 'Read knowledge bases', category: 'Knowledge Base' }],
        notGrantedScopes,
        user: { email: 'u@e.com' },
        redirectUri: 'https://claude.ai/api/mcp/auth_callback',
        state: 's1',
      },
    },
  };
}

describe('OAuthAuthorizeView not-granted scopes', () => {
  beforeEach(() => {
    get.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('lists requested scopes the app will not get, with the offline_access warning', async () => {
    get.mockResolvedValue(
      consentResponse([
        { name: 'offline_access', description: 'Refresh tokens', category: 'Identity' },
        { name: 'agent:read', description: 'Read agents', category: 'Agents' },
      ]),
    );

    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    expect(await screen.findByText(NOT_GRANTED_HEADING)).toBeTruthy();
    expect(screen.getByText('agent:read')).toBeTruthy();
    expect(screen.getByText(OFFLINE_ACCESS_WARNING)).toBeTruthy();
    expect(screen.getByText(OWNER_HINT)).toBeTruthy();
  });

  it('does not tell users of a dynamically registered app to edit it, because nobody can', async () => {
    get.mockResolvedValue(
      consentResponse([{ name: 'agent:read', description: 'Read agents', category: 'Agents' }], true),
    );

    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    expect(await screen.findByText(NOT_GRANTED_HEADING)).toBeTruthy();
    expect(screen.queryByText(OWNER_HINT)).toBeNull();
  });

  it('shows no not-granted section when every requested scope is granted', async () => {
    get.mockResolvedValue(consentResponse([]));

    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    expect(await screen.findByText('kb:read')).toBeTruthy();
    expect(screen.queryByText(NOT_GRANTED_HEADING)).toBeNull();
    expect(screen.queryByText(OFFLINE_ACCESS_WARNING)).toBeNull();
  });
});
