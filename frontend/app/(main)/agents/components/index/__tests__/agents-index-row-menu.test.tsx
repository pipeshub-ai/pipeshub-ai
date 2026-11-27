import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { useToastStore } from '@/lib/store/toast-store';
import { apiFailure, installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { AgentListRecord } from '@/app/(main)/agents/types';

// The row menu and the delete flow of main's agents sidebar (its agents-sidebar.test.tsx). That sidebar is gone:
// agents are listed on the Agents index, whose rows carry the same ⋯ menu (`agent-row-menu.tsx`). The cases the
// index's own tests already cover (the empty list, a failed load, search and New agent) are not repeated here.

installMemoryStorage();

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/agents/',
}));
vi.mock('@/config', () => {
  const state = { user: null };
  const useAuthStore = Object.assign(<T,>(select: (s: typeof state) => T) => select(state), { getState: () => state });
  return {
    useAuthStore,
    useUserPermission: () => true,
    PermissionLockIcon: () => null,
    useAgentRowShare: () => null,
  };
});
const agentsApi = vi.hoisted(() => ({ getAgents: vi.fn(), deleteAgent: vi.fn() }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));
const usersApi = vi.hoisted(() => ({ getUsersByIds: vi.fn() }));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: usersApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));

const { AgentsIndex } = await import('../agents-index');

function listRecord(overrides: Partial<AgentListRecord> = {}): AgentListRecord {
  return {
    id: 'agent-1',
    _key: 'agent-1',
    _id: 'agentInstances/agent-1',
    name: 'Sales helper',
    description: '',
    models: [],
    startMessage: '',
    systemPrompt: '',
    tags: [],
    isActive: true,
    isDeleted: false,
    createdAtTimestamp: 0,
    updatedAtTimestamp: 0,
    createdBy: 'user-owner',
    access_type: 'INDIVIDUAL',
    user_role: 'OWNER',
    can_edit: true,
    can_delete: true,
    can_share: true,
    can_view: true,
    shareWithOrg: false,
    ...overrides,
  };
}

function page(agents: AgentListRecord[]) {
  return {
    agents,
    pagination: { currentPage: 1, limit: 50, totalItems: agents.length, totalPages: 1, hasNext: false, hasPrev: false },
  };
}

const rowOf = (name: string) => screen.getByText(name).closest('[role="button"]') as HTMLElement;

async function renderIndex() {
  renderInTheme(<AgentsIndex />);
  await screen.findByText('Sales helper');
}

async function openRowMenu(agentName: string) {
  const trigger = within(rowOf(agentName)).getByRole('button', { name: `Actions for ${agentName}` });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  return screen.getByRole('menu');
}

beforeEach(() => {
  installBrowserShims();
  window.history.replaceState(null, '', '/agents/');
  useToastStore.setState({ toasts: [] });
  agentsApi.deleteAgent.mockResolvedValue(undefined);
  usersApi.getUsersByIds.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the Agents index row menu and delete (from the agents sidebar)', () => {
  it('shows a loader, then the agents, each opening its chat', async () => {
    let resolve: (value: ReturnType<typeof page>) => void = () => {};
    agentsApi.getAgents.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderInTheme(<AgentsIndex />);

    expect(screen.getByRole('status')).toBeTruthy();
    await act(async () => resolve(page([listRecord(), listRecord({ id: 'agent-2', _key: 'agent-2', name: 'Ops bot' })])));

    expect(screen.getByText('Ops bot')).toBeTruthy();
    fireEvent.click(rowOf('Sales helper'));
    expect(router.push).toHaveBeenCalledWith('/chat/?agentId=agent-1');
  });

  it('offers the owner Edit and Delete, and opens the builder on Edit', async () => {
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderIndex();

    const menu = await openRowMenu('Sales helper');
    expect(within(menu).queryByRole('menuitem', { name: 'View agent' })).toBeNull();
    expect(within(menu).getByRole('menuitem', { name: 'Delete agent' })).toBeTruthy();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Edit agent' }));

    expect(router.push).toHaveBeenCalledWith('/agents/edit?agentKey=agent-1');
  });

  it('offers only View to someone the agent was shared with', async () => {
    agentsApi.getAgents.mockResolvedValue(
      page([listRecord({ can_edit: false, can_delete: false, can_share: false, user_role: 'VIEWER' })]),
    );
    await renderIndex();

    const menu = await openRowMenu('Sales helper');
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['View agent']);
  });

  // Main's agents sidebar gave its ⋯ `touch-hit` (#4479), as every other row's ⋯ has it: the row opens the chat,
  // so a near miss on a phone would open the agent instead of its menu.
  it('grows the ⋯ tap area to 44px on touch screens', async () => {
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderIndex();

    const trigger = within(rowOf('Sales helper')).getByRole('button', { name: 'Actions for Sales helper' });
    expect(trigger.classList.contains('touch-hit')).toBe(true);
  });

  it('shows no menu on an agent the person can only chat with', async () => {
    agentsApi.getAgents.mockResolvedValue(
      page([listRecord({ can_edit: false, can_delete: false, can_share: false, user_role: '', shareWithOrg: false })]),
    );
    await renderIndex();

    expect(within(rowOf('Sales helper')).queryByRole('button', { name: 'Actions for Sales helper' })).toBeNull();
  });

  // The sidebar's "and leaves its open chat" has no place here: the index is not a chat, and no agent chat deletes its agent.
  it('removes the agent from the list after a confirmed delete', async () => {
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    // The list reloads after a delete: from then on the server no longer has the agent.
    agentsApi.deleteAgent.mockImplementation(async () => {
      agentsApi.getAgents.mockResolvedValue(page([]));
    });
    await renderIndex();

    fireEvent.click(within(await openRowMenu('Sales helper')).getByRole('menuitem', { name: 'Delete agent' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete this agent?' });
    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'delete' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(screen.queryByText('Sales helper')).toBeNull());
    expect(agentsApi.deleteAgent).toHaveBeenCalledWith('agent-1');
  });

  it('asks for DELETE again after the person backs out of the dialog', async () => {
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderIndex();

    fireEvent.click(within(await openRowMenu('Sales helper')).getByRole('menuitem', { name: 'Delete agent' }));
    let dialog = await screen.findByRole('dialog', { name: 'Delete this agent?' });
    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'DELETE' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(within(await openRowMenu('Sales helper')).getByRole('menuitem', { name: 'Delete agent' }));
    dialog = await screen.findByRole('dialog', { name: 'Delete this agent?' });
    expect(within(dialog).getByPlaceholderText('DELETE')).toHaveProperty('value', '');
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toHaveProperty('disabled', true);
  });

  it('keeps the agent listed and says why when the delete is refused', async () => {
    agentsApi.deleteAgent.mockRejectedValue(apiFailure(403, { message: 'Only the owner can delete this agent.' }));
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderIndex();

    fireEvent.click(within(await openRowMenu('Sales helper')).getByRole('menuitem', { name: 'Delete agent' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete this agent?' });
    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'DELETE' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
    const [toast] = useToastStore.getState().toasts;
    expect([toast.title, toast.description]).toEqual(['Could not delete agent', 'Only the owner can delete this agent.']);
    expect(screen.getByRole('dialog', { name: 'Delete this agent?' })).toBeTruthy();
    expect(screen.getByText('Sales helper')).toBeTruthy();
  });
});
