import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const listOAuthConfigs = vi.fn();
const getOAuthConfig = vi.fn();

vi.mock('../../../api', () => ({
  ConnectorsApi: {
    listOAuthConfigs: (...args: unknown[]) => listOAuthConfigs(...args),
    getOAuthConfig: (...args: unknown[]) => getOAuthConfig(...args),
  },
}));

// The app-wide `@/config` barrel pulls in the agent builder; this tab only needs the selector.
vi.mock('@/config', async () => {
  const { OAuthAppSelector } = await import('../oauth-app-selector');
  return { OAuthAppSelector };
});

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import { AuthenticateTab } from '../index';
import { useConnectorsStore } from '../../../store';
import type { AuthSchemaField } from '../../../types';
import {
  installDomShims,
  renderInTheme,
  signInAs,
  inputByLabel,
  makeConnector,
  makeSchema,
  makeConfig,
  apiTokenFields,
  toasts,
  clearToasts,
} from '../../../__tests__/fixtures';

const oauthFields: AuthSchemaField[] = [
  { name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true },
  { name: 'clientSecret', displayName: 'Client secret', fieldType: 'PASSWORD', required: true },
];

function openCreate(schema = makeSchema(), connector = makeConnector()) {
  const store = useConnectorsStore.getState();
  store.openPanel(connector, undefined, 'team');
  store.setSchemaAndConfig(schema);
}

function openExisting(schema = makeSchema(), connector = makeConnector({ _key: 'conn-1' })) {
  const store = useConnectorsStore.getState();
  store.openPanel(connector, 'conn-1', 'team');
  store.setSchemaAndConfig(schema, makeConfig());
}

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  listOAuthConfigs.mockReset().mockResolvedValue({ oauthConfigs: [] });
  getOAuthConfig.mockReset().mockResolvedValue({});
  clearToasts();
  signInAs('admin');
});
afterEach(() => cleanup());

describe('AuthenticateTab: credential form', () => {
  it('asks for an instance name and the credentials the schema lists', () => {
    openCreate();
    renderInTheme(<AuthenticateTab />);

    expect(screen.getByText('Instance name')).toBeTruthy();
    expect(screen.getByPlaceholderText('e.g. Production Slack')).toBeTruthy();
    expect(screen.getByText('API Token Credentials')).toBeTruthy();
    expect(screen.getByText('Enter your Jira authentication details')).toBeTruthy();
    expect(inputByLabel('Site URL').type).toBe('url');
    expect(inputByLabel('Account email').type).toBe('email');
    expect(inputByLabel('API token').type).toBe('password');
  });

  it('stores what the user types in the form state', () => {
    openCreate();
    renderInTheme(<AuthenticateTab />);

    fireEvent.change(screen.getByPlaceholderText('e.g. Production Slack'), {
      target: { value: 'Jira (Engineering)' },
    });
    fireEvent.change(inputByLabel('API token'), { target: { value: 'tok_123' } });

    const s = useConnectorsStore.getState();
    expect(s.instanceName).toBe('Jira (Engineering)');
    expect(s.formData.auth.apiToken).toBe('tok_123');
  });

  it('shows validation messages the save step put on the form', () => {
    openCreate();
    useConnectorsStore.getState().setInstanceNameError('Enter an instance name.');
    useConnectorsStore.getState().mergeFormErrors({ apiToken: 'API token is required' });
    renderInTheme(<AuthenticateTab />);

    expect(screen.getByText('Enter an instance name.')).toBeTruthy();
    expect(screen.getByPlaceholderText('e.g. Production Slack').getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('API token is required')).toBeTruthy();
    expect(inputByLabel('API token').getAttribute('aria-invalid')).toBe('true');
  });

  it('clears a field error as soon as the user edits that field', () => {
    openCreate();
    useConnectorsStore.getState().mergeFormErrors({ apiToken: 'API token is required' });
    renderInTheme(<AuthenticateTab />);

    fireEvent.change(inputByLabel('API token'), { target: { value: 't' } });
    expect(screen.queryByText('API token is required')).toBeNull();
  });

  it('hides a field whose display rule is off', () => {
    openCreate();
    useConnectorsStore.setState({ conditionalDisplay: { email: false } });
    renderInTheme(<AuthenticateTab />);

    expect(screen.queryByText('Account email')).toBeNull();
    expect(screen.getByText('API token')).toBeTruthy();
  });

  it('offers a choice of method only while creating, when there is more than one', () => {
    const schema = makeSchema({
      API_TOKEN: apiTokenFields,
      USERNAME_PASSWORD: [
        { name: 'username', displayName: 'Username', fieldType: 'TEXT', required: true },
      ],
    });
    openCreate(schema);
    const { unmount } = renderInTheme(<AuthenticateTab />);
    expect(screen.getByText('Authentication method')).toBeTruthy();
    expect(screen.getByRole('combobox')).toBeTruthy();
    unmount();

    openExisting(schema);
    renderInTheme(<AuthenticateTab />);
    expect(screen.queryByText('Authentication method')).toBeNull();
    expect(screen.queryByPlaceholderText('e.g. Production Slack')).toBeNull();
  });

  it('says so when the connector needs no credentials', () => {
    openCreate(makeSchema({ NONE: [] }));
    renderInTheme(<AuthenticateTab />);

    expect(screen.getByText('No authentication required for this connector')).toBeTruthy();
    expect(screen.queryByText(/Credentials$/)).toBeNull();
  });

  it('renders nothing until the schema has loaded', () => {
    useConnectorsStore.getState().openPanel(makeConnector(), undefined, 'team');
    const { container } = renderInTheme(<AuthenticateTab />);
    expect(container.querySelector('[data-ph-field]')).toBeNull();
    expect(screen.queryByText('Instance name')).toBeNull();
  });
});

describe('AuthenticateTab: who can edit credentials', () => {
  it('lets an admin edit the saved credentials of an existing instance', () => {
    openExisting();
    renderInTheme(<AuthenticateTab />);
    expect(inputByLabel('API token').disabled).toBe(false);
  });

  it('locks the saved credentials for a member', () => {
    signInAs('member');
    openExisting();
    renderInTheme(<AuthenticateTab />);

    expect(inputByLabel('API token').disabled).toBe(true);
    expect(inputByLabel('Site URL').disabled).toBe(true);
  });

  it('lets a member fill credentials in when creating their own instance', () => {
    signInAs('member');
    openCreate();
    renderInTheme(<AuthenticateTab />);
    expect(inputByLabel('API token').disabled).toBe(false);
  });

  it('never shows OAuth client credentials to a member', async () => {
    signInAs('member');
    openCreate(makeSchema({ OAUTH: oauthFields }));
    renderInTheme(<AuthenticateTab />);

    expect(
      await screen.findByText(
        'No OAuth apps are registered yet. Ask an administrator to add one in workspace connector settings.'
      )
    ).toBeTruthy();
    expect(screen.queryByText('Client secret')).toBeNull();
    expect(screen.queryByText('Client ID')).toBeNull();
  });
});

describe('AuthenticateTab: OAuth app', () => {
  it('shows progress while saved OAuth apps load', async () => {
    let resolveList: (v: unknown) => void = () => {};
    listOAuthConfigs.mockReturnValue(new Promise((r) => (resolveList = r)));
    openCreate(makeSchema({ OAUTH: oauthFields }));
    renderInTheme(<AuthenticateTab />);

    expect(screen.getByText('Loading OAuth configurations…')).toBeTruthy();
    expect(listOAuthConfigs).toHaveBeenCalledWith('Jira', 1, 100);

    await act(async () => resolveList({ oauthConfigs: [] }));
    expect(screen.queryByText('Loading OAuth configurations…')).toBeNull();
  });

  it('asks an admin to name a new OAuth app, prefilled with the connector type', async () => {
    openCreate(makeSchema({ OAUTH: oauthFields }));
    renderInTheme(<AuthenticateTab />);

    await waitFor(() =>
      expect(useConnectorsStore.getState().formData.auth.oauthInstanceName).toBe('Jira')
    );
    expect(screen.getByText('OAuth app name')).toBeTruthy();
    expect(inputByLabel('Client secret').type).toBe('password');
    expect(
      screen.getByText(/pick one above, or choose Create new OAuth app and enter client credentials/)
    ).toBeTruthy();
  });

  it('explains when the saved OAuth apps could not be loaded', async () => {
    listOAuthConfigs.mockRejectedValue(new Error('boom'));
    openCreate(makeSchema({ OAUTH: oauthFields }));
    renderInTheme(<AuthenticateTab />);

    expect(await screen.findByText('Could not load OAuth apps for this connector.')).toBeTruthy();
  });

  it('shows the OAuth app error the save step raised', async () => {
    signInAs('member');
    listOAuthConfigs.mockResolvedValue({
      oauthConfigs: [{ _id: 'app-1', oauthInstanceName: 'Acme Jira' }],
    });
    openCreate(makeSchema({ OAUTH: oauthFields }));
    useConnectorsStore.getState().mergeFormErrors({ oauthConfigId: 'Please select an OAuth app.' });
    renderInTheme(<AuthenticateTab />);

    expect(await screen.findByText('Please select an OAuth app.')).toBeTruthy();
    expect(screen.getByRole('combobox').getAttribute('data-invalid')).toBe('true');
  });
});

describe('AuthenticateTab: redirect URL', () => {
  const redirectSchema = () =>
    makeSchema({ OAUTH: oauthFields }, { redirectUri: '/connectors/oauth/callback/Jira' });

  it('shows the callback URL to register with the identity provider', () => {
    openCreate(redirectSchema());
    renderInTheme(<AuthenticateTab />);

    expect(screen.getByText('Redirect/Callback URL')).toBeTruthy();
    expect(
      screen.getByText(`${window.location.origin}/connectors/oauth/callback/Jira`)
    ).toBeTruthy();
  });

  it('copies the callback URL and confirms it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    openCreate(redirectSchema());
    renderInTheme(<AuthenticateTab />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy redirect/callback URL' }));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        `${window.location.origin}/connectors/oauth/callback/Jira`
      )
    );
    await waitFor(() =>
      expect(toasts().map((t) => t.title)).toContain('Redirect/Callback URL copied')
    );
  });

  it('tells the user to copy it by hand when the clipboard is blocked', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    openCreate(redirectSchema());
    renderInTheme(<AuthenticateTab />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy redirect/callback URL' }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      title: 'Could not copy',
      description: 'Copy the URL manually or allow clipboard access for this site.',
    });
  });

  it('hides the callback URL when the schema says not to display it', () => {
    openCreate(
      makeSchema(
        { OAUTH: oauthFields },
        { redirectUri: '/connectors/oauth/callback/Jira', displayRedirectUri: false }
      )
    );
    renderInTheme(<AuthenticateTab />);
    expect(screen.queryByText('Redirect/Callback URL')).toBeNull();
  });
});
