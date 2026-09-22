import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, cleanup } from '@testing-library/react';
import { isElectron } from '@/lib/electron';
import {
  buildLocalSyncStartOptionsFromConnectorConfig,
  extractLocalFsRootPath,
  getElectronDeviceInfo,
  getElectronLocalSyncStatus,
  startElectronLocalSync,
  stopElectronLocalSync,
} from '../electron-local-sync';
import { useEnsureLocalWatcher } from '../use-ensure-local-watcher';
import type { ConnectorConfig, ConnectorInstance, LocalSyncStatus } from '../../types';

const storeMocks = vi.hoisted(() => ({
  setLocalSyncStatus: vi.fn(),
  clearLocalSyncStatus: vi.fn(),
}));

vi.mock('@/lib/electron', () => ({ isElectron: vi.fn() }));
vi.mock('../electron-local-sync', () => ({
  buildLocalSyncStartOptionsFromConnectorConfig: vi.fn(),
  extractLocalFsRootPath: vi.fn(),
  getElectronDeviceInfo: vi.fn(),
  getElectronLocalSyncStatus: vi.fn(),
  startElectronLocalSync: vi.fn(),
  stopElectronLocalSync: vi.fn(),
}));
vi.mock('../../store', () => ({
  useConnectorsStore: (selector: (state: typeof storeMocks) => unknown) => selector(storeMocks),
}));

const mockIsElectron = vi.mocked(isElectron);
const mockBuildOptions = vi.mocked(buildLocalSyncStartOptionsFromConnectorConfig);
const mockExtractRootPath = vi.mocked(extractLocalFsRootPath);
const mockDeviceInfo = vi.mocked(getElectronDeviceInfo);
const mockGetStatus = vi.mocked(getElectronLocalSyncStatus);
const mockStart = vi.mocked(startElectronLocalSync);
const mockStop = vi.mocked(stopElectronLocalSync);

const THIS_DEVICE = { ok: true as const, deviceId: 'dev-a', deviceName: 'Work Laptop' };

const STATUS = { connectorId: 'c1', watcherState: 'watching' } as LocalSyncStatus;

function makeInstance(overrides: Partial<ConnectorInstance> = {}): ConnectorInstance {
  return {
    _key: 'c1',
    name: 'My Folder',
    type: 'LOCAL_FS',
    isActive: true,
    isConfigured: true,
    isAuthenticated: true,
    ownerDeviceId: 'dev-a',
    ...overrides,
  } as ConnectorInstance;
}

const CONFIG = {} as ConnectorConfig;

/** The hook only closes over the ref, so one render per assertion is enough. */
function ensure(instance: ConnectorInstance, ref: { current: Set<string> }, config = CONFIG) {
  const { result } = renderHook(() => useEnsureLocalWatcher(ref));
  return result.current(instance, config);
}

function makeRef(ids: string[] = ['c1']) {
  return { current: new Set(ids) };
}

describe('useEnsureLocalWatcher', () => {
  beforeEach(() => {
    mockIsElectron.mockReturnValue(true);
    mockDeviceInfo.mockResolvedValue(THIS_DEVICE);
    mockExtractRootPath.mockReturnValue('/home/me/Docs');
    mockBuildOptions.mockReturnValue({ syncStrategy: 'MANUAL' } as ReturnType<
      typeof buildLocalSyncStartOptionsFromConnectorConfig
    >);
    mockStart.mockResolvedValue(STATUS);
    mockGetStatus.mockResolvedValue(STATUS);
    mockStop.mockResolvedValue(null);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  describe('cases it declines to touch', () => {
    it('does nothing for an instance with no key', async () => {
      await ensure(makeInstance({ _key: undefined }), makeRef());

      expect(mockStop.mock.calls).toEqual([]);
      expect(mockStart.mock.calls).toEqual([]);
      expect(mockDeviceInfo.mock.calls).toEqual([]);
    });

    it('does nothing outside Electron', async () => {
      mockIsElectron.mockReturnValue(false);

      await ensure(makeInstance(), makeRef());

      expect(mockStop.mock.calls).toEqual([]);
      expect(mockStart.mock.calls).toEqual([]);
    });

    it('does nothing for a connector that is not Local FS', async () => {
      await ensure(makeInstance({ type: 'GOOGLE_DRIVE' }), makeRef());

      expect(mockStop.mock.calls).toEqual([]);
      expect(mockStart.mock.calls).toEqual([]);
      expect(mockDeviceInfo.mock.calls).toEqual([]);
    });
  });

  describe('tearing the watcher down', () => {
    it.each([
      ['inactive', { isActive: false }],
      ['unconfigured', { isConfigured: false }],
      ['unauthenticated', { isAuthenticated: false }],
    ])('stops and forgets the watcher when the instance is %s', async (_label, patch) => {
      const ref = makeRef();

      await ensure(makeInstance(patch), ref);

      expect(mockStop.mock.calls).toEqual([['c1']]);
      expect(ref.current.has('c1')).toBe(false);
      expect(storeMocks.clearLocalSyncStatus.mock.calls).toEqual([['c1']]);
      // Resolving the device is a child process on some platforms; an instance
      // already known to be down must not pay for it.
      expect(mockDeviceInfo.mock.calls).toEqual([]);
    });

    it('stops the watcher when another device owns the connector', async () => {
      const ref = makeRef();

      await ensure(makeInstance({ ownerDeviceId: 'dev-b' }), ref);

      expect(mockStop.mock.calls).toEqual([['c1']]);
      expect(ref.current.has('c1')).toBe(false);
      expect(storeMocks.clearLocalSyncStatus.mock.calls).toEqual([['c1']]);
      expect(mockStart.mock.calls).toEqual([]);
    });

    it('stops the watcher for a connector with no owner device at all', async () => {
      // Nothing has claimed it yet, so this machine is not its owner either.
      const ref = makeRef();

      await ensure(makeInstance({ ownerDeviceId: undefined }), ref);

      expect(mockStop.mock.calls).toEqual([['c1']]);
      expect(mockStart.mock.calls).toEqual([]);
    });
  });

  describe('when the device identity is unavailable', () => {
    it('leaves a running watcher alone when the identity lookup fails', async () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      mockDeviceInfo.mockResolvedValue({ ok: false, error: 'reg.exe: spawn EACCES' });
      const ref = makeRef();

      await ensure(makeInstance(), ref);

      // A transient identity failure must not be read as "another device owns
      // this": tearing the watcher down would drop the journal and demote the
      // next incremental pull to a full rescan.
      expect(mockStop.mock.calls).toEqual([]);
      expect(mockStart.mock.calls).toEqual([]);
      expect(ref.current.has('c1')).toBe(true);
      expect(storeMocks.clearLocalSyncStatus.mock.calls).toEqual([]);
      expect(warn.mock.calls[0]?.[0]).toContain('reg.exe: spawn EACCES');
      warn.mockRestore();
    });

    it('leaves a running watcher alone when there is no device info to read', async () => {
      mockDeviceInfo.mockResolvedValue(null);
      const ref = makeRef();

      await ensure(makeInstance(), ref);

      expect(mockStop.mock.calls).toEqual([]);
      expect(mockStart.mock.calls).toEqual([]);
      expect(ref.current.has('c1')).toBe(true);
    });
  });

  describe('mounting the watcher for the owner device', () => {
    it('starts, records the status and tracks the id', async () => {
      const ref = makeRef([]);

      await ensure(makeInstance(), ref);

      expect(mockStart.mock.calls).toEqual([
        [
          {
            connectorId: 'c1',
            connectorName: 'My Folder',
            rootPath: '/home/me/Docs',
            syncStrategy: 'MANUAL',
          },
        ],
      ]);
      expect(storeMocks.setLocalSyncStatus.mock.calls).toEqual([['c1', STATUS]]);
      expect(ref.current.has('c1')).toBe(true);
      expect(mockStop.mock.calls).toEqual([]);
    });

    it('does not start without a configured root path', async () => {
      mockExtractRootPath.mockReturnValue(null);
      const ref = makeRef([]);

      await ensure(makeInstance(), ref);

      expect(mockStart.mock.calls).toEqual([]);
      expect(ref.current.has('c1')).toBe(false);
      // Not a teardown either: the instance is still owned by this device.
      expect(mockStop.mock.calls).toEqual([]);
    });

    it('does not track the id when the watcher reports no status', async () => {
      mockGetStatus.mockResolvedValue(null);
      const ref = makeRef([]);

      await ensure(makeInstance(), ref);

      expect(mockStart.mock.calls.length).toBe(1);
      expect(storeMocks.setLocalSyncStatus.mock.calls).toEqual([]);
      expect(ref.current.has('c1')).toBe(false);
    });
  });
});
