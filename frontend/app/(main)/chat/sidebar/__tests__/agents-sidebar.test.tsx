import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useToastStore } from '@/lib/store/toast-store';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { apiFailure, installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
const route = vi.hoisted(() => ({ pathname: '/chat/', params: new URLSearchParams() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => route.params,
  usePathname: () => route.pathname,
}));

const permissions = vi.hoisted(() => ({ denied: new Set<string>(), deniedAttempts: vi.fn() }));
vi.mock('@/config', async () => {
  const { AgentSidebarListRow } = await vi.importActual<typeof import('../agent-sidebar-list-row')>('../agent-sidebar-list-row');
  return {
    AgentSidebarListRow,
    useUserPermission: (key: string) => !permissions.denied.has(key),
    usePermissionDeniedDialog: () => ({
      guard: <A extends unknown[], R>(allowed: boolean, fn: (...args: A) => R) =>
        allowed ? fn : () => permissions.deniedAttempts(),
      dialog: null,
    }),
  };
});

const agentsApi = vi.hoisted(() => ({ getAgents: vi.fn(), deleteAgent: vi.fn() }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => <div role="status">Loading</div>,
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

import { AgentsSidebar } from '../agents-sidebar';

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

function page(agents: AgentListRecord[], hasNext = false) {
  return {
    agents,
    pagination: { currentPage: 1, limit: 20, totalItems: agents.length, totalPages: 1, hasNext, hasPrev: false },
  };
}

async function renderSidebar() {
  const onBack = vi.fn();
  renderInTheme(<AgentsSidebar onBack={onBack} />);
  await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  return { onBack };
}

async function openRowMenu(agentName: string) {
  const link = screen.getByRole('link', { name: agentName });
  const rowEl = link.parentElement as HTMLElement;
  fireEvent.mouseEnter(rowEl);
  const trigger = within(rowEl).getByRole('button', { name: 'Agent options' });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  return screen.getByRole('menu');
}

beforeEach(() => {
  installBrowserShims();
  class NoopIntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(window, 'IntersectionObserver', { value: NoopIntersectionObserver, configurable: true });
  permissions.denied.clear();
  route.pathname = '/chat/';
  route.params = new URLSearchParams();
  useToastStore.setState({ toasts: [] });
  agentsApi.deleteAgent.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('AgentsSidebar', () => {
  it('shows a loader, then the agents with links into chat', async () => {
    let resolve: (value: ReturnType<typeof page>) => void = () => {};
    agentsApi.getAgents.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderInTheme(<AgentsSidebar onBack={vi.fn()} />);

    expect(screen.getByRole('status')).toBeTruthy();
    await act(async () => resolve(page([listRecord(), listRecord({ id: 'agent-2', _key: 'agent-2', name: 'Ops bot' })])));

    expect(screen.getByRole('link', { name: 'Sales helper' }).getAttribute('href')).toMatch(/^\/chat\/?\?agentId=agent-1$/);
    expect(screen.getByRole('link', { name: 'Ops bot' })).toBeTruthy();
    expect(agentsApi.getAgents).toHaveBeenCalledWith({ page: 1, limit: 20, search: undefined });
  });

  it('says when there are no agents yet', async () => {
    agentsApi.getAgents.mockResolvedValue(page([]));
    await renderSidebar();

    expect(screen.getByText('No agents found')).toBeTruthy();
  });

  it('says plainly when the list could not load', async () => {
    agentsApi.getAgents.mockRejectedValue(new Error('offline'));
    await renderSidebar();

    expect(screen.getByText('Could not load agents')).toBeTruthy();
  });

  it('searches once the person has typed at least two characters', async () => {
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderSidebar();

    fireEvent.change(screen.getByPlaceholderText('Search agents…'), { target: { value: 'ops' } });

    await waitFor(() => expect(agentsApi.getAgents).toHaveBeenLastCalledWith({ page: 1, limit: 20, search: 'ops' }));
  });

  it('opens the create-agent page, unless the person may not create agents', async () => {
    agentsApi.getAgents.mockResolvedValue(page([]));
    const { onBack } = await renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'New agent' }));
    expect(router.push).toHaveBeenCalledWith('/agents/new');
    expect(onBack).toHaveBeenCalled();

    cleanup();
    router.push.mockClear();
    permissions.denied.add('createAgent');
    await renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: 'New agent' }));
    expect(router.push).not.toHaveBeenCalled();
    expect(permissions.deniedAttempts).toHaveBeenCalled();
  });

  it('offers the owner Edit and Delete, and opens the builder on Edit', async () => {
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderSidebar();

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
    await renderSidebar();

    const menu = await openRowMenu('Sales helper');
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['View agent']);
  });

  it('shows no menu on an agent the person can only chat with', async () => {
    agentsApi.getAgents.mockResolvedValue(
      page([listRecord({ can_edit: false, can_delete: false, can_share: false, user_role: '', shareWithOrg: false })]),
    );
    await renderSidebar();

    fireEvent.mouseEnter(screen.getByRole('link', { name: 'Sales helper' }).parentElement as HTMLElement);
    expect(screen.queryByRole('button', { name: 'Agent options' })).toBeNull();
  });

  it('removes the agent from the list after a confirmed delete, and leaves its open chat', async () => {
    route.params = new URLSearchParams('agentId=agent-1');
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderSidebar();

    fireEvent.click(within(await openRowMenu('Sales helper')).getByRole('menuitem', { name: 'Delete agent' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete this agent?' });
    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'delete' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(screen.queryByRole('link', { name: 'Sales helper' })).toBeNull());
    expect(agentsApi.deleteAgent).toHaveBeenCalledWith('agent-1');
    expect(router.replace).toHaveBeenCalledWith('/chat/');
  });

  it('asks for DELETE again after the person backs out of the dialog', async () => {
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderSidebar();

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

  it("keeps the agent listed and says why when the delete is refused", async () => {
    agentsApi.deleteAgent.mockRejectedValue(apiFailure(403, { message: 'Only the owner can delete this agent.' }));
    agentsApi.getAgents.mockResolvedValue(page([listRecord()]));
    await renderSidebar();

    fireEvent.click(within(await openRowMenu('Sales helper')).getByRole('menuitem', { name: 'Delete agent' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete this agent?' });
    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'DELETE' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
    const [toast] = useToastStore.getState().toasts;
    expect([toast.title, toast.description]).toEqual(['Could not delete agent', 'Only the owner can delete this agent.']);
    expect(screen.getByRole('dialog', { name: 'Delete this agent?' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Sales helper', hidden: true })).toBeTruthy();
  });
});
