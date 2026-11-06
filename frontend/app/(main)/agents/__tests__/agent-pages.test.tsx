import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const search = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => search.params,
}));

vi.mock('@/config', () => ({
  AgentBuilder: ({ agentKey }: { agentKey: string | null }) => (
    <div data-testid="agent-builder">{agentKey ?? 'new agent'}</div>
  ),
  CreateAgentDialog: ({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) =>
    open ? (
      <div role="dialog" aria-label="Create agent">
        <button type="button" onClick={() => onOpenChange(false)}>
          Cancel
        </button>
      </div>
    ) : null,
}));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../components/index/agents-index', () => ({
  AgentsIndex: () => <div data-testid="agents-index" />,
}));

import AgentsPage from '../page';
import NewAgentPage from '../new/page';
import EditAgentPage from '../edit/page';

beforeEach(() => {
  search.params = new URLSearchParams();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('agent pages', () => {
  it('shows the Agents index at the bare agents URL', () => {
    render(<AgentsPage />);
    expect(screen.getByTestId('agents-index')).toBeTruthy();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("sends an old link with an agent in it to that agent's chat, keeping every param", () => {
    search.params = new URLSearchParams('agentId=a1&orgId=o1');
    render(<AgentsPage />);
    expect(router.replace).toHaveBeenCalledWith('/chat/?agentId=a1&orgId=o1');
    expect(screen.queryByTestId('agents-index')).toBeNull();
  });

  it('opens the create dialog over a blank builder, and returns to the Agents page when it is dismissed', async () => {
    vi.useFakeTimers();
    render(<NewAgentPage />);

    expect(screen.getByRole('dialog', { name: 'Create agent' })).toBeTruthy();
    expect(screen.getByTestId('agent-builder').textContent).toBe('new agent');

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(router.push).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(router.push).toHaveBeenCalledWith('/agents/');
  });

  it('edits the agent named in the link', () => {
    search.params = new URLSearchParams('agentKey=%20agent-7%20');
    render(<EditAgentPage />);

    expect(screen.getByTestId('agent-builder').textContent).toBe('agent-7');
  });

  it('explains what to do when the link has no agent in it', () => {
    render(<EditAgentPage />);

    expect(
      screen.getByText('Missing agent key. Open an agent from the Agents page or use a valid link.'),
    ).toBeTruthy();
    expect(screen.queryByTestId('agent-builder')).toBeNull();
  });
});
