import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';

type PopupOptions = { verifyAuthenticated: () => Promise<boolean>; onVerified: () => void; onIncomplete: () => void };
type BeginHandlers = { onTimeout: () => void; onOpenError: (e: unknown) => void };

const popup = vi.hoisted(() => ({
  begin: vi.fn(),
  stop: vi.fn(),
  authenticating: false,
  options: null as null | PopupOptions,
}));
const api = vi.hoisted(() => ({
  getInstanceOAuthAuthorizationUrl: vi.fn(async () => ({ success: true, authorizationUrl: 'https://auth.example/start' })),
  findMyToolsetByInstanceId: vi.fn(async () => ({ isAuthenticated: true })),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/lib/store/toast-store', () => ({ toast }));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: api }));
vi.mock('@/app/(main)/agents/agent-builder/hooks/use-toolset-oauth-popup-flow', () => ({
  useToolsetOauthPopupFlow: (options: PopupOptions) => {
    popup.options = options;
    return { authenticating: popup.authenticating, beginOAuth: popup.begin, stopOAuthUi: popup.stop };
  },
}));
// The real dialog posts the credentials; here "Save" stands for credentials that were accepted.
vi.mock('@/app/(main)/agents/agent-builder/components/user-toolset-config-dialog', () => ({
  UserToolsetConfigDialog: ({ toolset, onSuccess }: { toolset: BuilderSidebarToolset; onSuccess: () => void }) => (
    <div role="dialog" aria-label={`Sign in to ${toolset.instanceName}`}>
      <button type="button" onClick={onSuccess}>
        Save
      </button>
    </div>
  ),
}));

import { ToolsetConnectRows, actionsToConnect } from '../toolset-connect-rows';

function toolset(overrides: Partial<BuilderSidebarToolset> = {}): BuilderSidebarToolset {
  return {
    name: 'jira',
    normalized_name: 'jira',
    displayName: 'Jira',
    description: '',
    iconPath: '',
    category: 'app',
    toolCount: 2,
    tools: [],
    isConfigured: true,
    isAuthenticated: false,
    isFromRegistry: false,
    instanceId: 'inst-1',
    instanceName: 'Jira',
    toolsetType: 'jira',
    authType: 'OAUTH',
    ...overrides,
  };
}

const gmail = toolset({ instanceId: 'gm', instanceName: 'Team Gmail', toolsetType: 'gmail', authType: 'OAUTH' });
const jira = toolset({ instanceId: 'jr', instanceName: 'Jira Cloud', authType: 'API_TOKEN' });

beforeEach(() => {
  installBrowserShims();
  popup.authenticating = false;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('actionsToConnect', () => {
  it('lists only the instances a sign-in keeps out of the chat', () => {
    const signedIn = toolset({ instanceId: 'done', isAuthenticated: true });
    const nothingToSignIn = toolset({ instanceId: 'calc', authType: 'NONE' });
    const notSetUp = toolset({ instanceId: undefined, isFromRegistry: true });
    const listed = toolset({ instanceId: 'listed' });

    const rows = actionsToConnect([gmail, signedIn, nothingToSignIn, notSetUp, listed, jira], new Set(['listed']));

    expect(rows.map((ts) => ts.instanceId)).toEqual(['gm', 'jr']);
  });
});

describe('ToolsetConnectRows', () => {
  function renderRows(toolsets: BuilderSidebarToolset[]) {
    const onConnected = vi.fn();
    const view = renderInTheme(
      <ToolsetConnectRows toolsets={toolsets} rowStyle={{}} renderIcon={() => null} onConnected={onConnected} />,
    );
    const row = (name: string) =>
      screen.getAllByTestId('toolset-connect-row').find((r) => r.textContent?.includes(name)) as HTMLElement;
    return { onConnected, row, view };
  }

  it('offers Connect for each action not signed in to', () => {
    const { row } = renderRows([gmail, jira]);

    expect(screen.getByText('Not connected')).toBeTruthy();
    expect(within(row('Team Gmail')).getByRole('button', { name: 'Connect' })).toBeTruthy();
    expect(within(row('Jira Cloud')).getByRole('button', { name: 'Connect' })).toBeTruthy();
  });

  it('signs in to an OAuth action with the popup, and it joins the chat once verified', async () => {
    const { onConnected, row } = renderRows([gmail]);

    fireEvent.click(within(row('Team Gmail')).getByRole('button', { name: 'Connect' }));

    expect(popup.begin).toHaveBeenCalledTimes(1);
    const [windowName, fetchUrl] = popup.begin.mock.calls[0] as [string, () => Promise<string>, BeginHandlers];
    expect(windowName).toBe('oauth_user_toolset');
    await expect(fetchUrl()).resolves.toBe('https://auth.example/start');
    expect(api.getInstanceOAuthAuthorizationUrl).toHaveBeenCalledWith('gm', window.location.origin);

    await expect(popup.options?.verifyAuthenticated()).resolves.toBe(true);
    expect(api.findMyToolsetByInstanceId).toHaveBeenCalledWith('gm');
    popup.options?.onVerified();
    expect(onConnected).toHaveBeenCalledWith('gm');
    expect(toast.success).toHaveBeenCalledWith('Team Gmail is connected');
  });

  it('lets one sign-in run at a time', () => {
    const { row } = renderRows([gmail, toolset({ instanceId: 'cal', instanceName: 'Calendar' })]);
    popup.authenticating = true;
    fireEvent.click(within(row('Team Gmail')).getByRole('button', { name: 'Connect' }));

    expect((within(row('Calendar')).getByRole('button', { name: 'Connect' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('says why when the popup cannot start or the sign-in does not finish', () => {
    const { onConnected, row } = renderRows([gmail]);
    fireEvent.click(within(row('Team Gmail')).getByRole('button', { name: 'Connect' }));
    const handlers = popup.begin.mock.calls[0]![2] as BeginHandlers;

    handlers.onOpenError(new Error('Popup was blocked'));
    expect(toast.error).toHaveBeenCalledWith('Popup was blocked');
    popup.options?.onIncomplete();
    expect(toast.error).toHaveBeenCalledTimes(2);
    expect(onConnected).not.toHaveBeenCalled();
  });

  it('asks for credentials in the dialog for an action that takes them', () => {
    const { onConnected, row } = renderRows([jira]);

    fireEvent.click(within(row('Jira Cloud')).getByRole('button', { name: 'Connect' }));

    expect(popup.begin).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign in to Jira Cloud' })).getByRole('button', { name: 'Save' }));
    expect(onConnected).toHaveBeenCalledWith('jr');
    expect(toast.success).toHaveBeenCalledWith('Jira Cloud is connected');
  });

  it('closes the sign-in popup when the person leaves the tab mid sign-in', () => {
    const { row, view } = renderRows([gmail]);
    fireEvent.click(within(row('Team Gmail')).getByRole('button', { name: 'Connect' }));
    expect(popup.stop).not.toHaveBeenCalled();

    view.unmount();

    expect(popup.stop).toHaveBeenCalled();
  });

  it('shows nothing when every action is connected', () => {
    renderRows([]);
    expect(screen.queryByText('Not connected')).toBeNull();
  });
});
