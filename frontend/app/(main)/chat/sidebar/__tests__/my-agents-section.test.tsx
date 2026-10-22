import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { AgentListRecord } from '@/app/(main)/agents/types';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/config', async () => {
  const { AgentSidebarListRow } = await vi.importActual<typeof import('../agent-sidebar-list-row')>('../agent-sidebar-list-row');
  return {
    AgentSidebarListRow,
    useUserPermission: () => true,
    usePermissionDeniedDialog: () => ({ guard: <F,>(_allowed: boolean, fn: F) => fn, dialog: null }),
  };
});
const agentsApi = vi.hoisted(() => ({ getAgents: vi.fn() }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: agentsApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

import { MyAgentsSection } from '../my-agents-section';
import { AGENTS_CHANGED_EVENT } from '../../constants';

const agent = (id: string, name: string) => ({ id, _key: id, name, isActive: true, isDeleted: false }) as unknown as AgentListRecord;
const page = (agents: AgentListRecord[]) => ({ agents, pagination: { currentPage: 1, limit: 20, totalItems: agents.length, totalPages: 1, hasNext: false, hasPrev: false } });

beforeEach(() => {
  installBrowserShims();
  agentsApi.getAgents.mockReset();
});
afterEach(() => cleanup());

describe('MyAgentsSection', () => {
  it('reloads when the agents change outside the list (an agent created from a chat), with no reload of the page', async () => {
    agentsApi.getAgents.mockResolvedValueOnce(page([agent('a1', 'Alpha')])).mockResolvedValueOnce(page([agent('a1', 'Alpha'), agent('a2', 'Beta')]));
    renderInTheme(<MyAgentsSection />);
    expect(await screen.findByText('Alpha')).toBeTruthy();
    expect(screen.queryByText('Beta')).toBeNull();

    await act(async () => {
      window.dispatchEvent(new Event(AGENTS_CHANGED_EVENT));
    });
    expect(await screen.findByText('Beta')).toBeTruthy();
    expect(agentsApi.getAgents).toHaveBeenCalledTimes(2);
  });

  it('stops listening once it is gone', async () => {
    agentsApi.getAgents.mockResolvedValue(page([agent('a1', 'Alpha')]));
    const { unmount } = renderInTheme(<MyAgentsSection />);
    expect(await screen.findByText('Alpha')).toBeTruthy();
    unmount();
    window.dispatchEvent(new Event(AGENTS_CHANGED_EVENT));
    expect(agentsApi.getAgents).toHaveBeenCalledTimes(1);
  });
});
