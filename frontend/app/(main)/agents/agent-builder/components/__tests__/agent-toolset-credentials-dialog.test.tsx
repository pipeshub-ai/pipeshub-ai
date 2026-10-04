import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { apiFailure, installBrowserShims, renderInTheme, toolset } from '../../__tests__/agent-builder-harness';

const toolsetsApi = vi.hoisted(() => ({
  getToolsetRegistrySchema: vi.fn(),
  authenticateAgentToolset: vi.fn(),
  updateAgentToolsetCredentials: vi.fn(),
  removeAgentToolsetCredentials: vi.fn(),
  reauthenticateAgentToolset: vi.fn(),
  getAgentToolsetOAuthUrl: vi.fn(),
  findAgentToolsetByInstanceId: vi.fn(),
}));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: toolsetsApi }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AgentToolsetCredentialsDialog } from '../agent-toolset-credentials-dialog';

const apiTokenSchema = {
  toolset: {
    config: {
      auth: {
        schemas: {
          API_TOKEN: {
            fields: [
              { name: 'apiToken', displayName: 'API token', fieldType: 'PASSWORD', required: true, placeholder: 'Paste the API token' },
              { name: 'baseUrl', displayName: 'Site URL', fieldType: 'URL', required: false, placeholder: 'https://example.atlassian.net' },
              { name: 'clientSecret', displayName: 'Client secret', fieldType: 'PASSWORD', required: false, placeholder: 'Org client secret' },
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
    <AgentToolsetCredentialsDialog
      toolset={ts}
      instanceId={ts.instanceId ?? ''}
      agentKey="agent-1"
      onClose={onClose}
      onSuccess={onSuccess}
      onNotify={onNotify}
    />,
  );
  return { onClose, onSuccess, onNotify };
}

async function credentialsDialog() {
  const dialog = await screen.findByRole('dialog', { name: /agent toolset credentials/i });
  await waitFor(() => expect(within(dialog).queryByText('Loading schema…')).toBeNull());
  return dialog;
}

beforeEach(() => {
  installBrowserShims();
  toolsetsApi.getToolsetRegistrySchema.mockResolvedValue(apiTokenSchema);
  toolsetsApi.authenticateAgentToolset.mockResolvedValue(undefined);
  toolsetsApi.updateAgentToolsetCredentials.mockResolvedValue(undefined);
  toolsetsApi.removeAgentToolsetCredentials.mockResolvedValue(undefined);
  toolsetsApi.reauthenticateAgentToolset.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe('AgentToolsetCredentialsDialog — API token toolsets', () => {
  it('loads the fields for the toolset and never asks for the org OAuth app secret', async () => {
    renderDialog();
    const dialog = await credentialsDialog();

    expect(toolsetsApi.getToolsetRegistrySchema).toHaveBeenCalledWith('jira');
    expect(within(dialog).getByText('Team Jira')).toBeTruthy();
    expect(within(dialog).getByPlaceholderText('Paste the API token')).toBeTruthy();
    expect(within(dialog).queryByPlaceholderText('Org client secret')).toBeNull();
  });

  it('names the missing required field instead of sending an empty form', async () => {
    renderDialog();
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Save credentials' }));

    expect(await within(dialog).findByText('Please fill in all required fields.')).toBeTruthy();
    expect(within(dialog).getByText('API token is required')).toBeTruthy();
    expect(toolsetsApi.authenticateAgentToolset).not.toHaveBeenCalled();
  });

  it('stores the credentials for this agent and closes', async () => {
    const { onClose, onNotify } = renderDialog();
    const dialog = await credentialsDialog();

    fireEvent.change(within(dialog).getByPlaceholderText('Paste the API token'), { target: { value: 'tok-123' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save credentials' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toolsetsApi.authenticateAgentToolset).toHaveBeenCalledWith('agent-1', 'jira-instance-1', { apiToken: 'tok-123' });
    expect(onNotify).toHaveBeenCalledWith('Toolset authentication updated.');
  });

  it('updates, rather than re-creates, credentials that already exist', async () => {
    const { onClose } = renderDialog(toolset({ isAuthenticated: true, auth: { apiToken: 'old-token' } }));
    const dialog = await credentialsDialog();

    const token = within(dialog).getByPlaceholderText('Paste the API token');
    await waitFor(() => expect(token).toHaveProperty('value', 'old-token'));
    fireEvent.change(token, { target: { value: 'new-token' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toolsetsApi.updateAgentToolsetCredentials).toHaveBeenCalledWith('agent-1', 'jira-instance-1', { apiToken: 'new-token' });
    expect(toolsetsApi.authenticateAgentToolset).not.toHaveBeenCalled();
  });

  it('removes stored credentials only after the person confirms', async () => {
    const { onClose } = renderDialog(toolset({ isAuthenticated: true }));
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove credentials' }));
    const confirm = await screen.findByRole('dialog', { name: 'Remove credentials?' });
    expect(within(confirm).getByText('This removes stored credentials for Team Jira on this agent until you configure them again.')).toBeTruthy();
    fireEvent.click(within(confirm).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(toolsetsApi.removeAgentToolsetCredentials).toHaveBeenCalledWith('agent-1', 'jira-instance-1'));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the server's reason when the credentials are rejected, and keeps the dialog open", async () => {
    toolsetsApi.authenticateAgentToolset.mockRejectedValue(
      apiFailure(400, { message: 'Jira rejected this API token. Create a new token and paste it again.' }),
    );
    const { onClose } = renderDialog();
    const dialog = await credentialsDialog();

    fireEvent.change(within(dialog).getByPlaceholderText('Paste the API token'), { target: { value: 'expired' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save credentials' }));

    expect(await within(dialog).findByText('Jira rejected this API token. Create a new token and paste it again.')).toBeTruthy();
    expect(within(dialog).queryByText('Request failed')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows the server's reason when removing credentials fails", async () => {
    toolsetsApi.removeAgentToolsetCredentials.mockRejectedValue(
      apiFailure(403, { message: 'Only the agent owner can remove its credentials.' }),
    );
    renderDialog(toolset({ isAuthenticated: true }));
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove credentials' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Remove credentials?' })).getByRole('button', { name: 'Remove' }));

    expect(await within(dialog).findByText('Only the agent owner can remove its credentials.')).toBeTruthy();
  });

  it('says what to try when a failure carries no reason a person can use', async () => {
    toolsetsApi.authenticateAgentToolset.mockRejectedValue(apiFailure(500, { message: 'Traceback (most recent call last): ...' }));
    renderDialog();
    const dialog = await credentialsDialog();

    fireEvent.change(within(dialog).getByPlaceholderText('Paste the API token'), { target: { value: 'tok' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save credentials' }));

    expect(await within(dialog).findByText('An error occurred. Please try again.')).toBeTruthy();
    expect(within(dialog).queryByText(/Traceback/)).toBeNull();
  });

  it('says so when the toolset needs no credentials', async () => {
    renderDialog(toolset({ authType: 'NONE', isAuthenticated: false }));
    const dialog = await credentialsDialog();

    expect(within(dialog).getByText('No credentials are required for this toolset.')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Save credentials' })).toBeNull();
  });

  it('points at the registry schema when it returns no fields to fill', async () => {
    toolsetsApi.getToolsetRegistrySchema.mockResolvedValue({ toolset: { config: { auth: { schemas: {} } } } });
    renderDialog();
    const dialog = await credentialsDialog();

    expect(within(dialog).getByText(/No credential fields were returned for this auth type/)).toBeTruthy();
  });
});

describe('AgentToolsetCredentialsDialog — OAuth toolsets', () => {
  const oauthToolset = () => toolset({ authType: 'OAUTH', isAuthenticated: false });

  function fakePopup() {
    return { closed: false, close: vi.fn(), focus: vi.fn() } as unknown as Window;
  }

  it('signs in through a popup and closes once the agent shows as connected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    toolsetsApi.getAgentToolsetOAuthUrl.mockResolvedValue({ success: true, authorizationUrl: 'https://auth.example.com/authorize' });
    toolsetsApi.findAgentToolsetByInstanceId.mockResolvedValue({ isAuthenticated: true });
    const popup = fakePopup();
    const open = vi.spyOn(window, 'open').mockReturnValue(popup);
    const { onClose, onSuccess } = renderDialog(oauthToolset());
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));
    await waitFor(() => expect(open).toHaveBeenCalledWith('https://auth.example.com/authorize', 'oauth_agent_toolset', expect.any(String)));
    expect(within(dialog).getByRole('button', { name: 'Waiting for OAuth…' })).toHaveProperty('disabled', true);

    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', { data: { type: 'oauth-success' }, origin: window.location.origin }));
      await vi.advanceTimersByTimeAsync(2000);
    });

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(toolsetsApi.findAgentToolsetByInstanceId).toHaveBeenCalledWith('agent-1', 'jira-instance-1');
    expect(onSuccess).toHaveBeenCalled();
  });

  it('shows the error the sign-in window reported', async () => {
    toolsetsApi.getAgentToolsetOAuthUrl.mockResolvedValue({ success: true, authorizationUrl: 'https://auth.example.com/authorize' });
    vi.spyOn(window, 'open').mockReturnValue(fakePopup());
    renderDialog(oauthToolset());
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));
    await within(dialog).findByRole('button', { name: 'Waiting for OAuth…' });
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: 'oauth-error', error: 'Jira denied access. Ask your Jira admin to allow this app.' },
          origin: window.location.origin,
        }),
      );
    });

    expect(await within(dialog).findByText('Jira denied access. Ask your Jira admin to allow this app.')).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' })).toHaveProperty('disabled', false);
  });

  it("shows the server's reason when the sign-in link cannot be fetched", async () => {
    toolsetsApi.getAgentToolsetOAuthUrl.mockRejectedValue(
      apiFailure(404, { message: 'No OAuth app is set up for Jira. Ask an admin to add one under Actions.' }),
    );
    renderDialog(oauthToolset());
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));

    expect(await within(dialog).findByText('No OAuth app is set up for Jira. Ask an admin to add one under Actions.')).toBeTruthy();
  });

  it('tells the person to allow popups when the browser blocks the sign-in window', async () => {
    toolsetsApi.getAgentToolsetOAuthUrl.mockResolvedValue({ success: true, authorizationUrl: 'https://auth.example.com/authorize' });
    vi.spyOn(window, 'open').mockReturnValue(null);
    renderDialog(oauthToolset());
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));

    expect(await within(dialog).findByText('Popup blocked. Allow popups for this site and try again.')).toBeTruthy();
  });

  it('says sign-in did not finish when the window closes before the agent is connected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    toolsetsApi.getAgentToolsetOAuthUrl.mockResolvedValue({ success: true, authorizationUrl: 'https://auth.example.com/authorize' });
    toolsetsApi.findAgentToolsetByInstanceId.mockResolvedValue({ isAuthenticated: false });
    const popup = fakePopup();
    vi.spyOn(window, 'open').mockReturnValue(popup);
    const { onClose } = renderDialog(oauthToolset());
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));
    await within(dialog).findByRole('button', { name: 'Waiting for OAuth…' });
    (popup as { closed: boolean }).closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000 + 5 * 1500 + 100);
    });

    expect(await within(dialog).findByText(/Sign-in did not finish/)).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('disconnects a connected agent only after the person confirms', async () => {
    const { onClose } = renderDialog(toolset({ authType: 'OAUTH', isAuthenticated: true }));
    const dialog = await credentialsDialog();

    expect(within(dialog).getByText(/OAuth is connected for this agent/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Disconnect' }));
    const confirm = await screen.findByRole('dialog', { name: 'Disconnect Team Jira?' });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Disconnect' }));

    await waitFor(() => expect(toolsetsApi.reauthenticateAgentToolset).toHaveBeenCalledWith('agent-1', 'jira-instance-1'));
    expect(onClose).toHaveBeenCalled();
  });

  it('cancels a sign-in in progress when the person closes the dialog', async () => {
    toolsetsApi.getAgentToolsetOAuthUrl.mockResolvedValue({ success: true, authorizationUrl: 'https://auth.example.com/authorize' });
    const popup = fakePopup();
    vi.spyOn(window, 'open').mockReturnValue(popup);
    const { onClose, onNotify } = renderDialog(oauthToolset());
    const dialog = await credentialsDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Authenticate with OAuth' }));
    await within(dialog).findByRole('button', { name: 'Waiting for OAuth…' });
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[0]);

    expect(popup.close).toHaveBeenCalled();
    expect(onNotify).toHaveBeenCalledWith('Sign-in was cancelled.');
    expect(onClose).toHaveBeenCalled();
  });
});
