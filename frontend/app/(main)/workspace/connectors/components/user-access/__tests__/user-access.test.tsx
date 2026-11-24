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

import { ConnectAccountAction } from '../connect-account';
import { ConnectedUsers } from '../connected-users';
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
  sessionStorage.clear();
});
afterEach(() => cleanup());

const notion = {
  connectorId: 'c-1',
  name: 'Team Notion',
  type: 'Notion',
  connected: false,
  connection: null,
  signInAvailable: true,
};

describe('ConnectAccountAction (the Connect button on a notification)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders nothing when the connector is gone or no longer per-user', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([]);
    const { container } = renderInTheme(<ConnectAccountAction connectorId="c-1" />);
    await waitFor(() => expect(api.getMyUserAccessConnectors).toHaveBeenCalledWith({ silent: true }));
    expect(container.querySelector('button')).toBeNull();
  });

  it('opens the sign-in window straight from the button and shows Connected when it reports back', async () => {
    api.getMyUserAccessConnectors
      .mockResolvedValueOnce([notion])
      .mockResolvedValue([{ ...notion, connected: true, connection: connection({ method: 'oauth' }) }]);
    api.startUserAccessSignIn.mockResolvedValue('https://api.notion.com/v1/oauth/authorize?state=s');
    const popup = { closed: false, close: vi.fn(), location: { href: '' } };
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    const onConnected = vi.fn();
    renderInTheme(<ConnectAccountAction connectorId="c-1" onConnected={onConnected} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));

    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(popup.location.href).toBe('https://api.notion.com/v1/oauth/authorize?state=s'));
    // Not opened with noopener: the callback page reports back through window.opener.
    expect(String(open.mock.calls[0][2])).not.toContain('noopener');

    // Another origin, or another connector's popup, is not this sign-in.
    window.dispatchEvent(
      new MessageEvent('message', { origin: 'https://evil.example', data: { type: 'CONNECTOR_OAUTH_SUCCESS' } })
    );
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: window.location.origin,
        data: { type: 'CONNECTOR_OAUTH_SUCCESS', connectorId: 'c-9' },
      })
    );
    expect(api.getMyUserAccessConnectors).toHaveBeenCalledTimes(1);

    window.dispatchEvent(
      new MessageEvent('message', {
        origin: window.location.origin,
        data: { type: 'CONNECTOR_OAUTH_SUCCESS', connectorId: 'c-1' },
      })
    );
    expect(await screen.findByText('Connected as dana@example.com')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    expect(onConnected).toHaveBeenCalledTimes(1);
  });

  it('shows why a sign-in failed, and says so when the browser blocks the window', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([notion]);
    api.startUserAccessSignIn.mockResolvedValue('https://api.notion.com/v1/oauth/authorize?state=s');
    const popup = { closed: false, close: vi.fn(), location: { href: '' } };
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    renderInTheme(<ConnectAccountAction connectorId="c-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(popup.location.href).not.toBe(''));
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: window.location.origin,
        data: { type: 'CONNECTOR_OAUTH_ERROR', error: 'Access denied' },
      })
    );
    expect((await screen.findByRole('alert')).textContent).toBe('Access denied');

    open.mockReturnValue(null);
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/blocked the sign-in window/));
  });

  it('asks for a token in a dialog when the connector has no sign-in, and shows the server reason', async () => {
    const tokenOnly = { ...notion, signInAvailable: false };
    api.getMyUserAccessConnectors
      .mockResolvedValueOnce([tokenOnly])
      .mockResolvedValue([{ ...tokenOnly, connected: true, connection: connection() }]);
    api.connectUserAccess
      .mockRejectedValueOnce({
        type: 'VALIDATION_ERROR',
        message: 'This token belongs to another workspace.',
        statusCode: 400,
      })
      .mockResolvedValue(connection());
    const open = vi.spyOn(window, 'open');
    renderInTheme(<ConnectAccountAction connectorId="c-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
    const dialog = await screen.findByRole('dialog');
    expect(open).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText('Personal access token'), { target: { value: ' secret ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await within(dialog).findByText('This token belongs to another workspace.')).toBeTruthy();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await screen.findByText('Connected as dana@example.com')).toBeTruthy();
    expect(api.connectUserAccess).toHaveBeenLastCalledWith('c-1', 'secret');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('turns every button for that connector into Connected (one per reminder)', async () => {
    const tokenOnly = { ...notion, signInAvailable: false };
    api.getMyUserAccessConnectors
      .mockResolvedValueOnce([tokenOnly])
      .mockResolvedValueOnce([tokenOnly])
      .mockResolvedValue([{ ...tokenOnly, connected: true, connection: connection() }]);
    api.connectUserAccess.mockResolvedValue(connection());
    renderInTheme(
      <>
        <ConnectAccountAction connectorId="c-1" />
        <ConnectAccountAction connectorId="c-1" />
      </>
    );

    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Connect' })).toHaveLength(2));
    fireEvent.click(screen.getAllByRole('button', { name: 'Connect' })[0]);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Personal access token'), { target: { value: 'secret' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));

    await waitFor(() => expect(screen.getAllByText('Connected as dana@example.com')).toHaveLength(2));
  });

  it('offers Reconnect when the connection stopped working', async () => {
    api.getMyUserAccessConnectors.mockResolvedValue([
      { ...notion, connected: true, connection: connection({ status: 'revoked', lastError: 'Token revoked' }) },
    ]);
    renderInTheme(<ConnectAccountAction connectorId="c-1" />);
    expect(await screen.findByRole('button', { name: 'Reconnect' })).toBeTruthy();
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
  total: 5,
  ...over,
});

const rowOf = (text: string) => screen.getByText(text).closest('li') as HTMLElement;

/** Render the admin view and open the people list. */
const openPeople = async () => {
  renderInTheme(<ConnectedUsers connectorId="c-1" />);
  fireEvent.focus(await screen.findByRole('combobox', { name: 'Search people by name or email' }));
  await screen.findByText('Ada Lovelace');
};

describe('ConnectedUsers (admin)', () => {
  beforeEach(() => api.getMyUserAccessConnectors.mockResolvedValue([notion]));

  it('renders nothing for a connector that is not in per-user mode', async () => {
    api.getUserAccessRoster.mockRejectedValue({ type: 'NOT_FOUND', message: 'Connector not found', statusCode: 404 });
    const { container } = renderInTheme(<ConnectedUsers connectorId="c-1" />);
    await waitFor(() => expect(api.getUserAccessRoster).toHaveBeenCalledWith('c-1', { limit: 1 }));
    expect(container.querySelector('section')).toBeNull();
  });

  it('shows a summary and the admin own Connect, with no list until the search box is used', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster({ counts: { ...roster().counts, needs_reconnect: 2 } }));
    renderInTheme(<ConnectedUsers connectorId="c-1" />);

    expect(await screen.findByText('1 of 7 people connected · 2 need to reconnect')).toBeTruthy();
    expect(screen.getByText(/^Member list last read /)).toBeTruthy();
    expect(screen.getByText('Your account')).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(screen.queryByText('Ada Lovelace')).toBeNull();
    expect(api.getUserAccessRoster).toHaveBeenCalledTimes(1);
  });

  it('opens a paged list with statuses, labelled actions and Invite all at the top', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    await openPeople();

    expect(api.getUserAccessRoster).toHaveBeenLastCalledWith('c-1', { q: undefined, offset: 0, limit: 20 });
    expect(screen.getByRole('button', { name: 'Invite all (2)' })).toBeTruthy();
    expect(within(rowOf('Ada Lovelace')).getByText('Not invited')).toBeTruthy();
    expect(within(rowOf('Ada Lovelace')).getByRole('button', { name: 'Invite Ada Lovelace' })).toBeTruthy();
    expect(within(rowOf('bob@example.com')).getByRole('button', { name: 'Remind bob@example.com' })).toBeTruthy();
    expect(within(rowOf('bob@example.com')).getByText(/Last sent /)).toBeTruthy();
    expect(within(rowOf('eve@example.com')).getByText(/Invite them to PipesHub first/)).toBeTruthy();
    expect(within(rowOf('eve@example.com')).queryByRole('button')).toBeNull();
    expect(within(rowOf('olga@example.com')).queryByRole('button')).toBeNull();
    expect(
      within(rowOf('dana@example.com')).getByRole('button', { name: 'Remove the connection of dana@example.com' })
    ).toBeTruthy();
  });

  it('searches on the server, hides Invite all while searching, and says when no one matches', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    await openPeople();
    api.getUserAccessRoster.mockResolvedValue(roster({ people: [], total: 0 }));

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zed' } });

    expect(await screen.findByText('No one matches “zed”')).toBeTruthy();
    expect(api.getUserAccessRoster).toHaveBeenLastCalledWith('c-1', { q: 'zed', offset: 0, limit: 20 });
    expect(screen.queryByRole('button', { name: /^Invite all/ })).toBeNull();
  });

  it('loads the next page when the list is scrolled to the end', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster({ total: 45 }));
    await openPeople();
    const list = screen.getByRole('list').parentElement as HTMLElement;
    Object.defineProperties(list, {
      scrollHeight: { value: 600, configurable: true },
      clientHeight: { value: 320, configurable: true },
      scrollTop: { value: 280, configurable: true },
    });
    fireEvent.scroll(list);
    await waitFor(() =>
      expect(api.getUserAccessRoster).toHaveBeenLastCalledWith('c-1', { q: undefined, offset: 20, limit: 20 })
    );
  });

  it('queues Invite all and reads the roster again', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    api.inviteUserAccess.mockResolvedValue({ sent: 2, emailed: null, queued: true, skipped: [] });
    await openPeople();
    const calls = api.getUserAccessRoster.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: 'Invite all (2)' }));

    await waitFor(() => expect(api.inviteUserAccess).toHaveBeenCalledWith('c-1', { all: true }));
    await waitFor(() =>
      expect(toastTitles()).toContain("Sending to 2 people. They'll get a notification and an email.")
    );
    await waitFor(() => expect(api.getUserAccessRoster.mock.calls.length).toBeGreaterThan(calls));
  });

  it('reminds one person and reports mail that could not be sent', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    api.inviteUserAccess.mockResolvedValue({ sent: 1, emailed: 0, queued: false, skipped: [] });
    await openPeople();

    fireEvent.click(screen.getByRole('button', { name: 'Remind bob@example.com' }));

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
    await openPeople();

    fireEvent.click(screen.getByRole('button', { name: 'Remind bob@example.com' }));

    await waitFor(() => expect(toastTitles()).toContain('Nothing was sent'));
    expect(toasts()[0].variant).toBe('warning');
    expect(toasts()[0].description).toBe('Already sent something in the last day.');
  });

  it('cannot invite all when nobody is left, and says when the member list is not read yet', async () => {
    api.getUserAccessRoster.mockResolvedValue(
      roster({
        membersUpdatedAt: null,
        counts: { connected: 1, needs_reconnect: 0, invited: 0, not_invited: 0, opted_out: 0, no_account: 0 },
      })
    );
    await openPeople();

    expect((screen.getByRole('button', { name: 'Invite all (0)' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('The member list appears after the next sync')).toBeTruthy();
  });

  it('removes a connection after confirming, keeping the list open, then reads it again', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    api.removeUserAccessConnection.mockResolvedValue(undefined);
    await openPeople();
    const calls = api.getUserAccessRoster.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: 'Remove the connection of dana@example.com' }));
    const confirm = await screen.findByRole('alertdialog');
    fireEvent.mouseDown(confirm);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(api.removeUserAccessConnection).toHaveBeenCalledWith('c-1', 'u-1'));
    await waitFor(() => expect(api.getUserAccessRoster.mock.calls.length).toBeGreaterThan(calls));
    expect(screen.getByText('Ada Lovelace')).toBeTruthy();
  });

  it('closes the list on Escape and on a click outside', async () => {
    api.getUserAccessRoster.mockResolvedValue(roster());
    await openPeople();
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Ada Lovelace')).toBeNull());

    fireEvent.focus(screen.getByRole('combobox'));
    await screen.findByText('Ada Lovelace');
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByText('Ada Lovelace')).toBeNull());
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
    vi.restoreAllMocks();
  });

  it('takes the token out of the address bar but keeps it for a reload in this tab', async () => {
    let entryState: unknown = null;
    const replaceState = vi.spyOn(window.history, 'replaceState').mockImplementation((data) => {
      entryState = data;
    });
    at('?token=c-1.abc&optOut=1');
    api.getUserAccessLinkInfo.mockResolvedValue(info);
    const first = renderInTheme(<ConnectAccountClient />);
    await screen.findByText('Stop reminders for Team Notion?');
    expect(replaceState.mock.calls.map((c) => c[2])).toEqual(['/connect-account/?optOut=1']);
    first.unmount();

    // A reload of that history entry: same state, no token in the URL.
    vi.spyOn(window.history, 'state', 'get').mockReturnValue(entryState);
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

  it('sends a token-only connector to the app, where the notification has the Connect button', async () => {
    at('?token=c-1.abc');
    api.getUserAccessLinkInfo.mockResolvedValue({ ...info, signInAvailable: false });
    renderInTheme(<ConnectAccountClient />);
    expect(await screen.findByText(/select Connect on the notification/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Continue to Notion' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Open PipesHub' }).getAttribute('href')).toBe('/');
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
