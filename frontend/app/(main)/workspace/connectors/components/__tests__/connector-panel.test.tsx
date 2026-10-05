import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({
  getConnectorSchema: vi.fn(),
  getConnectorConfig: vi.fn(),
  createConnectorInstance: vi.fn(),
  saveAuthConfig: vi.fn(),
  saveFiltersSyncConfig: vi.fn(),
  toggleConnector: vi.fn(),
  getOAuthAuthorizationUrl: vi.fn(),
  listOAuthConfigs: vi.fn(),
  getOAuthConfig: vi.fn(),
  getFilterFieldOptions: vi.fn(),
}));
vi.mock('../../api', () => ({ ConnectorsApi: api }));

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));

vi.mock('@/config', async () => {
  const { OAuthAppSelector } = await import('../authenticate-tab/oauth-app-selector');
  return { OAuthAppSelector, PermissionLockIcon: () => null };
});
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({ ConnectorIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: ({ label }: { label?: string }) => <div role="status">{label}</div>,
}));
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import { ConnectorPanel } from '../connector-panel';
import { useConnectorsStore } from '../../store';
import type { AuthSchemaField } from '../../types';
import {
  installDomShims,
  renderInTheme,
  signInAs,
  inputByLabel,
  makeConnector,
  makeSchema,
  makeConfig,
  makeInstance,
  toasts,
  clearToasts,
} from '../../__tests__/fixtures';

const SECRET = 'tok_live_do_not_log';

const nextButton = () => screen.getByRole('button', { name: 'Next →' });
const saveButton = () => screen.getByRole('button', { name: 'Save Configuration' });

function startCreate(connector = makeConnector()) {
  useConnectorsStore.getState().openPanel(connector, undefined, 'team');
  return renderInTheme(<ConnectorPanel />);
}

async function startEdit(overrides: Parameters<typeof makeInstance>[0] = {}) {
  const instance = makeInstance(overrides);
  useConnectorsStore.getState().openPanel(instance, instance._key, 'team');
  const view = renderInTheme(<ConnectorPanel />);
  await waitFor(() => expect(screen.queryByText('Loading configuration…')).toBeNull());
  return view;
}

function fillCredentials(values: { name?: string; url?: string; email?: string; token?: string }) {
  if (values.name !== undefined) {
    fireEvent.change(screen.getByPlaceholderText('e.g. Production Slack'), {
      target: { value: values.name },
    });
  }
  if (values.url !== undefined) {
    const url = inputByLabel('Site URL');
    fireEvent.change(url, { target: { value: values.url } });
    fireEvent.blur(url);
  }
  if (values.email !== undefined) {
    fireEvent.change(inputByLabel('Account email'), { target: { value: values.email } });
  }
  if (values.token !== undefined) {
    fireEvent.change(inputByLabel('API token'), { target: { value: values.token } });
  }
}

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  clearToasts();
  signInAs('admin');
  routerPush.mockReset();
  for (const fn of Object.values(api)) fn.mockReset();
  api.getConnectorSchema.mockResolvedValue({ success: true, schema: makeSchema() });
  api.getConnectorConfig.mockResolvedValue(makeConfig());
  api.createConnectorInstance.mockResolvedValue({ connector: { connectorId: 'new-1' } });
  api.saveAuthConfig.mockResolvedValue({});
  api.saveFiltersSyncConfig.mockResolvedValue({});
  api.toggleConnector.mockResolvedValue({});
  api.listOAuthConfigs.mockResolvedValue({ oauthConfigs: [] });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ConnectorPanel: opening', () => {
  it('shows a loading state until the connector schema arrives', async () => {
    let resolveSchema: (v: unknown) => void = () => {};
    api.getConnectorSchema.mockReturnValue(new Promise((r) => (resolveSchema = r)));
    startCreate();

    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Loading configuration…');
    expect(screen.getAllByText('Jira Configuration').length).toBeGreaterThan(0);

    resolveSchema({ success: true, schema: makeSchema() });
    expect(await screen.findByText('API Token Credentials')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
    expect(api.getConnectorSchema).toHaveBeenCalledWith('Jira');
  });

  it('stops loading when the schema cannot be fetched', async () => {
    api.getConnectorSchema.mockRejectedValue({ type: 'SERVER_ERROR', message: 'down' });
    startCreate();

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(nextButton()).toBeTruthy();
  });

  it('keeps Configure locked while a new instance has no credentials yet', async () => {
    startCreate();
    await screen.findByText('API Token Credentials');

    const configureTab = screen.getByRole('tab', { name: /Configure Records/ });
    expect(configureTab.hasAttribute('disabled')).toBe(true);
  });

  it('closes when the user cancels', async () => {
    startCreate();
    await screen.findByText('API Token Credentials');

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(useConnectorsStore.getState().isPanelOpen).toBe(false);
    expect(screen.queryAllByText('Jira Configuration')).toEqual([]);
  });
});

describe('ConnectorPanel: validating credentials', () => {
  it("suggests the connector's name as the instance name", async () => {
    startCreate();
    await screen.findByText('API Token Credentials');
    expect((screen.getByPlaceholderText('e.g. Production Slack') as HTMLInputElement).value).toBe('Jira');
  });

  it('asks for an instance name before anything else', async () => {
    startCreate();
    await screen.findByText('API Token Credentials');
    fillCredentials({ name: '   ' });

    fireEvent.click(nextButton());

    expect(await screen.findByText('Enter an instance name.')).toBeTruthy();
    expect(api.createConnectorInstance).not.toHaveBeenCalled();
  });

  it('names every missing required credential', async () => {
    startCreate();
    await screen.findByText('API Token Credentials');
    fillCredentials({ name: 'Jira Eng' });

    fireEvent.click(nextButton());

    expect(await screen.findByText('Site URL is required')).toBeTruthy();
    expect(screen.getByText('Account email is required')).toBeTruthy();
    expect(screen.getByText('API token is required')).toBeTruthy();
    expect(api.createConnectorInstance).not.toHaveBeenCalled();
  });

  it('rejects a site address that is not a web URL', async () => {
    startCreate();
    await screen.findByText('API Token Credentials');
    fillCredentials({ name: 'Jira Eng', url: 'ftp://files.acme.com', email: 'a@b.co', token: SECRET });

    fireEvent.click(nextButton());

    expect(await screen.findByText('Site URL must use http or https')).toBeTruthy();
    expect(api.createConnectorInstance).not.toHaveBeenCalled();
  });

  it('treats whitespace as an empty credential', async () => {
    startCreate();
    await screen.findByText('API Token Credentials');
    fillCredentials({ name: 'Jira Eng', url: 'acme.atlassian.net', email: 'a@b.co', token: '   ' });

    fireEvent.click(nextButton());

    expect(await screen.findByText('API token is required')).toBeTruthy();
  });
});

describe('ConnectorPanel: creating an instance', () => {
  it('sends trimmed credentials, confirms, and moves on to Configure', async () => {
    api.getConnectorConfig.mockResolvedValue(makeConfig({ name: 'Jira Eng' }));
    startCreate();
    await screen.findByText('API Token Credentials');
    fillCredentials({
      name: '  Jira Eng ',
      url: 'acme.atlassian.net',
      email: ' ops@acme.com ',
      token: ` ${SECRET} `,
    });

    fireEvent.click(nextButton());

    await waitFor(() => expect(api.createConnectorInstance).toHaveBeenCalledTimes(1));
    expect(api.createConnectorInstance).toHaveBeenCalledWith({
      connectorType: 'Jira',
      instanceName: 'Jira Eng',
      scope: 'team',
      authType: 'API_TOKEN',
      config: {
        auth: {
          baseUrl: 'https://acme.atlassian.net',
          email: 'ops@acme.com',
          apiToken: SECRET,
          connectorScope: 'team',
        },
      },
      baseUrl: window.location.origin,
    });
    await waitFor(() =>
      expect(toasts().map((t) => t.title)).toContain(
        "Connector instance 'Jira Eng' created successfully"
      )
    );
    expect(api.getConnectorConfig).toHaveBeenCalledWith('new-1');
    await waitFor(() => expect(useConnectorsStore.getState().panelActiveTab).toBe('configure'));
    expect(screen.getByRole('tab', { name: /Configure Records/ }).getAttribute('aria-selected')).toBe(
      'true'
    );
  });

  it('never writes the secret to the console or the page text during the flow', async () => {
    const spies = (['log', 'info', 'debug', 'warn', 'error'] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {})
    );
    startCreate();
    await screen.findByText('API Token Credentials');
    fillCredentials({ name: 'Jira Eng', url: 'acme.atlassian.net', email: 'a@b.co', token: SECRET });

    fireEvent.click(nextButton());
    await waitFor(() => expect(useConnectorsStore.getState().panelActiveTab).toBe('configure'));

    const logged = spies.flatMap((s) => s.mock.calls).map((args) => {
      try {
        return JSON.stringify(args);
      } catch {
        return String(args);
      }
    });
    expect(logged.filter((line) => line.includes(SECRET))).toEqual([]);
    expect(document.body.textContent ?? '').not.toContain(SECRET);
  });

  it('stays on the credentials step and lets the user retry when creating fails', async () => {
    api.createConnectorInstance.mockRejectedValue({
      type: 'CONFLICT',
      message: 'An instance with this name already exists.',
      statusCode: 409,
    });
    startCreate();
    await screen.findByText('API Token Credentials');
    fillCredentials({ name: 'Jira Eng', url: 'acme.atlassian.net', email: 'a@b.co', token: SECRET });

    fireEvent.click(nextButton());

    await waitFor(() => expect(api.createConnectorInstance).toHaveBeenCalled());
    await waitFor(() => expect(nextButton().hasAttribute('disabled')).toBe(false));
    expect(useConnectorsStore.getState().panelActiveTab).toBe('authenticate');
    expect(useConnectorsStore.getState().panelConnectorId).toBeNull();
    expect(toasts().map((t) => t.title)).not.toContain(
      "Connector instance 'Jira Eng' created successfully"
    );
    expect(inputByLabel('API token').value).toBe(SECRET);
  });

  it('moves an OAuth instance on to Authorize after it is created', async () => {
    const oauthFields: AuthSchemaField[] = [
      { name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true },
      { name: 'clientSecret', displayName: 'Client secret', fieldType: 'PASSWORD', required: true },
    ];
    api.getConnectorSchema.mockResolvedValue({ success: true, schema: makeSchema({ OAUTH: oauthFields }) });
    api.getConnectorConfig.mockResolvedValue(makeConfig({ authType: 'OAUTH', isAuthenticated: false }));
    startCreate();
    await screen.findByText('OAuth 2.0 Credentials');
    await waitFor(() => expect(screen.queryByText('Loading OAuth configurations…')).toBeNull());
    fillCredentials({ name: 'Jira Eng' });
    fireEvent.change(inputByLabel('Client ID'), { target: { value: 'cid' } });
    fireEvent.change(inputByLabel('Client secret'), { target: { value: SECRET } });

    fireEvent.click(nextButton());

    await waitFor(() => expect(useConnectorsStore.getState().panelActiveTab).toBe('authorize'));
    expect(api.createConnectorInstance.mock.calls[0][0]).toMatchObject({
      authType: 'OAUTH',
      config: { auth: { clientId: 'cid', clientSecret: SECRET, oauthInstanceName: 'Jira' } },
    });
    expect(await screen.findByRole('button', { name: 'Authenticate Jira to Proceed' })).toBeTruthy();
  });
});

describe('ConnectorPanel: members and OAuth apps', () => {
  const oauthOnly = () =>
    makeSchema({
      OAUTH: [{ name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true }],
    });

  it('requires a member to pick an OAuth app before continuing', async () => {
    signInAs('member');
    api.getConnectorSchema.mockResolvedValue({ success: true, schema: oauthOnly() });
    api.listOAuthConfigs.mockResolvedValue({
      oauthConfigs: [{ _id: 'app-1', oauthInstanceName: 'Acme Jira' }],
    });
    startCreate();
    await screen.findByText('Select an OAuth app (required)…');
    fillCredentials({ name: 'My Jira' });

    fireEvent.click(nextButton());

    expect(await screen.findByText('Please select an OAuth app.')).toBeTruthy();
    expect(api.createConnectorInstance).not.toHaveBeenCalled();
  });

  it('tells a member to ask an admin when no OAuth app exists for a personal connector', async () => {
    signInAs('member');
    api.getConnectorSchema.mockResolvedValue({ success: true, schema: oauthOnly() });
    useConnectorsStore.getState().openPanel(makeConnector({ scope: 'personal' }), undefined, 'personal');
    renderInTheme(<ConnectorPanel />);
    await screen.findByText(/No OAuth apps are registered yet/);
    fillCredentials({ name: 'My Jira' });

    fireEvent.click(nextButton());

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'warning',
      title: 'No OAuth apps are available for this connector',
      description: 'Ask your workspace administrator to create an OAuth app for Jira first, then try again.',
    });
    expect(api.createConnectorInstance).not.toHaveBeenCalled();
  });
});

describe('ConnectorPanel: editing saved credentials', () => {
  it('saves changed credentials for an existing instance and moves on to Configure', async () => {
    api.getConnectorConfig.mockResolvedValue(
      makeConfig({
        config: {
          auth: { values: { baseUrl: 'https://acme.atlassian.net', email: 'a@b.co', apiToken: 'old' } },
          sync: {},
          filters: {},
        },
      })
    );
    await startEdit({ isAuthenticated: false, isConfigured: false });
    expect(useConnectorsStore.getState().panelActiveTab).toBe('authenticate');

    fillCredentials({ token: 'new-token' });
    fireEvent.click(nextButton());

    await waitFor(() => expect(api.saveAuthConfig).toHaveBeenCalledTimes(1));
    expect(api.saveAuthConfig).toHaveBeenCalledWith('conn-1', {
      auth: expect.objectContaining({ apiToken: 'new-token', connectorScope: 'team' }),
      baseUrl: window.location.origin,
    });
    await waitFor(() => expect(useConnectorsStore.getState().panelActiveTab).toBe('configure'));
  });

  it('stays on Authenticate when saving credentials fails', async () => {
    api.getConnectorConfig.mockResolvedValue(
      makeConfig({
        config: {
          auth: { values: { baseUrl: 'https://acme.atlassian.net', email: 'a@b.co', apiToken: 'old' } },
          sync: {},
          filters: {},
        },
      })
    );
    api.saveAuthConfig.mockRejectedValue({ type: 'VALIDATION_ERROR', message: 'Token rejected by Jira.' });
    await startEdit({ isAuthenticated: false, isConfigured: false });

    fireEvent.click(nextButton());

    await waitFor(() => expect(api.saveAuthConfig).toHaveBeenCalled());
    await waitFor(() => expect(nextButton().hasAttribute('disabled')).toBe(false));
    expect(useConnectorsStore.getState().panelActiveTab).toBe('authenticate');
  });
});

describe('ConnectorPanel: saving the sync configuration', () => {
  it('confirms a wide sync, saves, closes and shows the success step', async () => {
    await startEdit();
    expect(useConnectorsStore.getState().panelActiveTab).toBe('configure');

    fireEvent.click(saveButton());

    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('Start sync process?')).toBeTruthy();
    expect(within(dialog).getByText(/could sync a large number of records/)).toBeTruthy();
    expect(api.saveFiltersSyncConfig).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(api.saveFiltersSyncConfig).toHaveBeenCalledTimes(1));
    expect(api.saveFiltersSyncConfig).toHaveBeenCalledWith('conn-1', {
      sync: { selectedStrategy: 'MANUAL', customValues: {} },
      filters: { sync: { values: {} }, indexing: { values: {} } },
      baseUrl: window.location.origin,
    });
    await waitFor(() =>
      expect(routerPush).toHaveBeenCalledWith('/workspace/connectors/team/?connectorType=Jira')
    );
    const s = useConnectorsStore.getState();
    expect(s.isPanelOpen).toBe(false);
    expect(s.showConfigSuccessDialog).toBe(true);
    expect(s.newlyConfiguredConnectorId).toBe('conn-1');
  });

  it('does not save when the user backs out of the confirmation', async () => {
    await startEdit();
    fireEvent.click(saveButton());
    const dialog = await screen.findByRole('alertdialog');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Start sync process?')).toBeNull());
    expect(api.saveFiltersSyncConfig).not.toHaveBeenCalled();
    expect(useConnectorsStore.getState().isPanelOpen).toBe(true);
  });

  it('disables an enabled connector first, then saves', async () => {
    await startEdit({ isActive: true });

    fireEvent.click(saveButton());

    expect(await screen.findByText('Connector is currently enabled')).toBeTruthy();
    expect(
      screen.getByText(
        '"Jira (Engineering)" is currently enabled. To save configuration changes, it will be automatically disabled first. Do you want to proceed?'
      )
    ).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Disable & Proceed' }));

    await waitFor(() => expect(api.toggleConnector).toHaveBeenCalledWith('conn-1', 'sync'));
    await waitFor(() => expect(api.saveFiltersSyncConfig).toHaveBeenCalledTimes(1));
    expect(api.toggleConnector.mock.invocationCallOrder[0]).toBeLessThan(
      api.saveFiltersSyncConfig.mock.invocationCallOrder[0]
    );
  });

  it('keeps the panel open with its changes when saving fails', async () => {
    api.saveFiltersSyncConfig.mockRejectedValue({ type: 'SERVER_ERROR', message: 'Could not reach the sync service.' });
    await startEdit();
    fireEvent.click(saveButton());
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(api.saveFiltersSyncConfig).toHaveBeenCalled());
    await waitFor(() => expect(saveButton().hasAttribute('disabled')).toBe(false));
    expect(useConnectorsStore.getState().isPanelOpen).toBe(true);
    expect(routerPush).not.toHaveBeenCalled();
  });
});

describe('ConnectorPanel: OAuth instance not yet signed in', () => {
  it('opens on Authorize and keeps Configure and Continue locked', async () => {
    api.getConnectorSchema.mockResolvedValue({
      success: true,
      schema: makeSchema({
        OAUTH: [{ name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true }],
      }),
    });
    api.getConnectorConfig.mockResolvedValue(makeConfig({ authType: 'OAUTH', isAuthenticated: false }));
    await startEdit({ authType: 'OAUTH', isAuthenticated: false });

    expect(useConnectorsStore.getState().panelActiveTab).toBe('authorize');
    expect(screen.getByText('Sign in with your provider')).toBeTruthy();
    expect(screen.getByRole('tab', { name: /Configure Records/ }).hasAttribute('disabled')).toBe(true);
    expect(
      screen.getByRole('button', { name: 'Continue to configuration →' }).hasAttribute('disabled')
    ).toBe(true);
  });
});
