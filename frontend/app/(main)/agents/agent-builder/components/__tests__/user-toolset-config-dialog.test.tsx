import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { apiFailure, installBrowserShims, renderInTheme, toolset } from '../../__tests__/agent-builder-harness';

const toolsetsApi = vi.hoisted(() => ({
  getToolsetRegistrySchema: vi.fn(),
  authenticateMyToolsetInstance: vi.fn(),
  updateMyToolsetCredentials: vi.fn(),
  removeMyToolsetCredentials: vi.fn(),
  reauthenticateMyToolsetInstance: vi.fn(),
  getInstanceOAuthAuthorizationUrl: vi.fn(),
  findMyToolsetByInstanceId: vi.fn(),
}));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: toolsetsApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { UserToolsetConfigDialog } from '../user-toolset-config-dialog';

const apiTokenSchema = {
  toolset: {
    config: {
      auth: {
        schemas: {
          API_TOKEN: {
            fields: [
              { name: 'apiToken', displayName: 'API token', fieldType: 'PASSWORD', required: true, placeholder: 'Paste your API token' },
            ],
          },
        },
      },
    },
  },
};

function renderDialog(ts: BuilderSidebarToolset = toolset({ isAuthenticated: false })) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const onNotify = vi.fn();
  renderInTheme(
    <UserToolsetConfigDialog
      toolset={ts}
      instanceId={ts.instanceId ?? ''}
      onClose={onClose}
      onSuccess={onSuccess}
      onNotify={onNotify}
    />,
  );
  return { onClose, onSuccess, onNotify };
}

async function configDialog() {
  const dialog = await screen.findByRole('dialog', { name: /configure toolset/i });
  await waitFor(() => expect(within(dialog).queryByText('Loading schema…')).toBeNull());
  return dialog;
}

beforeEach(() => {
  installBrowserShims();
  toolsetsApi.getToolsetRegistrySchema.mockResolvedValue(apiTokenSchema);
  toolsetsApi.authenticateMyToolsetInstance.mockResolvedValue(undefined);
  toolsetsApi.updateMyToolsetCredentials.mockResolvedValue(undefined);
  toolsetsApi.removeMyToolsetCredentials.mockResolvedValue(undefined);
  toolsetsApi.reauthenticateMyToolsetInstance.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('UserToolsetConfigDialog', () => {
  it('names the required field the person left empty', async () => {
    renderDialog();
    const dialog = await configDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Save credentials' }));

    expect(await within(dialog).findByText('API token is required')).toBeTruthy();
    expect(toolsetsApi.authenticateMyToolsetInstance).not.toHaveBeenCalled();
  });

  it("saves the person's own credentials for the toolset", async () => {
    const { onClose, onSuccess, onNotify } = renderDialog();
    const dialog = await configDialog();

    fireEvent.change(within(dialog).getByPlaceholderText('Paste your API token'), { target: { value: 'mine-123' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save credentials' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toolsetsApi.authenticateMyToolsetInstance).toHaveBeenCalledWith('jira-instance-1', { apiToken: 'mine-123' });
    expect(onSuccess).toHaveBeenCalled();
    expect(onNotify).toHaveBeenCalledWith('Toolset authentication updated.');
  });

  it("shows the server's reason when the person's credentials are rejected", async () => {
    toolsetsApi.authenticateMyToolsetInstance.mockRejectedValue(
      apiFailure(400, { message: 'This token has expired. Create a new one in Jira and paste it here.' }),
    );
    const { onClose } = renderDialog();
    const dialog = await configDialog();

    fireEvent.change(within(dialog).getByPlaceholderText('Paste your API token'), { target: { value: 'old' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save credentials' }));

    expect(await within(dialog).findByText('This token has expired. Create a new one in Jira and paste it here.')).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('updates credentials the person already saved', async () => {
    const { onClose } = renderDialog(toolset({ isAuthenticated: true, auth: { apiToken: 'old' } }));
    const dialog = await configDialog();

    fireEvent.change(within(dialog).getByPlaceholderText('Paste your API token'), { target: { value: 'newer' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toolsetsApi.updateMyToolsetCredentials).toHaveBeenCalledWith('jira-instance-1', { apiToken: 'newer' });
  });

  it('removes saved credentials after confirmation', async () => {
    const { onClose } = renderDialog(toolset({ isAuthenticated: true }));
    const dialog = await configDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove credentials' }));
    const confirm = await screen.findByRole('dialog', { name: 'Remove credentials?' });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(toolsetsApi.removeMyToolsetCredentials).toHaveBeenCalledWith('jira-instance-1'));
    expect(onClose).toHaveBeenCalled();
  });

  it('signs the person in through a popup and closes when the toolset shows as connected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    toolsetsApi.getInstanceOAuthAuthorizationUrl.mockResolvedValue({ success: true, authorizationUrl: 'https://auth.example.com/me' });
    toolsetsApi.findMyToolsetByInstanceId.mockResolvedValue({ isAuthenticated: true });
    const open = vi.spyOn(window, 'open').mockReturnValue({ closed: false, close: vi.fn(), focus: vi.fn() } as unknown as Window);
    const { onClose } = renderDialog(toolset({ authType: 'OAUTH', isAuthenticated: false }));
    const dialog = await configDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));
    await waitFor(() => expect(open).toHaveBeenCalledWith('https://auth.example.com/me', 'oauth_user_toolset', expect.any(String)));
    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', { data: { type: 'TOOLSET_OAUTH_SUCCESS' }, origin: window.location.origin }));
      await vi.advanceTimersByTimeAsync(2000);
    });

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toolsetsApi.findMyToolsetByInstanceId).toHaveBeenCalledWith('jira-instance-1');
  });

  it('explains when the sign-in link could not be created', async () => {
    toolsetsApi.getInstanceOAuthAuthorizationUrl.mockResolvedValue({ success: false });
    renderDialog(toolset({ authType: 'OAUTH', isAuthenticated: false }));
    const dialog = await configDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));

    expect(await within(dialog).findByText('Failed to get authorization URL')).toBeTruthy();
  });
});
