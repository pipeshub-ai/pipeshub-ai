import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import OAuthCallbackPage from '../page';

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
});
