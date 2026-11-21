import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry, McpServerTemplate } from '../types';

const mcpApi = vi.hoisted(() => ({ getCatalog: vi.fn() }));
vi.mock('../api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { McpServersLayout } from '../components/mcp-servers-layout';

function template(overrides: Partial<McpServerTemplate>): McpServerTemplate {
  return {
    typeId: 'x',
    displayName: 'X',
    description: 'Does x.',
    transport: 'streamable_http',
    defaultAuthMode: 'oauth',
    supportedAuthModes: ['oauth'],
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    defaultScopes: [],
    supportsDcr: true,
    tags: [],
    ...overrides,
  };
}

const CALENDAR = template({
  typeId: 'google_calendar',
  displayName: 'Google Calendar',
  description: 'Find availability and manage events.',
  category: 'meetings',
  capabilities: ['Schedule meetings', 'Find free time'],
  documentationUrl: 'https://developers.google.com/calendar',
});
const ZOOM = template({ typeId: 'zoom', displayName: 'Zoom', description: 'Meetings.', category: 'meetings', capabilities: ['Schedule a Zoom meeting', 'Find recordings'] });
const GITHUB = template({ typeId: 'github', displayName: 'GitHub', description: 'Code.', category: 'developer', capabilities: ['Review pull requests', 'Search code'] });
const EXA = template({
  typeId: 'exa',
  displayName: 'Exa',
  description: 'Web search.',
  category: 'search',
  capabilities: ['Search the web', 'Read a web page'],
  defaultAuthMode: 'api_token',
  readOnly: true,
});
const CATALOG = [CALENDAR, ZOOM, GITHUB, EXA];

function server(overrides: Partial<McpMyServerEntry>): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'GitHub',
    typeId: 'github',
    transport: 'streamable_http',
    authMode: 'oauth',
    useAdminAuth: false,
    url: 'https://api.githubcopilot.com/mcp/',
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: [],
    isCustom: false,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [],
    ...overrides,
  };
}

function renderLayout({ instances = [] as McpMyServerEntry[], catalog = CATALOG, isAdmin = true } = {}) {
  const onAdd = vi.fn();
  const onSearchChange = vi.fn();
  renderInTheme(
    <McpServersLayout
      title="MCP servers"
      subtitle="Servers"
      actions={null}
      isLoading={false}
      searchQuery=""
      onSearchChange={onSearchChange}
      instances={instances}
      templates={catalog}
      catalog={catalog}
      catalogDescription="Ready-made servers."
      emptyTitle="No servers"
      emptyDescription="Add one."
      isAdmin={isAdmin}
      busyInstanceId={null}
      onOpen={vi.fn()}
      onConnect={vi.fn()}
      onReconnect={vi.fn()}
      onAdd={onAdd}
    />
  );
  return { onAdd, onSearchChange };
}

const discover = () => within(screen.getByTestId('mcp-discover'));
const cardNames = () =>
  screen.queryAllByTestId('mcp-catalog-card').map((card) => within(card).getByRole('link').textContent);
const search = () => screen.getByRole('textbox', { name: 'Search ready-made servers' });

beforeEach(() => {
  installBrowserShims();
  mcpApi.getCatalog.mockReset();
});
afterEach(() => cleanup());

describe('Discover servers', () => {
  it('lists every server it may add, not added first, with category chips that narrow it', () => {
    renderLayout({ instances: [server({})] });

    expect(cardNames()).toEqual(['Exa', 'Google Calendar', 'Zoom', 'GitHub']);
    const chips = discover().getByRole('group', { name: 'Categories' });
    expect(within(chips).getByRole('button', { name: /All\s*4/ }).getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(within(chips).getByRole('button', { name: /Meetings & calendar\s*2/ }));
    expect(cardNames()).toEqual(['Google Calendar', 'Zoom']);
    expect(within(chips).queryByRole('button', { name: /Sales & CRM/ })).toBeNull();

    fireEvent.click(within(chips).getByRole('button', { name: /All/ }));
    expect(cardNames()).toHaveLength(4);
    expect(mcpApi.getCatalog).not.toHaveBeenCalled();
  });

  it('asks the server to rank a search once typing pauses, and says which job matched', async () => {
    mcpApi.getCatalog.mockResolvedValue({
      templates: [
        { ...CALENDAR, matchedCapability: 'Schedule meetings' },
        { ...ZOOM, matchedCapability: 'Schedule a Zoom meeting' },
        template({ typeId: 'not_offered', displayName: 'Hidden' }),
      ],
      total: 3,
    });
    renderLayout();

    fireEvent.change(search(), { target: { value: 'sch' } });
    fireEvent.change(search(), { target: { value: 'schedule meetings' } });

    await waitFor(() => expect(cardNames()).toEqual(['Google Calendar', 'Zoom']));
    expect(mcpApi.getCatalog).toHaveBeenCalledTimes(1);
    expect(mcpApi.getCatalog).toHaveBeenCalledWith({ search: 'schedule meetings', limit: 200 });
    expect(screen.getAllByTestId('mcp-catalog-match').map((m) => m.textContent)).toEqual([
      'Schedule meetings',
      'Schedule a Zoom meeting',
    ]);
    expect(discover().getByText('2 servers for “schedule meetings”')).toBeTruthy();
  });

  it('never shows an older search’s answer for a newer one', async () => {
    let answerFirst: (value: unknown) => void = () => {};
    mcpApi.getCatalog
      .mockImplementationOnce(() => new Promise((resolve) => (answerFirst = resolve)))
      .mockResolvedValueOnce({ templates: [GITHUB], total: 1 });
    renderLayout();

    fireEvent.change(search(), { target: { value: 'meetings' } });
    await waitFor(() => expect(mcpApi.getCatalog).toHaveBeenCalledTimes(1));
    fireEvent.change(search(), { target: { value: 'pull requests' } });
    await waitFor(() => expect(cardNames()).toEqual(['GitHub']));

    answerFirst({ templates: [CALENDAR], total: 1 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(cardNames()).toEqual(['GitHub']);
  });

  it('falls back to names and descriptions when the search fails', async () => {
    mcpApi.getCatalog.mockRejectedValue(new Error('down'));
    renderLayout();

    fireEvent.change(search(), { target: { value: 'zoom' } });

    await waitFor(() => expect(cardNames()).toEqual(['Zoom']));
  });

  it('says when nothing matches', async () => {
    mcpApi.getCatalog.mockResolvedValue({ templates: [], total: 0 });
    renderLayout();

    fireEvent.change(search(), { target: { value: 'xylophone' } });

    expect(await discover().findByText(/No ready-made server matches “xylophone”/)).toBeTruthy();
  });

  it('adds straight from a card without opening its details', () => {
    const { onAdd } = renderLayout();

    const card = screen.getAllByTestId('mcp-catalog-card').find((c) => within(c).queryByText('GitHub'))!;
    fireEvent.click(within(card).getByRole('button', { name: /Set up/ }));

    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ typeId: 'github' }));
    expect(screen.queryByTestId('mcp-catalog-details')).toBeNull();
  });

  it('only filters your servers with the header search, and hides it when you have none', () => {
    renderLayout();
    expect(screen.queryByRole('textbox', { name: 'Search your servers' })).toBeNull();
    cleanup();

    const { onSearchChange } = renderLayout({ instances: [server({})] });
    fireEvent.change(screen.getByRole('textbox', { name: 'Search your servers' }), { target: { value: 'git' } });
    expect(onSearchChange).toHaveBeenCalledWith('git');
  });
});

describe('Catalog details', () => {
  it('shows what a server does, whether it changes data, sign-in and docs, then adds it', async () => {
    const { onAdd } = renderLayout();

    fireEvent.click(screen.getByRole('link', { name: 'Google Calendar' }));

    const details = within(await screen.findByTestId('mcp-catalog-details'));
    expect(details.getByText('Meetings & calendar')).toBeTruthy();
    expect(details.getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Schedule meetings', 'Find free time']);
    expect(details.getByText('Reads and changes data')).toBeTruthy();
    expect(details.getByText('Each person signs in with their Google Calendar account')).toBeTruthy();
    expect(details.getByRole('link', { name: 'developers.google.com' }).getAttribute('href')).toBe(
      'https://developers.google.com/calendar'
    );
    expect(details.getByText(/Everyone in your organization will see it/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Add to your organization' }));
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ typeId: 'google_calendar' }));
    await waitFor(() => expect(screen.queryByTestId('mcp-catalog-details')).toBeNull());
  });

  it('says a read-only server only looks things up, and a member adds it for themselves', async () => {
    renderLayout({ isAdmin: false });

    fireEvent.click(screen.getByRole('link', { name: 'Exa' }));

    const details = within(await screen.findByTestId('mcp-catalog-details'));
    expect(details.getByText('Reads only')).toBeTruthy();
    expect(details.getByText('An API token')).toBeTruthy();
    expect(details.getByText('Only you will see it.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add for yourself' })).toBeTruthy();
  });

  it('offers another copy of a server already added', async () => {
    renderLayout({ instances: [server({})] });

    fireEvent.click(discover().getByRole('link', { name: 'GitHub' }));

    const details = within(await screen.findByTestId('mcp-catalog-details'));
    expect(details.getByText('Added')).toBeTruthy();
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add another' })).toBeTruthy();
  });
});
