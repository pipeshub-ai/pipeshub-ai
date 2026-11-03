import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const list = vi.fn();
const revoke = vi.fn();
vi.mock('../api', () => ({ AgentSignInApi: { list: (...a: unknown[]) => list(...a), revoke: (...a: unknown[]) => revoke(...a) } }));

import { AgentSignIns } from '../components/agent-sign-ins';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const reg = { id: 'reg_' + 'a'.repeat(24), status: 'consumed', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' };

describe('agent sign-ins', () => {
  it('shows nothing when no agent was approved', async () => {
    installBrowserShims();
    list.mockResolvedValue([]);
    renderInTheme(<AgentSignIns />);
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(screen.queryByText('Agent sign-ins')).toBeNull();
  });

  it('says the list could not be loaded and loads it again on retry', async () => {
    installBrowserShims();
    list.mockRejectedValueOnce(new Error('down')).mockResolvedValue([reg]);
    renderInTheme(<AgentSignIns />);
    expect(await screen.findByText('Could not load your agent sign-ins')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText(reg.id)).toBeTruthy();
  });

  it('lists an approved agent and revokes it', async () => {
    installBrowserShims();
    list.mockResolvedValueOnce([reg]).mockResolvedValue([]);
    revoke.mockResolvedValue(undefined);
    renderInTheme(<AgentSignIns />);
    expect(await screen.findByText(reg.id)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: `Revoke agent sign-in ${reg.id}` }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith(reg.id));
    await waitFor(() => expect(screen.queryByText(reg.id)).toBeNull());
  });
});
