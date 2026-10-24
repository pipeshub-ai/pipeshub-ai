import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, act, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const listOAuthConfigs = vi.fn();
const getOAuthConfig = vi.fn();

vi.mock('../../../api', () => ({
  ConnectorsApi: {
    listOAuthConfigs: (...args: unknown[]) => listOAuthConfigs(...args),
    getOAuthConfig: (...args: unknown[]) => getOAuthConfig(...args),
  },
}));

// The deployment allows reveal; every request counts as current for the hook, so these
// tests exercise the selector's own panel check.
vi.mock('@/lib/hooks/use-secret-reveal-available', () => ({
  REVEAL_PARAMS: { reveal: 'true' },
  useSecretRevealAvailable: () => true,
  useRevealScope: () => () => () => true,
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
// jsdom has no matchMedia, which the reveal button's mobile check reads.
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

import { OAuthAppSelector } from '../oauth-app-selector';
import { useConnectorsStore } from '../../../store';
import { SERVICE_SECRET_PLACEHOLDER } from '@/lib/constants/config-secret-placeholder';
import type { AuthSchemaField } from '../../../types';
import {
  installDomShims,
  renderInTheme,
  signInAs,
  makeConnector,
  makeSchema,
  makeConfig,
} from '../../../__tests__/fixtures';

const oauthFields: AuthSchemaField[] = [
  { name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true },
  { name: 'clientSecret', displayName: 'Client secret', fieldType: 'PASSWORD', required: true },
];

const APPS = [
  { _id: 'app-1', oauthInstanceName: 'Jira A', config: { clientId: 'id-1', clientSecret: SERVICE_SECRET_PLACEHOLDER } },
  { _id: 'app-2', oauthInstanceName: 'Jira B', config: { clientId: 'id-2', clientSecret: SERVICE_SECRET_PLACEHOLDER } },
];

function openLinkedTo(appId: string, connectorKey = 'conn-1') {
  const store = useConnectorsStore.getState();
  store.openPanel(makeConnector({ _key: connectorKey, authType: 'OAUTH' }), connectorKey, 'team');
  store.setSchemaAndConfig(
    makeSchema({ OAUTH: oauthFields }),
    makeConfig({ authType: 'OAUTH', config: { auth: { oauthConfigId: appId }, sync: {}, filters: {} } }),
  );
  store.setSelectedAuthType('OAUTH');
  store.setAuthFormValue('oauthConfigId', appId);
}

const auth = () => useConnectorsStore.getState().formData.auth;

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  listOAuthConfigs.mockReset().mockResolvedValue({ oauthConfigs: APPS });
  getOAuthConfig.mockReset();
  signInAs('admin');
});
afterEach(() => cleanup());

describe('OAuthAppSelector: revealing stored credentials', () => {
  it('fills the masked secret with the stored one', async () => {
    openLinkedTo('app-1');
    getOAuthConfig.mockResolvedValue({ config: { clientId: 'id-1', clientSecret: 'secret-1' } });
    renderInTheme(<OAuthAppSelector />);

    fireEvent.click(await screen.findByRole('button', { name: /Show stored values/ }));

    await waitFor(() => expect(auth().clientSecret).toBe('secret-1'));
    expect(getOAuthConfig).toHaveBeenCalledWith('Jira', 'app-1', { reveal: true });
  });

  it('drops a response that arrives after the panel moved to another connector', async () => {
    openLinkedTo('app-1');
    let resolve: (v: unknown) => void = () => {};
    getOAuthConfig.mockReturnValue(new Promise((r) => (resolve = r)));
    renderInTheme(<OAuthAppSelector />);

    fireEvent.click(await screen.findByRole('button', { name: /Show stored values/ }));
    act(() => {
      useConnectorsStore.getState().closePanel();
    });
    openLinkedTo('app-1', 'conn-2');
    useConnectorsStore.getState().setAuthFormValue('clientSecret', SERVICE_SECRET_PLACEHOLDER);

    await act(async () => resolve({ config: { clientSecret: 'secret-1' } }));

    expect(auth().clientSecret).toBe(SERVICE_SECRET_PLACEHOLDER);
    expect(useConnectorsStore.getState().revealedOAuthAppId).toBe('');
  });

  it('offers the reveal again after switching away from a revealed app and back', async () => {
    openLinkedTo('app-1');
    getOAuthConfig.mockResolvedValue({ config: { clientId: 'id-1', clientSecret: 'secret-1' } });
    renderInTheme(<OAuthAppSelector />);

    fireEvent.click(await screen.findByRole('button', { name: /Show stored values/ }));
    await waitFor(() => expect(useConnectorsStore.getState().revealedOAuthAppId).toBe('app-1'));

    act(() => {
      useConnectorsStore.getState().setAuthFormValue('oauthConfigId', 'app-2');
    });
    await waitFor(() => expect(useConnectorsStore.getState().revealedOAuthAppId).toBe(''));

    act(() => {
      const store = useConnectorsStore.getState();
      store.setAuthFormValue('oauthConfigId', 'app-1');
      store.setAuthFormValue('clientSecret', SERVICE_SECRET_PLACEHOLDER);
    });
    expect(await screen.findByRole('button', { name: /Show stored values/ })).toBeTruthy();
  });
});
