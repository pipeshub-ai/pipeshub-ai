import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({
  getNotionPersonalToken: vi.fn(),
  linkNotionPersonalToken: vi.fn(),
  unlinkNotionPersonalToken: vi.fn(),
  listNotionPersonalTokens: vi.fn(),
  getActiveConnectors: vi.fn(),
}));
vi.mock('../../../api', () => ({ ConnectorsApi: api }));
vi.mock('@/lib/hooks/use-user-directory-entry', () => ({
  useUserDirectoryEntry: (id: string) => (id === 'u-ada' ? { userId: 'u-ada', name: 'Ada Lovelace' } : null),
}));

import { NotionPersonalToken, NotionTeamAccess } from '../notion-personal-token';
import { installDomShims, renderInTheme, signInAs, signOut } from '../../../__tests__/fixtures';

const fresh = (ui: React.ReactElement) =>
  renderInTheme(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);
const renderPanel = () => fresh(<NotionPersonalToken connectorId="notion-1" />);
const tokenField = () => screen.getByLabelText('Notion personal access token') as HTMLInputElement;

beforeEach(() => {
  installDomShims();
  signInAs('member');
  Object.values(api).forEach((f) => f.mockReset());
  api.getNotionPersonalToken.mockResolvedValue({ status: 'NOT_LINKED' });
  api.listNotionPersonalTokens.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  signOut();
});

describe('NotionPersonalToken: a member', () => {
  it("explains why, links Notion's guide and hides the token as it is typed", async () => {
    renderPanel();
    await screen.findByText('Not linked. Link your token to see the Notion pages you can open.');
    expect(screen.getByRole('heading', { name: 'Your Notion access' })).toBeTruthy();
    expect(screen.getByText(/Notion does not say who may open a page/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'How to create a personal access token' }).getAttribute('href')).toBe(
      'https://developers.notion.com/guides/connections-and-tokens/personal-access-tokens'
    );
    expect(tokenField().type).toBe('password');
  });

  it('links the pasted token without surrounding whitespace, then shows it linked and clears the field', async () => {
    api.linkNotionPersonalToken.mockResolvedValue({ status: 'LINKED' });
    renderPanel();
    await screen.findByText(/Not linked/);
    const link = screen.getByRole('button', { name: 'Link' }) as HTMLButtonElement;
    expect(link.disabled).toBe(true);

    fireEvent.change(tokenField(), { target: { value: '  ntn_abc123\n' } });
    fireEvent.click(link);

    await screen.findByText('Linked.');
    expect(api.linkNotionPersonalToken).toHaveBeenCalledWith('notion-1', 'ntn_abc123');
    expect(tokenField().value).toBe('');
    expect(screen.getByRole('button', { name: 'Replace' })).toBeTruthy();
  });

  it('keeps the typed token and the old status when the link fails', async () => {
    api.linkNotionPersonalToken.mockRejectedValue(new Error('refused'));
    renderPanel();
    await screen.findByText(/Not linked/);

    fireEvent.change(tokenField(), { target: { value: 'ntn_abc123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link' }));

    await waitFor(() => expect((screen.getByRole('button', { name: 'Link' }) as HTMLButtonElement).disabled).toBe(false));
    expect(tokenField().value).toBe('ntn_abc123');
    expect(screen.getByText(/Not linked/)).toBeTruthy();
  });

  it('removes a linked token', async () => {
    api.getNotionPersonalToken.mockResolvedValue({ status: 'LINKED', linkedAt: Date.UTC(2026, 10, 1) });
    api.unlinkNotionPersonalToken.mockResolvedValue({ status: 'NOT_LINKED' });
    renderPanel();
    await screen.findByText(/^Linked on /);

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));

    await screen.findByText(/Not linked/);
    expect(api.unlinkNotionPersonalToken).toHaveBeenCalledWith('notion-1');
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
  });

  it('says a token stopped working, and why', async () => {
    api.getNotionPersonalToken.mockResolvedValue({ status: 'FAILED', reason: 'Notion no longer accepts the token' });
    renderPanel();
    expect(
      (await screen.findByTestId('notion-token-status')).textContent
    ).toBe('Stopped working: Notion no longer accepts the token. Link a new token.');
    expect(screen.getByRole('button', { name: 'Link' })).toBeTruthy();
  });

  it('does not ask for the list of who linked a token', async () => {
    renderPanel();
    await screen.findByText(/Not linked/);
    expect(api.listNotionPersonalTokens).not.toHaveBeenCalled();
    expect(screen.queryByText('Who linked a token')).toBeNull();
  });

  it('says it could not load, and the retry loads it', async () => {
    api.getNotionPersonalToken.mockRejectedValueOnce(new Error('boom'));
    renderPanel();
    expect((await screen.findByRole('alert')).textContent).toContain('We could not load your Notion access.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByText(/Not linked/);
  });
});

describe('NotionPersonalToken: an admin', () => {
  beforeEach(() => signInAs('admin'));

  it('lists who linked a token by name, falling back to the id, with each state', async () => {
    api.listNotionPersonalTokens.mockResolvedValue([
      { userId: 'u-ada', status: 'LINKED' },
      { userId: 'u-unknown', status: 'FAILED', reason: 'The token belongs to another Notion workspace' },
    ]);
    renderPanel();

    await screen.findByText('Ada Lovelace');
    expect(screen.getByText('u-unknown')).toBeTruthy();
    expect(screen.getByText('Stopped working: The token belongs to another Notion workspace. Link a new token.')).toBeTruthy();
    expect(api.listNotionPersonalTokens).toHaveBeenCalledWith('notion-1');
  });

  it('says nobody linked a token yet', async () => {
    renderPanel();
    await screen.findByText('Nobody has linked a token yet.');
  });
});

describe('NotionTeamAccess', () => {
  it('shows a panel for each Notion team connector only', async () => {
    api.getActiveConnectors.mockResolvedValue({
      success: true,
      connectors: [
        { _key: 'notion-1', name: 'Company wiki', type: 'Notion', scope: 'team' },
        { _key: 'jira-1', name: 'Jira', type: 'Jira', scope: 'team' },
      ],
    });
    fresh(<NotionTeamAccess />);

    await screen.findByRole('heading', { name: 'Your Notion access: Company wiki' });
    expect(screen.getAllByTestId('notion-personal-token')).toHaveLength(1);
    expect(api.getActiveConnectors).toHaveBeenCalledWith('team', 1, 100, { suppressErrorToast: true });
    expect(api.getNotionPersonalToken).toHaveBeenCalledWith('notion-1');
  });

  it('renders nothing when there is no Notion team connector', async () => {
    api.getActiveConnectors.mockResolvedValue({ success: true, connectors: [] });
    const { container } = fresh(<NotionTeamAccess />);
    await waitFor(() => expect(api.getActiveConnectors).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="notion-personal-token"]')).toBeNull();
  });
});
