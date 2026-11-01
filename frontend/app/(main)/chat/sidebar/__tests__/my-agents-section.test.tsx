import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { clearSessionCache } from '@/lib/api/session-cache';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));

vi.mock('@/config', () => ({
  AgentSidebarListRow: ({ label }: { label: string }) => <div>{label}</div>,
  useUserPermission: () => true,
  usePermissionDeniedDialog: () => ({ guard: <F,>(_allowed: boolean, fn: F) => fn, dialog: null }),
}));

const agentsApi = vi.hoisted(() => ({ getAgents: vi.fn() }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

import { MyAgentsSection } from '../my-agents-section';

function agent(name: string): AgentListRecord {
  return { id: name, _key: name, name } as AgentListRecord;
}

function page(names: string[], hasNext = false) {
  return {
    agents: names.map(agent),
    pagination: { currentPage: 1, limit: 20, totalItems: names.length, totalPages: 1, hasNext, hasPrev: false },
  };
}

async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderSection() {
  const view = renderInTheme(<MyAgentsSection />);
  await settle();
  return view;
}

describe('MyAgentsSection', () => {
  beforeEach(() => {
    installBrowserShims();
    vi.useFakeTimers();
    vi.clearAllMocks();
    clearSessionCache();
    agentsApi.getAgents.mockResolvedValue(page(['Sales helper']));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('lists agents from one quiet request, since the section shows its own error', async () => {
    await renderSection();

    expect(screen.getByText('Sales helper')).toBeTruthy();
    expect(agentsApi.getAgents).toHaveBeenCalledWith({ page: 1, limit: 20 }, { suppressErrorToast: true });
  });

  it('shows the agents at once, without asking again, when the sidebar comes back within a minute', async () => {
    (await renderSection()).unmount();
    await settle(59_000);
    renderInTheme(<MyAgentsSection />);

    expect(screen.getByText('Sales helper')).toBeTruthy();
    await settle();
    expect(agentsApi.getAgents).toHaveBeenCalledTimes(1);
  });

  it('refreshes after a minute while the agents stay on screen', async () => {
    (await renderSection()).unmount();
    await settle(61_000);
    agentsApi.getAgents.mockResolvedValue(page(['Sales helper', 'Support bot']));
    renderInTheme(<MyAgentsSection />);

    expect(screen.getByText('Sales helper')).toBeTruthy();
    await settle();
    expect(agentsApi.getAgents).toHaveBeenCalledTimes(2);
    expect(screen.getByText('Support bot')).toBeTruthy();
  });

  it('refreshes when an agent is created, edited or deleted elsewhere', async () => {
    await renderSection();
    agentsApi.getAgents.mockResolvedValue(page(['Renamed helper']));

    act(() => revalidateSwrNamespace(SWR_NS.agentLists));
    await settle();

    expect(screen.getByText('Renamed helper')).toBeTruthy();
  });

  it('says it could not load the agents when the first request fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    agentsApi.getAgents.mockRejectedValue(new Error('500'));
    await renderSection();

    expect(screen.getByText('Could not load agents')).toBeTruthy();
  });

  it('keeps the agents when a later refresh fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    (await renderSection()).unmount();
    await settle(61_000);
    agentsApi.getAgents.mockRejectedValue(new Error('500'));
    await renderSection();

    expect(agentsApi.getAgents).toHaveBeenCalledTimes(2);
    expect(screen.getByText('Sales helper')).toBeTruthy();
    expect(screen.queryByText('Could not load agents')).toBeNull();
  });

  it('offers More agents when there are more than fit or the server has another page', async () => {
    agentsApi.getAgents.mockResolvedValue(page(['One'], true));
    await renderSection();

    expect(screen.getByText('More agents')).toBeTruthy();
  });
});
