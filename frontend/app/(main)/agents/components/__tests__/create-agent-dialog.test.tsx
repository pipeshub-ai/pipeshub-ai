import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { AgentFormPayload } from '../../agent-builder/types';
import { agentDetail, apiFailure, installBrowserShims, renderInTheme } from '../../agent-builder/__tests__/agent-builder-harness';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

const createAgent = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: { createAgent } }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { CreateAgentDialog } from '../create-agent-dialog';

function renderDialog() {
  const onOpenChange = vi.fn();
  renderInTheme(<CreateAgentDialog open onOpenChange={onOpenChange} />);
  return { onOpenChange, dialog: screen.getByRole('dialog', { name: 'Create agent' }) };
}

function typeName(dialog: HTMLElement, value: string) {
  fireEvent.change(within(dialog).getByPlaceholderText('e.g. Support bot'), { target: { value } });
}

function lastPayload(): AgentFormPayload {
  return createAgent.mock.calls[createAgent.mock.calls.length - 1]?.[0] as AgentFormPayload;
}

beforeEach(() => {
  installBrowserShims();
  createAgent.mockImplementation(async (payload: AgentFormPayload) => agentDetail({ name: payload.name, _key: 'agent-9' }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('CreateAgentDialog', () => {
  it('asks for a name, and clears the message once the person types one', async () => {
    const { dialog } = renderDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Create agent' }));

    expect(within(dialog).getByText('Enter a name to continue.')).toBeTruthy();
    expect(createAgent).not.toHaveBeenCalled();
    typeName(dialog, 'Support bot');
    expect(within(dialog).queryByText('Enter a name to continue.')).toBeNull();
  });

  it('creates a personal agent and opens it in the builder', async () => {
    const { dialog } = renderDialog();

    typeName(dialog, '  Support bot  ');
    fireEvent.keyDown(within(dialog).getByPlaceholderText('e.g. Support bot'), { key: 'Enter' });

    await waitFor(() => expect(createAgent).toHaveBeenCalledTimes(1));
    expect(lastPayload()).toMatchObject({ name: 'Support bot', shareWithOrg: false, isServiceAccount: false });
    expect(router.replace).toHaveBeenCalledWith('/agents/edit?agentKey=agent-9');
    expect(within(dialog).getByRole('button', { name: /creating/i })).toHaveProperty('disabled', true);
  });

  it('creates a service agent only after the person confirms what that means', async () => {
    const { dialog } = renderDialog();
    typeName(dialog, 'Ops bot');

    fireEvent.click(within(dialog).getByText('Service agent'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));

    const confirm = await screen.findByRole('dialog', { name: /create service agent/i });
    expect(within(confirm).getByText('Ops bot')).toBeTruthy();
    within(confirm).getAllByRole('checkbox').forEach((box) => fireEvent.click(box));
    fireEvent.click(within(confirm).getByRole('button', { name: 'Create service agent' }));

    await waitFor(() => expect(createAgent).toHaveBeenCalledTimes(1));
    expect(lastPayload()).toMatchObject({ name: 'Ops bot', shareWithOrg: true, isServiceAccount: true });
    expect(router.replace).toHaveBeenCalledWith('/agents/edit?agentKey=agent-9&sa=1');
  });

  it('goes back to the form when the person cancels the service agent confirmation', async () => {
    const { dialog } = renderDialog();
    typeName(dialog, 'Ops bot');
    fireEvent.click(within(dialog).getByText('Service agent'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));

    const confirm = await screen.findByRole('dialog', { name: /create service agent/i });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }));

    const form = await screen.findByRole('dialog', { name: 'Create agent' });
    expect(within(form).getByPlaceholderText('e.g. Support bot')).toHaveProperty('value', 'Ops bot');
    expect(createAgent).not.toHaveBeenCalled();
  });

  it("shows the server's reason when the agent cannot be created, and lets the person try again", async () => {
    createAgent.mockRejectedValue(apiFailure(409, { message: 'An agent called Support bot already exists. Choose another name.' }));
    const { dialog } = renderDialog();

    typeName(dialog, 'Support bot');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create agent' }));

    expect(await within(dialog).findByText('An agent called Support bot already exists. Choose another name.')).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Create agent' })).toHaveProperty('disabled', false);
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("shows the server's reason when the service agent cannot be created", async () => {
    createAgent.mockRejectedValue(apiFailure(403, { message: 'Only admins can create service agents.' }));
    const { dialog } = renderDialog();
    typeName(dialog, 'Ops bot');
    fireEvent.click(within(dialog).getByText('Service agent'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));

    const confirm = await screen.findByRole('dialog', { name: /create service agent/i });
    within(confirm).getAllByRole('checkbox').forEach((box) => fireEvent.click(box));
    fireEvent.click(within(confirm).getByRole('button', { name: 'Create service agent' }));

    expect(await within(confirm).findByText('Only admins can create service agents.')).toBeTruthy();
  });

  it('closes when the person cancels', () => {
    const { dialog, onOpenChange } = renderDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
