import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { AgentListRecord } from '@/app/(main)/agents/types';

installMemoryStorage();

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
const url = vi.hoisted(() => ({ query: '' }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(url.query),
  usePathname: () => '/agents/',
}));
const permissions = vi.hoisted(() => ({ denied: new Set<string>() }));
vi.mock('@/config', () => {
  const state = { user: null };
  const useAuthStore = Object.assign(<T,>(select: (s: typeof state) => T) => select(state), { getState: () => state });
  return {
    useAuthStore,
    useUserPermission: (key: string) => !permissions.denied.has(key),
    PermissionLockIcon: () => <span>Locked</span>,
    useAgentRowShare: () => null,
  };
});
const agentsApi = vi.hoisted(() => ({ getAgents: vi.fn(), deleteAgent: vi.fn() }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));
const usersApi = vi.hoisted(() => ({ getUsersByIds: vi.fn() }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: usersApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} /> }));
const layout = vi.hoisted(() => ({ drawer: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => layout.drawer, useIsBelowDesktop: () => layout.drawer }));

const { useUserStore } = await import('@/lib/store/user-store');
const { useMobileSidebarStore } = await import('@/lib/store/mobile-sidebar-store');
const { AgentsIndex } = await import('../agents-index');

const ME = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const BOB = 'bbbbbbbbbbbbbbbbbbbbbbbb';

const agent = (id: string, createdBy: string, extra: Partial<AgentListRecord> = {}) =>
  ({ id, _key: id, name: `Agent ${id}`, description: '', createdBy, can_edit: true, can_delete: true, ...extra }) as AgentListRecord;
/** One page from the list API, which counts every match, not just this page. */
const page = (agents: AgentListRecord[], totalItems = agents.length, limit = 50) => ({
  agents,
  pagination: {
    currentPage: 1,
    limit,
    totalItems,
    totalPages: Math.ceil(totalItems / limit),
    hasNext: false,
    hasPrev: false,
  },
});
const lastRequest = () => agentsApi.getAgents.mock.calls.at(-1)?.[0];
const currentUrl = () => window.location.pathname + window.location.search;
const header = () => screen.getByText('Agents', { selector: 'span' }).closest('.rt-Flex')!.parentElement as HTMLElement;

/** Opens a Radix dropdown from the keyboard, the way jsdom can. */
async function openMenu(trigger: HTMLElement) {
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
}

beforeEach(() => {
  installBrowserShims();
  router.push.mockReset();
  router.replace.mockReset();
  url.query = '';
  window.history.replaceState(null, '', '/agents/');
  permissions.denied.clear();
  agentsApi.getAgents.mockReset();
  usersApi.getUsersByIds.mockReset();
  useUserStore.setState({ profile: { userId: ME } as never });
  layout.drawer = false;
  useMobileSidebarStore.setState({ isOpen: false, registeredCount: 0 });
});
afterEach(() => cleanup());

describe('the Agents index', () => {
  it('leads the header row with Open sidebar in the drawer layout, 8px before the title, with no extra inset', async () => {
    layout.drawer = true;
    useMobileSidebarStore.setState({ registeredCount: 1 });
    agentsApi.getAgents.mockResolvedValue(page([]));
    renderInTheme(<AgentsIndex />);

    const toggle = await screen.findByRole('button', { name: 'Open sidebar' });
    const group = toggle.parentElement as HTMLElement;
    expect(group.firstElementChild).toBe(toggle);
    expect(group.textContent).toContain('Agents');
    expect(group.className).toContain('rt-r-gap-2');
    // The header row's own 12px is the only inset, as on the other index pages.
    expect(group.style.paddingLeft).toBe('');
    expect((group.parentElement as HTMLElement).style.paddingInline).toBe('var(--space-3)');
  });

  it('puts Find and then New agent at the right of the header, as Collections does, with no search field until Find', async () => {
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', ME)]));
    renderInTheme(<AgentsIndex />);
    await screen.findByText('Agent a1');

    const actions = [...header().children].at(-1) as HTMLElement;
    const buttons = within(actions).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['Find', 'New agent']);
    // The quiet Find and the size-1 solid primary of the Collections header.
    expect(buttons[0].className).toContain('rt-variant-ghost');
    expect(buttons[1].className).toContain('rt-variant-solid');
    expect(buttons[1].className).toContain('rt-r-size-1');
    expect(screen.queryByRole('textbox')).toBeNull();

    fireEvent.click(buttons[0]);
    const search = screen.getByRole('textbox', { name: 'Search agents' });
    expect(document.activeElement).toBe(search);
    expect(within(actions).getByRole('button', { name: 'Close search' })).toBeTruthy();
  });

  it('asks the server for one page of 50 and names the owners on it with one lookup', async () => {
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', ME), agent('a2', BOB), agent('a3', 'system')], 120));
    usersApi.getUsersByIds.mockResolvedValue([{ id: BOB, name: 'Bob Builder', email: 'bob@example.com' }]);
    renderInTheme(<AgentsIndex />);

    expect(await screen.findByText('Agent a3')).toBeTruthy();
    expect(agentsApi.getAgents).toHaveBeenCalledTimes(1);
    expect(lastRequest()).toEqual({ page: 1, limit: 50, search: undefined });
    expect(usersApi.getUsersByIds.mock.calls).toEqual([[[BOB]]]);
    // The title carries no count; the footer gives the server's total, not the rows on this page.
    expect(within(header()).queryByText('120')).toBeNull();
    expect(header().querySelector('.rt-Badge')).toBeNull();
    expect(screen.getByText('Showing 1-50 of 120')).toBeTruthy();
    const rowOf = (name: string) => screen.getByText(name).closest('[role="button"]') as HTMLElement;
    expect(within(rowOf('Agent a1')).getByText('You')).toBeTruthy();
    expect(within(rowOf('Agent a2')).getByText('Bob Builder')).toBeTruthy();
    expect(rowOf('Agent a3').textContent).not.toContain('You');
  });

  it('pages on the server: Next asks for page 2, a new page size starts again from page 1, and the URL keeps both', async () => {
    agentsApi.getAgents.mockImplementation(async ({ page: p, limit }: { page: number; limit: number }) =>
      page([agent(`p${p}-${limit}`, ME)], 28, limit),
    );
    renderInTheme(<AgentsIndex />);
    expect(await screen.findByText('Agent p1-50')).toBeTruthy();

    await openMenu(screen.getByRole('button', { name: '50' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: '10 per page' }));
    expect(await screen.findByText('Agent p1-10')).toBeTruthy();
    expect(lastRequest()).toEqual({ page: 1, limit: 10, search: undefined });
    expect(screen.getByText('1 / 3')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Agent p2-10')).toBeTruthy();
    expect(lastRequest()).toEqual({ page: 2, limit: 10, search: undefined });
    expect(screen.getByText('Showing 11-20 of 28')).toBeTruthy();
    expect(currentUrl()).toBe('/agents/?page=2&limit=10');

    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('Agent p1-10')).toBeTruthy();
    expect(currentUrl()).toBe('/agents/?limit=10');
  });

  it('searches on the server from page 1, says when nothing matches, and Escape clears the search', async () => {
    url.query = 'page=2&limit=10';
    agentsApi.getAgents.mockImplementation(async ({ search }: { search?: string }) =>
      search === 'nothing like this'
        ? page([], 0, 10)
        : page([agent(search ? 'match' : 'any', ME, { name: search ? 'Ops' : 'Any agent' })], 28, 10),
    );
    renderInTheme(<AgentsIndex />);
    expect(await screen.findByText('Any agent')).toBeTruthy();
    expect(lastRequest()).toEqual({ page: 2, limit: 10, search: undefined });

    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Search agents' }), { target: { value: '  travel ' } });
    // The server decides what matches: its row shows though "travel" is not in its name.
    expect(await screen.findByText('Ops')).toBeTruthy();
    expect(lastRequest()).toEqual({ page: 1, limit: 10, search: 'travel' });
    expect(currentUrl()).toBe('/agents/?search=travel&limit=10');

    fireEvent.change(screen.getByRole('textbox', { name: 'Search agents' }), { target: { value: 'nothing like this' } });
    expect(await screen.findByText('No agents match your search')).toBeTruthy();
    expect(screen.getByText('Try a different search.')).toBeTruthy();
    expect(screen.queryByText(/^Showing/)).toBeNull();

    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Search agents' }), { key: 'Escape' });
    expect(await screen.findByText('Any agent')).toBeTruthy();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(lastRequest()).toEqual({ page: 1, limit: 10, search: undefined });
  });

  it('opens on the search, page and page size a link carries', async () => {
    url.query = 'search=QA&page=2&limit=10';
    agentsApi.getAgents.mockResolvedValue(page([agent('qa', ME)], 12, 10));
    renderInTheme(<AgentsIndex />);

    expect(await screen.findByText('Agent qa')).toBeTruthy();
    expect(agentsApi.getAgents.mock.calls[0][0]).toEqual({ page: 2, limit: 10, search: 'QA' });
    expect((screen.getByRole('textbox', { name: 'Search agents' }) as HTMLInputElement).value).toBe('QA');
    expect(screen.getByText('Showing 11-12 of 12')).toBeTruthy();
  });

  it('shows the last page when asked for one past the end', async () => {
    url.query = 'page=9&limit=10';
    agentsApi.getAgents.mockImplementation(async ({ page: p }: { page: number }) =>
      p > 3 ? page([], 28, 10) : page([agent(`p${p}`, ME)], 28, 10),
    );
    renderInTheme(<AgentsIndex />);

    expect(await screen.findByText('Agent p3')).toBeTruthy();
    expect(agentsApi.getAgents.mock.calls.map(([q]) => q.page)).toEqual([9, 3]);
  });

  it('leads each row with the agent glyph, 12px before its name and description, hidden from screen readers', async () => {
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', ME, { description: 'Answers travel questions' })]));
    renderInTheme(<AgentsIndex />);
    const row = (await screen.findByText('Agent a1')).closest('[role="button"]') as HTMLElement;

    const cell = row.firstElementChild as HTMLElement;
    const glyph = cell.querySelector('[data-icon="smart_toy"]') as HTMLElement;
    expect(glyph).toBeTruthy();
    expect(cell.firstElementChild).toBe(glyph.parentElement);
    expect(glyph.parentElement!.getAttribute('aria-hidden')).toBe('true');
    expect(cell.className).toContain('rt-r-gap-3');
    expect(within(cell).getByText('Answers travel questions')).toBeTruthy();
  });

  it('leads the second line with the handle, so two agents with the same name can be told apart', async () => {
    agentsApi.getAgents.mockResolvedValue(
      page([
        agent('a1', ME, { name: 'Travel helper', handle: 'travel-helper', description: 'Answers travel questions' }),
        agent('a2', ME, { name: 'Travel helper', handle: 'travel-helper-2' }),
        agent('a3', ME, { name: 'Old helper', description: 'Made before handles' }),
      ]),
    );
    renderInTheme(<AgentsIndex />);
    const line = await screen.findByText('@travel-helper · Answers travel questions');
    expect(line.getAttribute('title')).toBe('@travel-helper · Answers travel questions');
    expect(screen.getByText('@travel-helper-2')).toBeTruthy();
    expect(screen.getByText('Made before handles')).toBeTruthy();
  });

  it("opens the agent's chat from the row, but not from its menu", async () => {
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', ME)]));
    renderInTheme(<AgentsIndex />);
    const row = (await screen.findByText('Agent a1')).closest('[role="button"]') as HTMLElement;

    const trigger = within(row).getByRole('button', { name: 'Actions for Agent a1' });
    fireEvent.click(trigger);
    await openMenu(trigger);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete agent' }));
    expect(await screen.findByRole('dialog', { name: 'Delete this agent?' })).toBeTruthy();
    expect(router.push).not.toHaveBeenCalled();

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(row);
    expect(router.push).toHaveBeenCalledWith('/chat/?agentId=a1');
  });

  it('offers New agent to someone who may create agents, and opens the create flow', async () => {
    agentsApi.getAgents.mockResolvedValue(page([]));
    renderInTheme(<AgentsIndex />);

    expect(await screen.findByText('No agents yet')).toBeTruthy();
    expect(screen.getByText('Agents you create or can use show up here. To create one, select New agent.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'New agent' }));
    expect(router.push).toHaveBeenCalledWith('/agents/new');
  });

  it('shows no New agent button, and no hint about it, to someone who may not create agents', async () => {
    permissions.denied.add('createAgent');
    agentsApi.getAgents.mockResolvedValue(page([]));
    renderInTheme(<AgentsIndex />);

    expect(await screen.findByText('Agents you create or can use show up here.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'New agent' })).toBeNull();
  });

  it('writes the owner and the date in the secondary text role, for you and for anyone else', async () => {
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', ME, { updatedAtTimestamp: Date.now() }), agent('a2', BOB)]));
    usersApi.getUsersByIds.mockResolvedValue([{ id: BOB, name: 'Bob Builder' }]);
    renderInTheme(<AgentsIndex />);

    expect((await screen.findByText('Bob Builder')).style.color).toBe('var(--text-secondary)');
    expect(screen.getByText('You').style.color).toBe('var(--text-secondary)');
    expect(screen.getByText('Agent a1').style.color).toBe('var(--text-primary)');
  });

  it('stops the Agent column at 640px so the owner and date stay beside the name, and drops the date on phones', async () => {
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', ME, { updatedAtTimestamp: Date.now() })]));
    const wide = renderInTheme(<AgentsIndex />);
    const row = (await screen.findByText('Agent a1')).closest('[role="button"]') as HTMLElement;
    const columns = 'minmax(160px, calc(var(--space-9) * 10)) 110px 96px minmax(var(--space-7), 1fr)';
    expect(row.style.gridTemplateColumns).toBe(columns);
    expect((screen.getByText('Updated').parentElement!.parentElement as HTMLElement).style.gridTemplateColumns).toBe(columns);
    // The ⋯ keeps to the row's end of its widened column.
    expect((row.lastElementChild as HTMLElement).style.justifyContent).toBe('flex-end');
    wide.unmount();

    layout.drawer = true;
    renderInTheme(<AgentsIndex />);
    const phoneRow = (await screen.findByText('Agent a1')).closest('[role="button"]') as HTMLElement;
    expect(phoneRow.style.gridTemplateColumns).toBe('minmax(160px, 1fr) 110px var(--space-7)');
    expect(phoneRow.children).toHaveLength(3);
    expect(screen.queryByText('Updated')).toBeNull();
  });

  it("locks Edit and Delete in the row menu when the person's role does not allow them", async () => {
    permissions.denied.add('editAgent');
    permissions.denied.add('deleteAgent');
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', ME)]));
    renderInTheme(<AgentsIndex />);
    const row = (await screen.findByText('Agent a1')).closest('[role="button"]') as HTMLElement;

    await openMenu(within(row).getByRole('button', { name: 'Actions for Agent a1' }));
    expect((await screen.findByRole('menuitem', { name: /Edit agent/ })).getAttribute('aria-disabled')).toBe('true');
    expect(screen.getByRole('menuitem', { name: /Delete agent/ }).getAttribute('aria-disabled')).toBe('true');
  });

  it('says when the list could not load, and tries again', async () => {
    agentsApi.getAgents.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(page([agent('a1', ME)]));
    renderInTheme(<AgentsIndex />);

    expect(await screen.findByText("Couldn't load agents")).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Agent a1')).toBeTruthy();
  });
});
