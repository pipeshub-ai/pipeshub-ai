import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const listOAuthConfigs = vi.fn();
const getOAuthConfig = vi.fn();

vi.mock('../../../api', () => ({
  ConnectorsApi: {
    listOAuthConfigs: (...args: unknown[]) => listOAuthConfigs(...args),
    getOAuthConfig: (...args: unknown[]) => getOAuthConfig(...args),
  },
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

import { OAuthAppSelector } from '../oauth-app-selector';
import { useConnectorsStore } from '../../../store';
import type { AuthSchemaField } from '../../../types';
import {
  installDomShims,
  renderInTheme,
  signInAs,
  makeConnector,
  makeSchema,
} from '../../../__tests__/fixtures';

const oauthFields: AuthSchemaField[] = [
  { name: 'clientId', displayName: 'Client ID', fieldType: 'TEXT', required: true },
  { name: 'clientSecret', displayName: 'Client secret', fieldType: 'PASSWORD', required: true },
];

const auth = () => useConnectorsStore.getState().formData.auth;

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  listOAuthConfigs
    .mockReset()
    .mockResolvedValue({ oauthConfigs: [{ _id: 'mine', oauthInstanceName: 'My app', personal: true }] });
  getOAuthConfig.mockReset();
  signInAs('member');
});
afterEach(() => cleanup());

describe('OAuthAppSelector: picking a saved app', () => {
  it('drops a client typed for a new app, so it is not sent with the saved one', async () => {
    const store = useConnectorsStore.getState();
    store.openPanel(makeConnector({ authType: 'OAUTH' }), undefined, 'personal');
    store.setSchemaAndConfig(makeSchema({ OAUTH: oauthFields }), null);
    store.setSelectedAuthType('OAUTH');
    store.setAuthFormValue('clientId', 'typed-client');
    store.setAuthFormValue('clientSecret', 'typed-secret');
    renderInTheme(<OAuthAppSelector />);

    const trigger = await screen.findByRole('combobox');
    await waitFor(() => expect(listOAuthConfigs).toHaveBeenCalled());
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.click(await screen.findByRole('option', { name: /My app/ }));

    expect(auth().oauthConfigId).toBe('mine');
    expect(auth().clientId).toBe('');
    expect(auth().clientSecret).toBe('');
  });
});
