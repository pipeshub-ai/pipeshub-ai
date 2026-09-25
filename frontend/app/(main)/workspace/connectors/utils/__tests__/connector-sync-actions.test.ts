/**
 * "Sync now", "Full sync" and turning sync on for a connector instance: which
 * backend call each makes, the status the card shows straight away, and how a
 * Local FS connector whose desktop app is offline is reported. The backend is
 * faked at the axios adapter; the desktop bridge (`window.electronAPI`) is
 * faked for the Electron cases.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';
import type { ConnectorInstance } from '../../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { useConnectorsStore } = await import('../../store');
const { startConnectorSync, runConnectorResync, toggleConnectorSyncOn, assertLocalFsRootPathAvailable } =
  await import('../connector-sync-actions');
const { LOCAL_FS_DESKTOP_OFFLINE, LOCAL_FS_DESKTOP_OWNED_BY_OTHER_DEVICE } = await import('../../constants');

const BASE = '/api/v1/connectors';

function instance(overrides: Partial<ConnectorInstance> = {}): ConnectorInstance {
  return {
    _key: 'c1',
    name: 'Team Drive',
    type: 'Google Drive',
    isActive: true,
    isConfigured: false,
    isAuthenticated: true,
    status: 'IDLE',
    ...overrides,
  } as ConnectorInstance;
}

function desktopRefusal(code: string, ownerDeviceName?: string) {
  return { status: 409, data: { message: 'The desktop app is not connected.', details: { code, ownerDeviceName } } };
}

const card = (id = 'c1') => useConnectorsStore.getState().activeConnectors.find((c) => c._key === id);

function installDesktop(overrides: Record<string, unknown> = {}) {
  const localSync = {
    getDeviceInfo: vi.fn(async () => ({ ok: true, deviceId: 'dev-1', deviceName: 'Ada’s laptop' })),
    checkRootPathConflict: vi.fn(async () => ({ available: true })),
    start: vi.fn(async () => ({})),
    stop: vi.fn(async () => ({})),
    ...overrides,
  };
  (window as unknown as { electronAPI: unknown }).electronAPI = { isElectron: true, localSync };
  return localSync;
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
  useConnectorsStore.getState().reset();
  useConnectorsStore.getState().setActiveConnectors([instance()]);
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI;
  vi.restoreAllMocks();
});

describe('startConnectorSync', () => {
  it('resyncs an active connector and shows it syncing even if the refetch still says idle', async () => {
    const api = fakeApi({
      [`GET ${BASE}/c1`]: { status: 200, data: { connector: instance({ status: 'IDLE' }) } },
      [`POST ${BASE}/c1/resync`]: { status: 200, data: { success: true } },
    });

    const outcome = await startConnectorSync({ _key: 'c1' });

    expect(outcome).toEqual({ kind: 'backend' });
    expect(api.sent.find((r) => r.url.endsWith('/resync'))?.body).toEqual({ connectorName: 'Google Drive', fullSync: false });
    expect(card()?.status).toBe('SYNCING');
    expect(api.count(`POST ${BASE}/c1/toggle`)).toBe(0);
  });

  it('keeps the status the server reports once it has picked the job up', async () => {
    fakeApi({
      [`GET ${BASE}/c1`]: [
        { status: 200, data: { connector: instance() } },
        { status: 200, data: { connector: instance({ status: 'FULL_SYNCING' }) } },
      ],
      [`POST ${BASE}/c1/resync`]: { status: 200 },
    });
    await startConnectorSync({ _key: 'c1' });
    expect(card()?.status).toBe('FULL_SYNCING');
  });

  it('turns sync on for an inactive connector instead of resyncing it', async () => {
    const api = fakeApi({
      [`GET ${BASE}/c1`]: [
        { status: 200, data: { connector: instance({ isActive: false }) } },
        { status: 200, data: { connector: instance({ isActive: true, status: 'SYNCING' }) } },
      ],
      [`POST ${BASE}/c1/toggle`]: { status: 200 },
    });

    expect(await startConnectorSync({ _key: 'c1' })).toEqual({ kind: 'backend' });

    expect(api.sent.find((r) => r.url.endsWith('/toggle'))?.body).toEqual({ type: 'sync' });
    expect(api.count(`POST ${BASE}/c1/resync`)).toBe(0);
    expect(card()).toMatchObject({ isActive: true, status: 'SYNCING' });
  });

  it('loads the saved config of a configured connector while refreshing it', async () => {
    const config = { config: { sync: { values: {} } } };
    fakeApi({
      [`GET ${BASE}/c1`]: { status: 200, data: { connector: instance({ isConfigured: true }) } },
      [`POST ${BASE}/c1/resync`]: { status: 200 },
      [`GET ${BASE}/c1/config`]: { status: 200, data: { config } },
    });
    await startConnectorSync({ _key: 'c1' });
    expect(useConnectorsStore.getState().instanceConfigs.c1).toEqual(config);
  });

  it('refreshes the open drawer stats for that connector', async () => {
    useConnectorsStore.getState().openInstancePanel(instance());
    const api = fakeApi({
      [`GET ${BASE}/c1`]: { status: 200, data: { connector: instance() } },
      [`POST ${BASE}/c1/resync`]: { status: 200 },
      [`GET ${BASE}/c1/stats`]: { status: 200, data: { data: { total: 12 } } },
    });
    await startConnectorSync({ _key: 'c1' });
    await vi.waitFor(() => expect(useConnectorsStore.getState().instanceStats.c1).toEqual({ total: 12 }));
    expect(api.count(`GET ${BASE}/c1/stats`)).toBe(1);
  });

  it('requires an instance id and a known type', async () => {
    await expect(startConnectorSync({ _key: '' })).rejects.toThrow(/connectorId/);
    fakeApi({ [`GET ${BASE}/c1`]: { status: 200, data: { connector: instance({ type: '' as never }) } } });
    await expect(startConnectorSync({ _key: 'c1' })).rejects.toThrow(/type unknown/);
  });

  it('passes a backend failure through without marking the card syncing', async () => {
    fakeApi({
      [`GET ${BASE}/c1`]: { status: 200, data: { connector: instance() } },
      [`POST ${BASE}/c1/resync`]: { status: 409, data: { message: 'A sync is already running for this connector.' } },
    });
    await expect(startConnectorSync({ _key: 'c1' })).rejects.toMatchObject({
      message: 'A sync is already running for this connector.',
    });
    expect(card()?.status).toBe('IDLE');
  });
});

describe('runConnectorResync', () => {
  it('asks for a full sync and shows full-syncing', async () => {
    const api = fakeApi({
      [`POST ${BASE}/c1/resync`]: { status: 200 },
      [`GET ${BASE}/c1`]: { status: 200, data: { connector: instance() } },
    });
    await runConnectorResync({ connectorId: 'c1', connectorType: 'Google Drive', fullSync: true });
    expect(api.sent[0].body).toEqual({ connectorName: 'Google Drive', fullSync: true });
    expect(card()?.status).toBe('FULL_SYNCING');
  });

  it('reports a Local FS connector whose desktop app is offline instead of throwing', async () => {
    fakeApi({ [`POST ${BASE}/c1/resync`]: desktopRefusal(LOCAL_FS_DESKTOP_OFFLINE, 'Studio Mac') });
    const outcome = await runConnectorResync({ connectorId: 'c1', connectorType: 'Local FS' });
    expect(outcome).toEqual({ kind: 'requires-desktop', reason: 'offline', ownerDeviceName: 'Studio Mac' });
    expect(card()?.status).toBe('IDLE');
  });

  it('does not treat the desktop code as special for other connector types', async () => {
    fakeApi({ [`POST ${BASE}/c1/resync`]: desktopRefusal(LOCAL_FS_DESKTOP_OFFLINE) });
    await expect(runConnectorResync({ connectorId: 'c1', connectorType: 'Google Drive' })).rejects.toBeDefined();
  });
});

describe('toggleConnectorSyncOn', () => {
  // First Electron case in the file: the bridge caches a successful device identity for the window's life.
  it('refuses to enable when this device cannot identify itself', async () => {
    installDesktop({ getDeviceInfo: vi.fn(async () => ({ ok: false, error: 'keychain locked' })) });
    const api = fakeApi({});
    await expect(toggleConnectorSyncOn('c1', 'Local FS')).rejects.toThrow();
    expect(api.sent).toHaveLength(0);
  });

  it('sends this desktop as the owner when enabling a Local FS connector in the app', async () => {
    const desktop = installDesktop();
    useConnectorsStore.getState().setInstanceConfig('c1', { config: { sync: { values: { sync_root_path: '/Users/ada/Notes' } } } } as never);
    const api = fakeApi({ [`POST ${BASE}/c1/toggle`]: { status: 200 } });

    expect(await toggleConnectorSyncOn('c1', 'Local FS')).toEqual({ kind: 'backend' });

    expect(desktop.checkRootPathConflict).toHaveBeenCalledWith('c1', '/Users/ada/Notes');
    expect(api.sent[0].body).toEqual({ type: 'sync', deviceId: 'dev-1', deviceName: 'Ada’s laptop' });
  });

  it('stops the watcher it started when another device owns the connector', async () => {
    const desktop = installDesktop();
    fakeApi({
      [`GET ${BASE}/c1/config`]: { status: 200, data: { config: { config: { sync: { values: { sync_root_path: '/data' } } } } } },
      [`POST ${BASE}/c1/toggle`]: desktopRefusal(LOCAL_FS_DESKTOP_OWNED_BY_OTHER_DEVICE, 'Office PC'),
    });
    const outcome = await toggleConnectorSyncOn('c1', 'Local FS');
    expect(outcome).toEqual({ kind: 'requires-desktop', reason: 'other_device', ownerDeviceName: 'Office PC' });
    expect(desktop.stop).toHaveBeenCalledWith('c1');
  });

  it('sends no device for an ordinary connector', async () => {
    const api = fakeApi({ [`POST ${BASE}/c1/toggle`]: { status: 200 } });
    await toggleConnectorSyncOn('c1', 'Slack');
    expect(api.sent[0].body).toEqual({ type: 'sync' });
  });
});

describe('assertLocalFsRootPathAvailable', () => {
  it('blocks enabling when another connector already watches the same folder', async () => {
    installDesktop({
      checkRootPathConflict: vi.fn(async () => ({ available: false, ownerConnectorName: 'Notes (old)' })),
    });
    fakeApi({ [`GET ${BASE}/c1/config`]: { status: 200, data: { config: { config: { sync: { customValues: { folderPath: ' /data ' } } } } } } });
    await expect(assertLocalFsRootPathAvailable('c1', 'Local FS')).rejects.toThrow(/Notes \(old\)/);
  });

  it('is a no-op in the browser and for other connector types', async () => {
    const api = fakeApi({});
    await assertLocalFsRootPathAvailable('c1', 'Local FS');
    installDesktop();
    await assertLocalFsRootPathAvailable('c1', 'Slack');
    expect(api.sent).toHaveLength(0);
  });
});
