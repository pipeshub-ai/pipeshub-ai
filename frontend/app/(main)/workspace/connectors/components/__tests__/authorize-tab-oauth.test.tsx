import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const getOAuthAuthorizationUrl = vi.fn();
const getConnectorSchema = vi.fn();
const getConnectorConfig = vi.fn();

vi.mock('../../api', () => ({
  ConnectorsApi: {
    getOAuthAuthorizationUrl: (...args: unknown[]) => getOAuthAuthorizationUrl(...args),
    getConnectorSchema: (...args: unknown[]) => getConnectorSchema(...args),
    getConnectorConfig: (...args: unknown[]) => getConnectorConfig(...args),
  },
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AuthorizeTab } from '../authorize-tab';
import { useConnectorOAuthPopup } from '../authenticate-tab/use-connector-oauth-popup';
import { useConnectorsStore } from '../../store';
import { CONNECTOR_OAUTH_POST_MESSAGE } from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
import type { AuthSchemaField } from '../../types';
import {
  installDomShims,
  renderInTheme,
  signInAs,
  makeConnector,
  makeSchema,
  makeConfig,
  toasts,
  clearToasts,
} from '../../__tests__/fixtures';

const oauthFields: AuthSchemaField[] = [
  { name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true },
];
const AUTH_URL = 'https://auth.atlassian.com/authorize?client_id=abc&state=xyz';

/** Same wiring as ConnectorPanel: the hook owns the popup, the tab only renders it. */
function OAuthStep() {
  const { startOAuthPopup, isAuthenticating } = useConnectorOAuthPopup();
  return <AuthorizeTab startOAuthPopup={startOAuthPopup} isAuthenticating={isAuthenticating} />;
}

type FakePopup = { closed: boolean; focus: () => void; close: () => void };

function fakePopup(): FakePopup {
  const popup: FakePopup = {
    closed: false,
    focus: vi.fn(),
    close: vi.fn(() => {
      popup.closed = true;
    }),
  };
  return popup;
}

function openInstance(authenticated: boolean) {
  const connector = makeConnector({ _key: 'conn-1', authType: 'OAUTH', isAuthenticated: authenticated });
  const store = useConnectorsStore.getState();
  store.openPanel(connector, 'conn-1', 'team');
  store.setSchemaAndConfig(
    makeSchema({ OAUTH: oauthFields }),
    makeConfig({ authType: 'OAUTH', isAuthenticated: authenticated })
  );
}

function postFromCallback(type: string, origin = window.location.origin) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data: { type }, origin }));
  });
}

const signInButton = () => screen.getByRole('button', { name: 'Authenticate Jira to Proceed' });

let openSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  clearToasts();
  signInAs('admin');
  getOAuthAuthorizationUrl.mockReset().mockResolvedValue({ authorizationUrl: AUTH_URL });
  getConnectorSchema.mockReset().mockResolvedValue({ schema: makeSchema({ OAUTH: oauthFields }) });
  getConnectorConfig
    .mockReset()
    .mockResolvedValue(makeConfig({ authType: 'OAUTH', isAuthenticated: true }));
  openSpy = vi.spyOn(window, 'open');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('Authorize tab: starting OAuth', () => {
  it('asks the user to sign in with the provider before the instance is connected', () => {
    openInstance(false);
    renderInTheme(<OAuthStep />);

    expect(screen.getByText('Sign in with your provider')).toBeTruthy();
    expect(signInButton()).toBeTruthy();
    expect(screen.queryByText(/Connected/)).toBeNull();
  });

  it("opens the provider's sign-in page from the server in a centred popup", async () => {
    const popup = fakePopup();
    openSpy.mockReturnValue(popup as unknown as Window);
    openInstance(false);
    renderInTheme(<OAuthStep />);

    fireEvent.click(signInButton());

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
    expect(getOAuthAuthorizationUrl).toHaveBeenCalledWith('conn-1');
    const [url, name, features] = openSpy.mock.calls[0] as [string, string, string];
    expect(url).toBe(AUTH_URL);
    expect(name).toBe('connector-oauth-conn-1');
    expect(features).toContain('width=600');
    expect(features).toContain('height=700');
    // `noopener` would drop window.opener, which the callback page needs to report back.
    expect(features).not.toContain('noopener');
    expect(popup.focus).toHaveBeenCalled();
  });

  it('shows that sign-in is in progress while the popup is open', async () => {
    openSpy.mockReturnValue(fakePopup() as unknown as Window);
    openInstance(false);
    renderInTheme(<OAuthStep />);

    fireEvent.click(signInButton());

    expect(await screen.findByText('Authenticating...')).toBeTruthy();
    expect(useConnectorsStore.getState().authState).toBe('authenticating');
  });

  it('puts the button back when the browser blocks the popup', async () => {
    openSpy.mockReturnValue(null);
    openInstance(false);
    renderInTheme(<OAuthStep />);

    fireEvent.click(signInButton());

    await waitFor(() => expect(openSpy).toHaveBeenCalled());
    await waitFor(() => expect(useConnectorsStore.getState().authState).toBe('empty'));
    expect(signInButton()).toBeTruthy();
    expect(screen.queryByText('Authenticating...')).toBeNull();
  });

  it('tells the user to allow popups when the browser blocks the sign-in window', async () => {
    openSpy.mockReturnValue(null);
    openInstance(false);
    renderInTheme(<OAuthStep />);

    fireEvent.click(signInButton());

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      title: 'Popup blocked. Allow popups for this site and try again.',
    });
  });

  it('shows a failure when the sign-in link cannot be fetched', async () => {
    getOAuthAuthorizationUrl.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down' });
    openInstance(false);
    renderInTheme(<OAuthStep />);

    fireEvent.click(signInButton());

    expect(await screen.findByText('Failed to Authenticate your Jira')).toBeTruthy();
    expect(openSpy).not.toHaveBeenCalled();
  });
});

describe('Authorize tab: finishing OAuth', () => {
  it('confirms the connection once the callback reports success and the server agrees', async () => {
    const popup = fakePopup();
    openSpy.mockReturnValue(popup as unknown as Window);
    openInstance(false);
    renderInTheme(<OAuthStep />);
    fireEvent.click(signInButton());
    await waitFor(() => expect(openSpy).toHaveBeenCalled());

    postFromCallback(CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS);

    expect(
      await screen.findByText('Connected — you can continue to Configure records', {}, { timeout: 4000 })
    ).toBeTruthy();
    expect(getConnectorConfig).toHaveBeenCalledWith('conn-1');
    expect(popup.close).toHaveBeenCalled();
    expect(useConnectorsStore.getState().panelActiveTab).toBe('authorize');
  });

  it('ignores a success message from another origin', async () => {
    vi.useFakeTimers();
    openSpy.mockReturnValue(fakePopup() as unknown as Window);
    openInstance(false);
    renderInTheme(<OAuthStep />);
    fireEvent.click(signInButton());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    postFromCallback(CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS, 'https://evil.example');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(getConnectorConfig).not.toHaveBeenCalled();
    expect(screen.queryByText(/Connected/)).toBeNull();
  });

  it('shows a failure when the callback reports an error', async () => {
    openSpy.mockReturnValue(fakePopup() as unknown as Window);
    openInstance(false);
    renderInTheme(<OAuthStep />);
    fireEvent.click(signInButton());
    await waitFor(() => expect(openSpy).toHaveBeenCalled());

    postFromCallback(CONNECTOR_OAUTH_POST_MESSAGE.ERROR);

    expect(await screen.findByText('Failed to Authenticate your Jira')).toBeTruthy();
    expect(useConnectorsStore.getState().authState).toBe('failed');
  });

  it('returns to the sign-in button when the user closes the popup without finishing', async () => {
    vi.useFakeTimers();
    getConnectorConfig.mockResolvedValue(makeConfig({ authType: 'OAUTH', isAuthenticated: false }));
    const popup = fakePopup();
    openSpy.mockReturnValue(popup as unknown as Window);
    openInstance(false);
    renderInTheme(<OAuthStep />);
    fireEvent.click(signInButton());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    popup.closed = true;
    // One poll tick notices the closed popup, then five spaced checks with the server.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000 + 5 * 1500 + 100);
    });

    expect(getConnectorConfig).toHaveBeenCalledTimes(5);
    expect(useConnectorsStore.getState().authState).toBe('empty');
    expect(signInButton()).toBeTruthy();
    expect(screen.queryByText(/Connected/)).toBeNull();
  });
});

describe('Authorize tab: already connected', () => {
  it('shows the connection and offers to sign in again', async () => {
    openSpy.mockReturnValue(fakePopup() as unknown as Window);
    openInstance(true);
    renderInTheme(<OAuthStep />);

    expect(screen.getByText('Authorization status')).toBeTruthy();
    expect(screen.getByText('Connected — you can continue to Configure records')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Re-authenticate with provider' }));
    await waitFor(() => expect(getOAuthAuthorizationUrl).toHaveBeenCalledWith('conn-1'));
  });

  it('warns when signing in again did not complete', () => {
    openInstance(true);
    useConnectorsStore.getState().setAuthState('failed');
    renderInTheme(<OAuthStep />);

    expect(
      screen.getByText('Sign-in did not complete. Try again, or check your identity provider settings.')
    ).toBeTruthy();
  });

  it('renders nothing before the instance exists', () => {
    useConnectorsStore.getState().openPanel(makeConnector(), undefined, 'team');
    renderInTheme(<OAuthStep />);
    expect(screen.queryByText('Sign in with your provider')).toBeNull();
  });
});
