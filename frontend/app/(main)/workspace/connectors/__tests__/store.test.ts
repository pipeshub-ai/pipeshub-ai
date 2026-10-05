/**
 * The connectors store: which tab the setup panel opens on, how saved config
 * becomes form state, and how instance lists stay consistent across delete,
 * rename and stale fetches.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useConnectorsStore } from '../store';
import type { Connector, ConnectorConfig, ConnectorInstance, ConnectorSchemaResponse } from '../types';

type Schema = ConnectorSchemaResponse['schema'];

const store = () => useConnectorsStore.getState();

function connector(overrides: Partial<Connector> = {}): Connector {
  return { _key: 'c1', name: 'Team Drive', type: 'Google Drive', authType: 'OAUTH', ...overrides } as Connector;
}

function schema(overrides: Partial<Schema> = {}): Schema {
  return {
    auth: {
      supportedAuthTypes: ['CUSTOM', 'OAUTH'],
      schemas: {
        CUSTOM: {
          fields: [
            { name: 'apiKey', fieldType: 'PASSWORD' },
            { name: 'region', fieldType: 'SELECT', defaultValue: 'eu' },
          ],
        },
        OAUTH: { fields: [{ name: 'clientId', fieldType: 'TEXT' }] },
      },
      conditionalDisplay: {
        regionHelp: { showWhen: { field: 'region', operator: 'equals', value: 'us' } },
      },
    },
    sync: {
      supportedStrategies: ['SCHEDULED', 'MANUAL'],
      customFields: [{ name: 'batchSize', fieldType: 'NUMBER', defaultValue: '50' }],
      scheduledConfig: { intervalMinutes: 60 },
    },
    filters: { sync: { schema: { fields: [] } }, indexing: { schema: { fields: [] } } },
    ...overrides,
  } as unknown as Schema;
}

beforeEach(() => {
  store().reset();
});

describe('opening the setup panel', () => {
  it.each([
    ['a new connector', connector({ _key: undefined }), undefined, 'authenticate'],
    ['an authenticated instance', connector({ isAuthenticated: true } as never), 'c1', 'configure'],
    ['an instance with no auth that is configured', connector({ authType: 'NONE', isConfigured: true } as never), 'c1', 'configure'],
    ['an OAuth instance still waiting for consent', connector(), 'c1', 'authorize'],
    ['a credentials instance not yet authenticated', connector({ authType: 'CUSTOM' }), 'c1', 'authenticate'],
  ])('opens %s on the %s tab', (_label, row, id, tab) => {
    store().openPanel(row, id as string | undefined, 'team');
    expect(store().panelActiveTab).toBe(tab);
    expect(store().isPanelOpen).toBe(true);
  });

  it('does not offer OAuth consent for a legacy Workspace team connector', () => {
    store().openPanel(connector({ type: 'Gmail Workspace' }), 'c1', 'team');
    expect(store().panelActiveTab).toBe('authenticate');
  });

  it("drops the previous instance's config so its sign-in state can't leak", () => {
    store().setSchemaAndConfig(schema(), { authType: 'CUSTOM', isAuthenticated: true } as ConnectorConfig);
    store().openPanel(connector({ _key: 'c2' }), 'c2');
    expect(store()).toMatchObject({
      connectorSchema: null,
      connectorConfig: null,
      isLoadingSchema: true,
      isAuthTypeImmutable: true,
    });
  });

  it('starts a new instance from a clean form named after the catalog entry', () => {
    store().setInstanceName('Old name');
    store().setAuthFormValue('apiKey', 'secret');
    store().openPanel(connector({ name: '  Jira Cloud ' }));
    expect(store().instanceName).toBe('Jira Cloud');
    expect(store().formData.auth).toEqual({});
    expect(store().isAuthTypeImmutable).toBe(false);
  });

  it('forgets everything when closed', () => {
    store().openPanel(connector(), 'c1');
    store().closePanel();
    expect(store().isPanelOpen).toBe(false);
    expect(store().panelConnector).toBeNull();
  });
});

describe('loading schema and saved config into the form', () => {
  it('fills saved values over schema defaults', () => {
    store().setSchemaAndConfig(schema(), {
      authType: 'CUSTOM',
      config: { auth: { apiKey: 'k-123' }, sync: { selectedStrategy: 'MANUAL', customValues: { batchSize: 10 } } },
    } as unknown as ConnectorConfig);

    expect(store().selectedAuthType).toBe('CUSTOM');
    expect(store().formData.auth).toMatchObject({ apiKey: 'k-123', region: 'eu' });
    expect(store().formData.sync.selectedStrategy).toBe('MANUAL');
    expect(store().formData.sync.customValues).toMatchObject({ batchSize: 10 });
    expect(store().authState).toBe('empty');
  });

  it('uses schema defaults for a brand-new instance, with numbers as numbers', () => {
    store().setSchemaAndConfig(schema(), null);
    expect(store().selectedAuthType).toBe('CUSTOM');
    expect(store().formData.auth).toEqual({ apiKey: '', region: 'eu' });
    expect(store().formData.sync.customValues).toEqual({ batchSize: 50 });
    expect(store().formData.sync.selectedStrategy).toBe('SCHEDULED');
  });

  it('falls back to the schema default when the saved auth type was migrated away', () => {
    store().openPanel(connector({ type: 'Gmail Workspace' }), 'c1');
    store().setPanelActiveTab('authorize');
    store().setSchemaAndConfig(schema({ auth: { ...schema().auth, supportedAuthTypes: ['CUSTOM'] } } as never), {
      authType: 'OAUTH',
    } as ConnectorConfig);
    expect(store().selectedAuthType).toBe('CUSTOM');
    expect(store().panelActiveTab).toBe('authenticate');
  });

  it('marks the auth step done for an authenticated or no-auth connector', () => {
    store().setSchemaAndConfig(schema(), { authType: 'CUSTOM', is_authenticated: 'true' } as unknown as ConnectorConfig);
    expect(store().authState).toBe('success');
    store().setSchemaAndConfig(schema({ auth: { supportedAuthTypes: ['NONE'] } } as never), null);
    expect(store().authState).toBe('success');
  });

  it('re-evaluates conditional fields as the person types, and clears that field error', () => {
    store().setSchemaAndConfig(schema(), null);
    store().mergeFormErrors({ region: 'Pick a region', apiKey: 'Required' });
    store().setAuthFormValue('region', 'us');
    expect(store().conditionalDisplay.regionHelp).toBe(true);
    expect(store().formErrors).toEqual({ apiKey: 'Required' });
    store().setAuthFormValue('region', undefined);
    expect(store().formData.auth).not.toHaveProperty('region');
    expect(store().conditionalDisplay.regionHelp).toBe(false);
  });

  it('switches auth type for a new instance but never for an existing one', () => {
    store().setSchemaAndConfig(schema(), null);
    store().setSelectedAuthType('OAUTH');
    expect(store().selectedAuthType).toBe('OAUTH');
    expect(store().formData.auth).toEqual({ clientId: '' });

    store().openPanel(connector(), 'c1');
    store().setSchemaAndConfig(schema(), { authType: 'CUSTOM' } as ConnectorConfig);
    store().setSelectedAuthType('OAUTH');
    expect(store().selectedAuthType).toBe('CUSTOM');
  });

  it('writes sync and filter edits into the form', () => {
    store().setSchemaAndConfig(schema(), null);
    store().setSyncStrategy('MANUAL');
    store().setSyncInterval(15);
    store().setSyncFormValue('batchSize', 5);
    store().setFilterFormValue('sync', 'folders', ['a']);
    store().setFilterFormValue('indexing', 'skip', true);
    store().setFilterFormValue('indexing', 'skip', undefined);
    const { sync, filters } = store().formData;
    expect(sync).toMatchObject({ selectedStrategy: 'MANUAL', scheduledConfig: { intervalMinutes: 15 }, customValues: { batchSize: 5 } });
    expect(filters.sync).toEqual({ folders: ['a'] });
    expect(filters.indexing).toEqual({});
  });
});

describe('the OAuth app list', () => {
  it('ignores an answer for a connector the panel has moved away from', () => {
    store().beginOAuthAppsListFetch('Google Drive');
    store().beginOAuthAppsListFetch('Slack');
    store().finishOAuthAppsListFetch('Google Drive', { ok: true, apps: [{ _id: 'stale' }] } as never);
    expect(store().oauthAppsListPhase).toBe('loading');
    store().finishOAuthAppsListFetch('Slack', { ok: true, apps: [{ _id: 'a1' }] } as never);
    expect(store().oauthAppsListPhase).toBe('ready');
    expect(store().oauthAppsList).toEqual([{ _id: 'a1' }]);
  });

  it('records a failure, and a cancel only affects the matching pending fetch', () => {
    store().beginOAuthAppsListFetch('Slack');
    store().cancelOAuthAppsListFetchIfPending('Jira');
    expect(store().oauthAppsListPhase).toBe('loading');
    store().finishOAuthAppsListFetch('Slack', { ok: false, error: 'Could not load apps.' } as never);
    expect(store()).toMatchObject({ oauthAppsListPhase: 'ready', oauthAppsList: [], oauthAppsListFetchError: 'Could not load apps.' });
    store().beginOAuthAppsListFetch('Jira');
    store().cancelOAuthAppsListFetchIfPending('Jira');
    expect(store().oauthAppsListPhase).toBe('idle');
  });
});

describe('instance lists', () => {
  const a = { _key: 'a', name: 'A', type: 'Slack', status: 'IDLE' } as ConnectorInstance;
  const b = { _key: 'b', name: 'B', type: 'Slack', status: 'IDLE' } as ConnectorInstance;

  it('keeps a deleted instance out of lists fetched before the delete finished', () => {
    store().setActiveConnectors([a, b]);
    store().setInstances([a, b]);
    store().setInstanceStats('a', { total: 1 } as never);
    store().openInstancePanel(a);

    store().removeConnectorInstance('a');

    expect(store().activeConnectors.map((c) => c._key)).toEqual(['b']);
    expect(store().instances.map((c) => c._key)).toEqual(['b']);
    expect(store().instanceStats).toEqual({});
    expect(store().isInstancePanelOpen).toBe(false);

    store().setActiveConnectors([a, b]);
    store().setRegistryConnectors([a, b]);
    expect(store().activeConnectors.map((c) => c._key)).toEqual(['b']);
    expect(store().registryConnectors.map((c) => c._key)).toEqual(['b']);
  });

  it('merges an updated row everywhere it is shown, and adds it if new', () => {
    store().setActiveConnectors([a]);
    store().setInstances([a]);
    store().openInstancePanel(a);
    store().upsertConnectorInstance({ _key: 'a', status: 'SYNCING' } as Connector);
    store().upsertConnectorInstance({ _key: 'z', name: 'Z' } as Connector);
    store().upsertConnectorInstance({ name: 'no id' } as Connector);

    expect(store().activeConnectors).toEqual([{ ...a, status: 'SYNCING' }, { _key: 'z', name: 'Z' }]);
    expect(store().instances[0].status).toBe('SYNCING');
    expect(store().selectedInstance?.status).toBe('SYNCING');
  });

  it('keeps the open instance current when the list is refetched', () => {
    store().openInstancePanel(a);
    store().setInstances([{ ...a, status: 'FULL_SYNCING' }]);
    expect(store().selectedInstance?.status).toBe('FULL_SYNCING');
  });

  it('renames everywhere and ignores a blank name', () => {
    store().setActiveConnectors([a]);
    store().setInstances([a]);
    store().openInstancePanel(a);
    store().openPanel(connector({ _key: 'a' }), 'a');
    store().renameConnectorInstance('a', 'Support Slack');
    store().renameConnectorInstance('a', '   ');
    expect(store().activeConnectors[0].name).toBe('Support Slack');
    expect(store().instances[0].name).toBe('Support Slack');
    expect(store().selectedInstance?.name).toBe('Support Slack');
    expect(store().panelConnector?.name).toBe('Support Slack');
  });

  it('updates the sign-in flag on every copy of an instance', () => {
    store().setActiveConnectors([a]);
    store().setInstances([a]);
    store().openInstancePanel(a);
    store().openPanel(connector({ _key: 'a' }), 'a');
    store().syncConnectorInstanceAuthFlags('a', true);
    expect(store().activeConnectors[0].isAuthenticated).toBe(true);
    expect(store().instances[0].isAuthenticated).toBe(true);
    expect(store().selectedInstance?.isAuthenticated).toBe(true);
    expect(store().panelConnector?.isAuthenticated).toBe(true);
  });

  it('clears per-instance caches on request', () => {
    store().setInstanceConfig('a', {} as ConnectorConfig);
    store().setLocalSyncStatus('a', { state: 'watching' } as never);
    store().removeConnectorInstanceCaches('a');
    expect(store().instanceConfigs).toEqual({});
    store().clearLocalSyncStatus('a');
    store().setInstances([a]);
    store().clearInstanceData();
    expect(store().instances).toEqual([]);
    expect(store().localSyncStatuses).toEqual({});
  });
});
