import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({
  getMyUserAccessConnectors: vi.fn(),
  connectUserAccess: vi.fn(),
  startUserAccessSignIn: vi.fn(),
  getUserAccess: vi.fn(),
  disconnectUserAccess: vi.fn(),
  listUserAccessConnections: vi.fn(),
  removeUserAccessConnection: vi.fn(),
  getUserAccessRoster: vi.fn(),
  inviteUserAccess: vi.fn(),
  setUserAccessReminders: vi.fn(),
  getUserAccessLinkInfo: vi.fn(),
  authorizeUserAccessLink: vi.fn(),
  optOutUserAccessLink: vi.fn(),
  completeUserAccessLink: vi.fn(),
  completeConnectorOAuthCallback: vi.fn(),
}));
const nav = vi.hoisted(() => ({
  params: new URLSearchParams(),
  router: { replace: vi.fn(), push: vi.fn() },
}));
vi.mock('../../../api', () => ({ ConnectorsApi: api }));
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  usePathname: () => '/workspace/connectors/personal/',
  useSearchParams: () => nav.params,
}));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { MyAccountConnections } from '../my-account-connections';
import { ConnectedUsers } from '../connected-users';
import { ConnectAccountBanner, forgetMyUserAccessConnectors } from '../connect-banner';
import { ConnectAccountClient } from '@/app/(public)/connect-account/connect-account-client';
import { ConnectorOAuthCallbackClient } from '@/app/(public)/connectors/oauth/callback/connector-oauth-callback-client';
import { installDomShims, renderInTheme, toastTitles, toasts, clearToasts } from '../../../__tests__/fixtures';
import type { UserAccessConnection, UserAccessRoster } from '../../../types';

const connection = (over: Partial<UserAccessConnection> = {}): UserAccessConnection => ({
  userId: 'u-1',
  email: 'dana@example.com',
  sourceUserId: 'n-1',
  sourceEmail: 'dana@example.com',
  workspaceId: 'w-1',
  status: 'active',
  connectedAt: 1,
  lastCompleteListingAt: null,
  lastError: null,
  ...over,
});

beforeEach(() => {
  installDomShims();
  clearToasts();
  Object.values(api).forEach((fn) => fn.mockReset());
  nav.params = new URLSearchParams();
  nav.router.replace.mockReset();
  forgetMyUserAccessConnectors();
  sessionStorage.clear();
});
afterEach(() => cleanup());

describe('MyAccountConnections', () => {
  it('renders nothing when no connector is in per-user mode', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([]);
    const { container } = renderInTheme(<MyAccountConnections />);
    await waitFor(() => expect(api.getMyUserAccessConnectors).toHaveBeenCalled());
    expect(container.querySelector('section')).toBeNull();
  });

  it('asks for a reconnect when the token was revoked, and says why', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([
      {
        connectorId: 'c-1',
        name: 'Team Notion',
        type: 'Notion',
        connected: true,
        connection: connection({ status: 'revoked', lastError: 'Notion no longer accepts this token.' }),
      },
    ]);
    renderInTheme(<MyAccountConnections />);
    expect(await screen.findByText('Needs reconnecting: Notion no longer accepts this token.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeTruthy();
  });

  it('shows the server reason inside the dialog, then the connected state once a token is accepted', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([
      { connectorId: 'c-1', name: 'Team Notion', type: 'Notion', connected: false, connection: null },
    ]);
    api.connectUserAccess
      .mockRejectedValueOnce({
        type: 'VALIDATION_ERROR',
        message: 'This token belongs to a different Notion workspace than this connector.',
        statusCode: 400,
      })
      .mockResolvedValueOnce(connection());
    renderInTheme(<MyAccountConnections />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(screen.getByLabelText('Personal access token'), { target: { value: ' ntn_abc ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));

    expect(
      await screen.findByText('This token belongs to a different Notion workspace than this connector.')
    ).toBeTruthy();
    expect(api.connectUserAccess).toHaveBeenCalledWith('c-1', 'ntn_abc');
    expect(dialog.isConnected).toBe(true);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await screen.findByText(/Connected as dana@example.com/)).toBeTruthy();
    expect(toastTitles()).toContain('Account connected');
  });
});

describe('MyAccountConnections sign-in with the source', () => {
  const signInConnector = {
    connectorId: 'c-1',
    name: 'Team Notion',
    type: 'Notion',
    connected: false,
    connection: null,
    signInAvailable: true,
  };

  afterEach(() => vi.restoreAllMocks());

  it('signs in through a popup and shows the connected state once the callback reports back', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([signInConnector]);
    api.startUserAccessSignIn.mockResolvedValue('https://api.notion.com/v1/oauth/authorize?state=s');
    api.getUserAccess.mockResolvedValue(connection({ method: 'oauth' }));
    const popup = { closed: false, close: vi.fn(), location: { href: '' } };
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    renderInTheme(<MyAccountConnections />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in with Notion' }));

    await waitFor(() => expect(popup.location.href).toBe('https://api.notion.com/v1/oauth/authorize?state=s'));
    // Not opened with noopener: the callback page reports back through window.opener.
    expect(String(open.mock.calls[0][2])).not.toContain('noopener');
    expect(api.startUserAccessSignIn).toHaveBeenCalledWith('c-1');

    window.dispatchEvent(
      new MessageEvent('message', { origin: window.location.origin, data: { type: 'CONNECTOR_OAUTH_SUCCESS' } })
    );
    expect(await screen.findByText(/Connected as dana@example.com/)).toBeTruthy();
    expect(api.getUserAccess).toHaveBeenCalledWith('c-1');
  });

  it('ignores a message from another origin and shows the reason a sign-in failed', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([signInConnector]);
    api.startUserAccessSignIn.mockResolvedValue('https://api.notion.com/v1/oauth/authorize?state=s');
    const popup = { closed: false, close: vi.fn(), location: { href: '' } };
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    renderInTheme(<MyAccountConnections />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in with Notion' }));
    await waitFor(() => expect(popup.location.href).not.toBe(''));

    window.dispatchEvent(
      new MessageEvent('message', { origin: 'https://evil.example', data: { type: 'CONNECTOR_OAUTH_SUCCESS' } })
    );
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: window.location.origin,
        data: { type: 'CONNECTOR_OAUTH_ERROR', error: 'This token belongs to bob@example.com.' },
      })
    );
    expect(await screen.findByText('This token belongs to bob@example.com.')).toBeTruthy();
    expect(api.getUserAccess).not.toHaveBeenCalled();
  });

  it('says so when the browser blocks the sign-in window', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([signInConnector]);
    vi.spyOn(window, 'open').mockReturnValue(null);
    renderInTheme(<MyAccountConnections />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in with Notion' }));
    expect(await screen.findByText(/blocked the sign-in window/)).toBeTruthy();
    expect(api.startUserAccessSignIn).not.toHaveBeenCalled();
  });

  it('offers only the token when the connector has no OAuth app', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([{ ...signInConnector, signInAvailable: false }]);
    renderInTheme(<MyAccountConnections />);
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('button', { name: 'Sign in with Notion' })).toBeNull();
    expect(within(dialog).getByLabelText('Personal access token')).toBeTruthy();
  });
});

const person = (over: Partial<UserAccessRoster['people'][number]>): UserAccessRoster['people'][number] => ({
  name: null,
  email: 'x@example.com',
  sourceUserId: null,
  userId: null,
  status: 'not_invited',
  connection: null,
  invite: null,
  ...over,
});

const roster = (over: Partial<UserAccessRoster> = {}): UserAccessRoster => ({
  membersUpdatedAt: Date.now() - 2 * 60 * 60 * 1000,
  people: [
    person({ name: 'Ada Lovelace', email: 'ada@example.com', userId: 'u-2', status: 'not_invited' }),
    person({
      email: 'bob@example.com',
      userId: 'u-3',
      status: 'invited',
      invite: { firstSentAt: 1, lastSentAt: Date.now() - 3 * 86400000, sendCount: 1, optedOut: false },
    }),
    person({ email: 'dana@example.com', userId: 'u-1', status: 'connected', connection: connection() }),
    person({ email: 'eve@example.com', sourceUserId: 'n-9', status: 'no_account' }),
    person({
      email: 'olga@example.com',
      userId: 'u-5',
      status: 'opted_out',
      invite: { firstSentAt: null, lastSentAt: null, sendCount: 0, optedOut: true },
    }),
  ],
  counts: { connected: 1, needs_reconnect: 0, invited: 1, not_invited: 1, opted_out: 1, no_account: 1 },
  ...over,
});

const rowOf = (text: string) => screen.getByText(text).closest('li') as HTMLElement;

describe('ConnectedUsers roster', () => {
  it('renders nothing for a connector that is not in per-user mode', async () => {
    api.getUserAccessRoster.mockRejectedValue({ type: 'NOT_FOUND', message: 'Connector not found', statusCode: 404 });
    const { container } = renderInTheme(<ConnectedUsers connectorId="c-1" />);
    await waitFor(() => expect(api.getUserAccessRoster).toHaveBeenCalledWith('c-1'));
    expect(container.querySelector('section')).toBeNull();
  });

  it('shows counts, statuses, labelled row actions and when the member list was read', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    renderInTheme(<ConnectedUsers connectorId="c-1" />);

    expect(
      await screen.findByText(
        'Connected: 1 · Invited: 1 · Not invited: 1 · Asked not to be reminded: 1 · No PipesHub account: 1'
      )
    ).toBeTruthy();
    expect(screen.getByText(/^Member list last read /)).toBeTruthy();
    expect(within(rowOf('Ada Lovelace')).getByText('Not invited')).toBeTruthy();
    expect(within(rowOf('Ada Lovelace')).getByRole('button', { name: 'Invite Ada Lovelace' })).toBeTruthy();
    expect(within(rowOf('bob@example.com')).getByRole('button', { name: 'Remind bob@example.com' })).toBeTruthy();
    expect(within(rowOf('bob@example.com')).getByText(/^Last sent /)).toBeTruthy();
    expect(within(rowOf('eve@example.com')).getByText('Invite them to PipesHub first')).toBeTruthy();
    expect(within(rowOf('eve@example.com')).queryByRole('button')).toBeNull();
    // Opted out before any invite: no action, the badge says why.
    expect(within(rowOf('olga@example.com')).getByText('Asked not to be reminded')).toBeTruthy();
    expect(within(rowOf('olga@example.com')).queryByRole('button')).toBeNull();
    expect(
      within(rowOf('dana@example.com')).getByRole('button', { name: 'Remove the connection of dana@example.com' })
    ).toBeTruthy();
    expect(within(rowOf('dana@example.com')).queryByRole('button', { name: /^Remind/ })).toBeNull();
  });

  it('labels a person with an invite record but nothing sent as Invite', async () => {
    api.getUserAccessRoster.mockResolvedValue(
      roster({
        people: [
          person({
            email: 'kim@example.com',
            userId: 'u-6',
            status: 'invited',
            invite: { firstSentAt: null, lastSentAt: null, sendCount: 0, optedOut: false },
          }),
        ],
      })
    );
    renderInTheme(<ConnectedUsers connectorId="c-1" />);
    expect(await screen.findByRole('button', { name: 'Invite kim@example.com' })).toBeTruthy();
  });

  it('queues an invite for everyone not connected and reloads the roster', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    api.inviteUserAccess.mockResolvedValue({ sent: 2, emailed: null, queued: true, skipped: [] });
    renderInTheme(<ConnectedUsers connectorId="c-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Invite 2 people' }));

    await waitFor(() => expect(api.inviteUserAccess).toHaveBeenCalledWith('c-1', { all: true }));
    await waitFor(() =>
      expect(toastTitles()).toContain("Sending to 2 people. They'll get a notification and an email.")
    );
    expect(toasts()[0].description).toBeUndefined();
    await waitFor(() => expect(api.getUserAccessRoster).toHaveBeenCalledTimes(2));
  });

  it('reminds one person and reports mail that could not be sent', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    api.inviteUserAccess.mockResolvedValue({ sent: 1, emailed: 0, queued: false, skipped: [] });
    renderInTheme(<ConnectedUsers connectorId="c-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Remind bob@example.com' }));

    await waitFor(() => expect(api.inviteUserAccess).toHaveBeenCalledWith('c-1', { userIds: ['u-3'] }));
    await waitFor(() => expect(toastTitles()).toContain('Sent to 1 person'));
    expect(toasts()[0].description).toBe(
      'Email could not be sent to 1 person; they still got an in-app notification.'
    );
  });

  it('explains why nothing was sent instead of "Sent to 0 people"', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    api.inviteUserAccess.mockResolvedValue({
      sent: 0,
      emailed: 0,
      queued: false,
      skipped: [{ userId: 'u-3', reason: 'recently_sent' }],
    });
    renderInTheme(<ConnectedUsers connectorId="c-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Remind bob@example.com' }));

    await waitFor(() => expect(toastTitles()).toContain('Nothing was sent'));
    expect(toasts()[0].variant).toBe('warning');
    expect(toasts()[0].description).toBe('Already sent something in the last day.');
    expect(toastTitles()).not.toContain('Sent to 0 people');
  });

  it('cannot invite when nobody is left, and says when the member list is not read yet', async () => {
    api.getUserAccessRoster.mockResolvedValue(
      roster({
        membersUpdatedAt: null,
        people: [person({ email: 'dana@example.com', userId: 'u-1', status: 'connected', connection: connection() })],
        counts: { connected: 1, needs_reconnect: 0, invited: 0, not_invited: 0, opted_out: 0, no_account: 0 },
      })
    );
    renderInTheme(<ConnectedUsers connectorId="c-1" />);

    const button = await screen.findByRole('button', { name: 'Invite 0 people' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('The member list appears after the next sync')).toBeTruthy();
  });

  it('removes a connection after confirming, then reloads the roster', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    api.removeUserAccessConnection.mockResolvedValue(undefined);
    renderInTheme(<ConnectedUsers connectorId="c-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Remove the connection of dana@example.com' }));
    const confirm = await screen.findByRole('alertdialog');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(api.removeUserAccessConnection).toHaveBeenCalledWith('c-1', 'u-1'));
    await waitFor(() => expect(api.getUserAccessRoster).toHaveBeenCalledTimes(2));
  });
});

describe('MyAccountConnections deep link, dialog and reminders', () => {
  const notion = {
    connectorId: 'c-1',
    name: 'Team Notion',
    type: 'Notion',
    connected: false,
    connection: null,
    signInAvailable: true,
  };

  afterEach(() => vi.restoreAllMocks());

  it('opens the connect dialog from ?connect= with Sign in focused, without opening the popup', async () => {
    nav.params = new URLSearchParams('connect=c-1&tab=all');
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    const open = vi.spyOn(window, 'open');
    renderInTheme(<MyAccountConnections />);

    const dialog = await screen.findByRole('dialog');
    const signIn = within(dialog).getByRole('button', { name: 'Sign in with Notion' });
    await waitFor(() => expect(document.activeElement).toBe(signIn));
    expect(open).not.toHaveBeenCalled();
    expect(nav.router.replace).toHaveBeenCalledWith('/workspace/connectors/personal/?tab=all');
  });

  it('opens the dialog again when the same notification is clicked again', async () => {
    nav.params = new URLSearchParams('connect=c-1');
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    const view = renderInTheme(<MyAccountConnections />);
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // router.replace dropped the param, then the notification put it back.
    nav.params = new URLSearchParams();
    view.rerender(<Theme><MyAccountConnections /></Theme>);
    nav.params = new URLSearchParams('connect=c-1');
    view.rerender(<Theme><MyAccountConnections /></Theme>);

    expect(await screen.findByRole('dialog')).toBeTruthy();
  });

  it('keeps the token form behind a disclosure when sign-in is available', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    renderInTheme(<MyAccountConnections />);
    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).queryByLabelText('Personal access token')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Use a personal access token instead' }));
    expect(within(dialog).getByLabelText('Personal access token')).toBeTruthy();
  });

  it("turns reminders off from the card right away, and back on", async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    api.setUserAccessReminders.mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    renderInTheme(<MyAccountConnections />);

    fireEvent.click(await screen.findByRole('button', { name: "Don't remind me" }));
    await waitFor(() => expect(api.setUserAccessReminders).toHaveBeenCalledWith('c-1', true));
    expect(toastTitles()).toContain('Reminders turned off');

    fireEvent.click(await screen.findByRole('button', { name: 'Remind me again' }));
    await waitFor(() => expect(api.setUserAccessReminders).toHaveBeenCalledWith('c-1', false));
  });

  it('reflects an earlier opt-out and hides the action once connected', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([
      { ...notion, optedOut: true },
      { ...notion, connectorId: 'c-2', name: 'Other Notion', connected: true, connection: connection() },
    ]);
    renderInTheme(<MyAccountConnections />);
    expect(await screen.findAllByRole('button', { name: 'Remind me again' })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: "Don't remind me" })).toBeNull();
  });
});

describe('ConnectAccountBanner', () => {
  const notion = { connectorId: 'c-1', name: 'Team Notion', type: 'Notion', connected: false, connection: null };
  const notConnected = "Notion results aren't included for you yet.";

  afterEach(() => vi.useRealTimers());

  it('asks to connect, links to the dialog, and stays dismissed for the session', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    const { unmount } = renderInTheme(<ConnectAccountBanner />);

    expect(await screen.findByText(notConnected)).toBeTruthy();
    // next/link drops the trailing slash here; the app's trailingSlash config keeps it.
    expect(screen.getByRole('link', { name: 'Connect Notion' }).getAttribute('href')).toMatch(
      /^\/workspace\/connectors\/personal\/?\?connect=c-1$/
    );
    expect(api.getMyUserAccessConnectors).toHaveBeenCalledWith({ silent: true });

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(notConnected)).toBeNull();
    unmount();

    renderInTheme(<ConnectAccountBanner />);
    await act(async () => {});
    expect(screen.queryByText(notConnected)).toBeNull();
    expect(api.getMyUserAccessConnectors).toHaveBeenCalledTimes(1);
  });

  it('fetches once for several mounts', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    renderInTheme(<ConnectAccountBanner />);
    renderInTheme(<ConnectAccountBanner />);
    await waitFor(() => expect(screen.getAllByText(notConnected)).toHaveLength(2));
    expect(api.getMyUserAccessConnectors).toHaveBeenCalledTimes(1);
  });

  it('re-reads the list on focus at most once a minute, and hides once connected elsewhere', async () => {
    const now = vi.spyOn(Date, 'now');
    now.mockReturnValue(1_000_000);
    api.getMyUserAccessConnectors.mockResolvedValueOnce([notion]);
    renderInTheme(<ConnectAccountBanner />);
    expect(await screen.findByText(notConnected)).toBeTruthy();

    now.mockReturnValue(1_000_000 + 30_000);
    act(() => void window.dispatchEvent(new Event('focus')));
    expect(api.getMyUserAccessConnectors).toHaveBeenCalledTimes(1);

    api.getMyUserAccessConnectors.mockResolvedValueOnce([{ ...notion, connected: true, connection: connection() }]);
    now.mockReturnValue(1_000_000 + 61_000);
    act(() => void window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(screen.queryByText(notConnected)).toBeNull());
    expect(api.getMyUserAccessConnectors).toHaveBeenCalledTimes(2);
    now.mockRestore();
  });

  it("stops reminders for good from the banner", async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    api.setUserAccessReminders.mockResolvedValue(true);
    renderInTheme(<ConnectAccountBanner />);

    fireEvent.click(await screen.findByRole('button', { name: "Don't remind me" }));
    expect(screen.queryByText(notConnected)).toBeNull();
    expect(api.setUserAccessReminders).toHaveBeenCalledWith('c-1', true);
  });

  it('asks to reconnect a revoked connection', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([
      { ...notion, connected: true, connection: connection({ status: 'revoked' }) },
    ]);
    renderInTheme(<ConnectAccountBanner />);
    expect(await screen.findByText('Your Notion connection stopped working.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Reconnect' })).toBeTruthy();
  });

  it('stays hidden when connected, opted out, or the list fails', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([
      { ...notion, connected: true, connection: connection() },
      { ...notion, connectorId: 'c-2', optedOut: true },
    ]);
    const first = renderInTheme(<ConnectAccountBanner />);
    await act(async () => {});
    expect(first.container.querySelector('[role="status"]')).toBeNull();

    cleanup();
    forgetMyUserAccessConnectors();
    api.getMyUserAccessConnectors.mockRejectedValue(new Error('offline'));
    const second = renderInTheme(<ConnectAccountBanner />);
    await act(async () => {});
    expect(second.container.querySelector('[role="status"]')).toBeNull();
  });
});

describe('connect-account landing page', () => {
  const originalLocation = window.location;
  const assign = vi.fn();
  const at = (search: string) =>
    Object.defineProperty(window, 'location', {
      value: {
        search,
        assign,
        origin: 'http://localhost',
        pathname: '/connect-account/',
        href: `http://localhost/connect-account/${search}`,
      },
      configurable: true,
    });
  const info = {
    connectorName: 'Team Notion',
    sourceType: 'Notion',
    email: 'ada@example.com',
    connected: false,
    signInAvailable: true,
    expiresAt: 0,
  };

  afterEach(() => {
    Object.defineProperty(window, 'location', { value: originalLocation, configurable: true });
    assign.mockReset();
    sessionStorage.clear();
    vi.mocked(window.history.replaceState).mockRestore?.();
  });

  it('takes the token out of the address bar but keeps it for a reload in this tab', async () => {
    const replaceState = vi.spyOn(window.history, 'replaceState').mockImplementation(() => undefined);
    at('?token=c-1.abc&optOut=1');
    api.getUserAccessLinkInfo.mockResolvedValue(info);
    const first = renderInTheme(<ConnectAccountClient />);
    await screen.findByText('Stop reminders for Team Notion?');
    expect(replaceState.mock.calls.map((c) => c[2])).toEqual(['/connect-account/?optOut=1']);
    first.unmount();

    at('');
    renderInTheme(<ConnectAccountClient />);
    expect(await screen.findByText('Connect your Notion account')).toBeTruthy();
    expect(api.getUserAccessLinkInfo).toHaveBeenLastCalledWith('c-1.abc');
  });

  it('shows who the link is for and continues to the source in this tab', async () => {
    at('?token=c-1.abc');
    api.getUserAccessLinkInfo.mockResolvedValue(info);
    api.authorizeUserAccessLink.mockResolvedValue('https://api.notion.com/v1/oauth/authorize?state=s');
    renderInTheme(<ConnectAccountClient />);

    expect(await screen.findByText('Connect your Notion account')).toBeTruthy();
    expect(screen.getByText('for ada@example.com')).toBeTruthy();
    expect(screen.getByText(/lists only pages you have full access to/)).toBeTruthy();
    expect(api.getUserAccessLinkInfo).toHaveBeenCalledWith('c-1.abc');

    fireEvent.click(screen.getByRole('button', { name: 'Continue to Notion' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://api.notion.com/v1/oauth/authorize?state=s'));
    expect(api.authorizeUserAccessLink).toHaveBeenCalledWith('c-1.abc');
  });

  it('stops the spinner when Back restores the page from the back/forward cache', async () => {
    at('?token=c-1.abc');
    api.getUserAccessLinkInfo.mockResolvedValue(info);
    api.authorizeUserAccessLink.mockResolvedValue('https://api.notion.com/v1/oauth/authorize?state=s');
    renderInTheme(<ConnectAccountClient />);

    const button = await screen.findByRole('button', { name: 'Continue to Notion' });
    fireEvent.click(button);
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect((button as HTMLButtonElement).disabled).toBe(true);

    const restored = new Event('pageshow') as PageTransitionEvent;
    Object.defineProperty(restored, 'persisted', { value: true });
    act(() => void window.dispatchEvent(restored));
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  it('sends a token-only connector to Your connectors with its dialog', async () => {
    at('?token=c-1.abc');
    api.getUserAccessLinkInfo.mockResolvedValue({ ...info, signInAvailable: false });
    renderInTheme(<ConnectAccountClient />);
    expect(await screen.findByText(/uses personal access tokens/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Continue to Notion' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Open PipesHub' }).getAttribute('href')).toMatch(
      /^\/workspace\/connectors\/personal\/?\?connect=c-1$/
    );
  });

  it('says when the account is already connected', async () => {
    at('?token=c-1.abc');
    api.getUserAccessLinkInfo.mockResolvedValue({ ...info, connected: true });
    renderInTheme(<ConnectAccountClient />);
    expect(await screen.findByText('Your Notion account is already connected.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open PipesHub' }).getAttribute('href')).toBe('/');
    expect(screen.queryByRole('button', { name: 'Continue to Notion' })).toBeNull();
  });

  it('explains an expired or used link', async () => {
    at('?token=c-1.old');
    api.getUserAccessLinkInfo.mockRejectedValue({ response: { status: 404 } });
    renderInTheme(<ConnectAccountClient />);
    expect(await screen.findByText(/This link has expired or was already used/)).toBeTruthy();
  });

  it('asks to wait after too many attempts', async () => {
    at('?token=c-1.abc');
    api.getUserAccessLinkInfo.mockRejectedValue({ response: { status: 429 } });
    renderInTheme(<ConnectAccountClient />);
    expect(await screen.findByText('Too many attempts, try again in a minute.')).toBeTruthy();
  });

  it('asks before turning reminders off with &optOut=1 (mail scanners open links)', async () => {
    at('?token=c-1.abc&optOut=1');
    api.getUserAccessLinkInfo.mockResolvedValue(info);
    api.optOutUserAccessLink.mockResolvedValue(undefined);
    renderInTheme(<ConnectAccountClient />);

    expect(await screen.findByText('Stop reminders for Team Notion?')).toBeTruthy();
    expect(api.optOutUserAccessLink).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Stop reminders' }));
    expect(await screen.findByText("You won't get more reminders for this connector.")).toBeTruthy();
    expect(api.optOutUserAccessLink).toHaveBeenCalledWith('c-1.abc');
  });
});

describe('connector OAuth callback from an emailed link', () => {
  const linkState = btoa(JSON.stringify({ state: 's', connector_id: 'c-1', userAccess: true, linkUser: 'u-1' }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  const at = (query: string) => window.history.replaceState(null, '', `/connectors/oauth/callback/notion?${query}`);

  it('completes through the public route and shows the result in the page', async () => {
    at(`code=abc&state=${linkState}`);
    api.completeUserAccessLink.mockResolvedValue({ success: true, connectorName: 'Notion' });
    renderInTheme(<ConnectorOAuthCallbackClient />);

    expect(
      await screen.findByText('Notion is connected. Your pages will appear in PipesHub search in a few minutes.')
    ).toBeTruthy();
    expect(api.completeUserAccessLink).toHaveBeenCalledWith('abc', linkState);
    expect(api.completeConnectorOAuthCallback).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'Open PipesHub' }).getAttribute('href')).toBe('/');
  });

  it('shows the reason and points back to the email when it fails', async () => {
    at(`code=abc&state=${linkState}`);
    api.completeUserAccessLink.mockResolvedValue({ success: false, error_message: 'This sign-in link expired.' });
    renderInTheme(<ConnectorOAuthCallbackClient />);

    expect(await screen.findByText('This sign-in link expired.')).toBeTruthy();
    expect(screen.getByText('Use the link in your email to try again.')).toBeTruthy();
    expect(api.completeConnectorOAuthCallback).not.toHaveBeenCalled();
  });

  it('says the user cancelled instead of showing the provider error code', async () => {
    at(`error=access_denied&state=${linkState}`);
    renderInTheme(<ConnectorOAuthCallbackClient />);

    expect(await screen.findByText('You cancelled the sign-in.')).toBeTruthy();
    expect(screen.queryByText('access_denied')).toBeNull();
    expect(api.completeUserAccessLink).not.toHaveBeenCalled();
  });

  it('gives a translated message when no code came back', async () => {
    at(`state=${linkState}`);
    renderInTheme(<ConnectorOAuthCallbackClient />);
    expect(await screen.findByText('The sign-in did not finish. Try again.')).toBeTruthy();
  });
});
