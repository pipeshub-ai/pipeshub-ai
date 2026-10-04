import { describe, it, expect } from 'vitest';
import type { Connector, ConnectorStatsResponse } from '../../types';
import {
  DEMO_CONNECTOR_TYPE,
  REMOVAL_NOTICE_SNOOZE_MS,
  demoConnectorsIn,
  hasActiveDemo,
  hasIndexedRecords,
  isRemovalNoticeSnoozed,
  isSampleAccountEmail,
  otherConnectorsIn,
  snoozeRemovalNotice,
} from '../demo-data';

function connector(overrides: Partial<Connector>): Connector {
  return {
    _key: 'c1',
    name: 'Connector',
    type: 'Slack',
    appGroup: '',
    appDescription: '',
    appCategories: [],
    iconPath: '',
    supportedAuthTypes: [],
    supportsRealtime: false,
    supportsSync: true,
    supportsAgent: false,
    scope: 'team',
    isActive: true,
    isAgentActive: false,
    isConfigured: true,
    isAuthenticated: true,
    ...overrides,
  };
}

function stats(completed: number): ConnectorStatsResponse['data'] {
  return {
    orgId: 'o',
    connectorId: 'c',
    origin: 'CONNECTOR',
    stats: {
      total: completed,
      indexingStatus: {
        NOT_STARTED: 0,
        IN_PROGRESS: 3,
        COMPLETED: completed,
        FAILED: 0,
        FILE_TYPE_NOT_SUPPORTED: 0,
        AUTO_INDEX_OFF: 0,
        ENABLE_MULTIMODAL_MODELS: 0,
        EMPTY: 0,
        QUEUED: 0,
        PAUSED: 0,
      },
    },
    byRecordType: [],
  };
}

describe('demoConnectorsIn', () => {
  it('keeps Demo instances, disabled ones too, but not ones being deleted', () => {
    // A disabled connector's records stay searchable, so they still need the badge.
    const list = [
      connector({ _key: 'demo', type: DEMO_CONNECTOR_TYPE }),
      connector({ _key: 'off', type: DEMO_CONNECTOR_TYPE, isActive: false }),
      connector({ _key: 'going', type: DEMO_CONNECTOR_TYPE, status: 'DELETING' }),
      connector({ _key: undefined, type: DEMO_CONNECTOR_TYPE }),
      connector({ _key: 'slack', type: 'Slack' }),
    ];
    expect(demoConnectorsIn(list).map((c) => c._key)).toEqual(['demo', 'off']);
  });
});

describe('hasActiveDemo', () => {
  it('is true only while some Demo instance is enabled', () => {
    const on = connector({ _key: 'demo', type: DEMO_CONNECTOR_TYPE });
    const off = connector({ _key: 'off', type: DEMO_CONNECTOR_TYPE, isActive: false });
    expect(hasActiveDemo([off, on])).toBe(true);
    expect(hasActiveDemo([off])).toBe(false);
    expect(hasActiveDemo([])).toBe(false);
  });
});

describe('otherConnectorsIn', () => {
  it('counts disabled connectors, which keep their indexed records, but not ones being deleted', () => {
    const list = [
      connector({ _key: 'demo', type: DEMO_CONNECTOR_TYPE }),
      connector({ _key: 'slack', type: 'Slack' }),
      connector({ _key: 'drive-off', type: 'Drive', isActive: false }),
      connector({ _key: 'jira-going', type: 'Jira', status: 'DELETING' }),
    ];
    expect(otherConnectorsIn(list).map((c) => c._key)).toEqual(['slack', 'drive-off']);
  });
});

describe('hasIndexedRecords', () => {
  it('is true once one record is indexed, not while records are only in progress', () => {
    expect(hasIndexedRecords(stats(1))).toBe(true);
    expect(hasIndexedRecords(stats(0))).toBe(false);
    expect(hasIndexedRecords(undefined)).toBe(false);
  });
});

describe('isSampleAccountEmail', () => {
  it('matches the reserved demo domain exactly, ignoring case and spaces', () => {
    expect(isSampleAccountEmail('alice@acme-demo.example')).toBe(true);
    expect(isSampleAccountEmail(' Bob@ACME-DEMO.example ')).toBe(true);
  });

  it('does not match look-alike domains or other addresses', () => {
    expect(isSampleAccountEmail('ceo@acme-demo.example.com')).toBe(false);
    expect(isSampleAccountEmail('x@evil-acme-demo.example')).toBe(false);
    expect(isSampleAccountEmail('alice@acme.com')).toBe(false);
    expect(isSampleAccountEmail(undefined)).toBe(false);
  });
});

describe('removal notice snooze', () => {
  function memoryStorage() {
    const data = new Map<string, string>();
    return {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    };
  }

  it('hides the notice for a week for that demo connector only', () => {
    const storage = memoryStorage();
    const now = 1_000_000;
    snoozeRemovalNotice('demo-1', now, storage);

    expect(isRemovalNoticeSnoozed('demo-1', now + 1, storage)).toBe(true);
    expect(isRemovalNoticeSnoozed('demo-1', now + REMOVAL_NOTICE_SNOOZE_MS + 1, storage)).toBe(false);
    expect(isRemovalNoticeSnoozed('demo-2', now + 1, storage)).toBe(false);
  });

  it('shows the notice when storage is missing or throws', () => {
    const throwing = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
    };
    expect(() => snoozeRemovalNotice('demo-1', 0, throwing)).not.toThrow();
    expect(isRemovalNoticeSnoozed('demo-1', 0, throwing)).toBe(false);
    expect(isRemovalNoticeSnoozed('demo-1', 0, null)).toBe(false);
  });
});
