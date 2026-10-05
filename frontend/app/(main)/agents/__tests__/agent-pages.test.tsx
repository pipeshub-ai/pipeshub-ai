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
  it('shows the agents landing placeholder', () => {
    render(<AgentsPage />);

    expect(screen.getByRole('heading', { name: 'Agents' })).toBeTruthy();
    expect(screen.getByText('Coming soon')).toBeTruthy();
  });

  it('opens the create dialog over a blank builder, and returns to chat when it is dismissed', async () => {
    vi.useFakeTimers();
    render(<NewAgentPage />);

    expect(screen.getByRole('dialog', { name: 'Create agent' })).toBeTruthy();
    expect(screen.getByTestId('agent-builder').textContent).toBe('new agent');

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(router.push).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(router.push).toHaveBeenCalledWith('/chat');
  });

  it('edits the agent named in the link', () => {
    search.params = new URLSearchParams('agentKey=%20agent-7%20');
    render(<EditAgentPage />);

    expect(screen.getByTestId('agent-builder').textContent).toBe('agent-7');
  });

  it('explains what to do when the link has no agent in it', () => {
    render(<EditAgentPage />);

    expect(
      screen.getByText('Missing agent key. Open an agent from the chat sidebar or use a valid link.'),
    ).toBeTruthy();
    expect(screen.queryByTestId('agent-builder')).toBeNull();
  });
});
