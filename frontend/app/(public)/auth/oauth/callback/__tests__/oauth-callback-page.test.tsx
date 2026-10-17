import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => undefined },
  useTranslation: () => ({ t: (key: string) => key }),
}));

import OAuthCallbackPage from '../oauthCallbackPage';

describe('OAuthCallbackPage', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('hands a desktop sign-in to the app without exchanging the code', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    window.history.replaceState(null, '', '/auth/oauth/callback?code=abc&state=phd.x1');

    render(
      <Theme>
        <OAuthCallbackPage />
      </Theme>,
    );

    const link = await screen.findByRole('link', { name: 'auth.desktopHandoff.openApp' });
    expect(link.getAttribute('href')).toBe(
      'pipeshub://auth/oauth/callback?state=phd.x1&code=abc',
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('rejects a callback when this browser never started the sign-in', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    localStorage.removeItem('oauth_state');
    const state = btoa(JSON.stringify({ provider: 'okta' }));
    window.history.replaceState(null, '', `/auth/oauth/callback?code=abc&state=${state}`);

    render(
      <Theme>
        <OAuthCallbackPage />
      </Theme>,
    );

    expect(
      await screen.findByText('Authentication response validation failed. Please try again.'),
    ).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
