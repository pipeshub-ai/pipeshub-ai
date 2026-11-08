import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const api = vi.hoisted(() => ({ completeOAuthCallback: vi.fn() }));
vi.mock('@/app/(main)/workspace/api-toolsets/api', () => ({ ApiToolsetsApi: api }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import ApiToolsetOAuthCallbackPage from '../page';

const opener = { postMessage: vi.fn() };

beforeEach(() => {
  api.completeOAuthCallback.mockReset();
  opener.postMessage.mockReset();
  Object.defineProperty(window, 'opener', { value: opener, configurable: true });
  // The page closes its popup when done; closing jsdom's only window would end the test.
  vi.spyOn(window, 'close').mockImplementation(() => {});
  window.history.replaceState(null, '', '/api-toolsets/oauth/callback/?code=c1&state=s1');
});
afterEach(cleanup);

describe('API toolset OAuth callback page', () => {
  it('completes the sign-in through the API toolsets route and tells the opener which toolset', async () => {
    api.completeOAuthCallback.mockResolvedValue({ success: true, instanceId: 'abc', name: 'Orders' });
    renderInTheme(<ApiToolsetOAuthCallbackPage />);
    expect(await screen.findByText('Connected successfully')).toBeTruthy();
    expect(api.completeOAuthCallback).toHaveBeenCalledWith({ code: 'c1', state: 's1' });
    await waitFor(() =>
      expect(opener.postMessage).toHaveBeenCalledWith({ type: 'MCP_OAUTH_SUCCESS', instanceId: 'abc' }, window.location.origin),
    );
  });

  it('hands a sign-in started from the desktop app back to the app as an API action sign-in', async () => {
    window.history.replaceState(null, '', '/api-toolsets/oauth/callback/?code=c1&state=phd.s1');
    renderInTheme(<ApiToolsetOAuthCallbackPage />);
    const link = await waitFor(() => {
      const found = document.querySelector('a[href*="integration/toolset/callback"]');
      expect(found).toBeTruthy();
      return found!;
    });
    expect(link.getAttribute('href')).toContain('state=phd.s1');
    expect(api.completeOAuthCallback).not.toHaveBeenCalled();
  });

  it("shows and forwards the backend's reason when the sign-in is refused", async () => {
    api.completeOAuthCallback.mockRejectedValue(new Error('This sign-in link has expired.'));
    renderInTheme(<ApiToolsetOAuthCallbackPage />);
    expect(await screen.findByText('This sign-in link has expired.')).toBeTruthy();
    expect(opener.postMessage).toHaveBeenCalledWith(
      { type: 'MCP_OAUTH_ERROR', error: 'This sign-in link has expired.' },
      window.location.origin,
    );
  });
});
