import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ConnectorsApi } from '../../api';
import { KnowledgeHubApi } from '@/app/(main)/knowledge-base/api';
import type { KnowledgeHubApiResponse } from '@/app/(main)/knowledge-base/types';
import type { Connector, ConnectorStatsResponse } from '../../types';
import { useDemoDataStore } from '../store';

vi.mock('../../api', () => ({
  ConnectorsApi: {
    getActiveConnectors: vi.fn(),
    getConnectorStats: vi.fn(),
  },
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: { searchAllRecords: vi.fn() },
}));

const getActiveConnectors = vi.mocked(ConnectorsApi.getActiveConnectors);
const getConnectorStats = vi.mocked(ConnectorsApi.getConnectorStats);
const searchAllRecords = vi.mocked(KnowledgeHubApi.searchAllRecords);

function collectionRecords(count: number): KnowledgeHubApiResponse {
  return { items: Array.from({ length: count }, (_, i) => ({ id: `r${i}` })) } as unknown as KnowledgeHubApiResponse;
}

function connector(key: string, type: string, extra: Partial<Connector> = {}): Connector {
  return { _key: key, type, name: key, isActive: true, scope: 'team', ...extra } as Connector;
}

function statsWith(completed: number): ConnectorStatsResponse {
  return {
    success: true,
    data: { stats: { total: completed, indexingStatus: { COMPLETED: completed } } },
  } as unknown as ConnectorStatsResponse;
}

function listing(...connectors: Connector[]) {
  return { success: true, connectors };
}

beforeEach(() => {
  vi.clearAllMocks();
  searchAllRecords.mockResolvedValue(collectionRecords(0));
  useDemoDataStore.getState().reset();
});

describe('setTeamConnectors', () => {
  it('keeps the Demo connector instances and nothing else', () => {
    useDemoDataStore.getState().setTeamConnectors([connector('demo', 'Demo'), connector('slack', 'Slack')]);

    expect(useDemoDataStore.getState().demoConnectors.map((c) => c._key)).toEqual(['demo']);
  });

  it('keeps a disabled demo, whose records are still searchable', () => {
    useDemoDataStore.getState().setTeamConnectors([connector('demo', 'Demo', { isActive: false })]);

    expect(useDemoDataStore.getState().demoConnectors.map((c) => c._key)).toEqual(['demo']);
  });

  it('forgets a demo that is no longer listed', () => {
    useDemoDataStore.getState().setTeamConnectors([connector('demo', 'Demo')]);
    useDemoDataStore.getState().setTeamConnectors([connector('slack', 'Slack')]);

    expect(useDemoDataStore.getState().demoConnectors).toEqual([]);
  });
});

describe('checkRealData', () => {
  it('counts files uploaded to a Collection, which the connector list leaves out', async () => {
    searchAllRecords.mockResolvedValue(collectionRecords(1));

    await useDemoDataStore.getState().checkRealData();

    expect(useDemoDataStore.getState().realDataIndexed).toBe(true);
    expect(searchAllRecords).toHaveBeenCalledWith(
      expect.objectContaining({ origins: 'COLLECTION', nodeTypes: 'record', indexingStatus: 'COMPLETED' }),
    );
    expect(getActiveConnectors).not.toHaveBeenCalled();
  });

  it('falls back to connectors when the Collection lookup fails', async () => {
    searchAllRecords.mockRejectedValue(new Error('500'));
    getActiveConnectors.mockResolvedValue(listing(connector('slack', 'Slack')));
    getConnectorStats.mockResolvedValue(statsWith(1));

    await useDemoDataStore.getState().checkRealData();

    expect(useDemoDataStore.getState().realDataIndexed).toBe(true);
  });

  it('is true once another connector, team or personal, has an indexed record', async () => {
    getActiveConnectors.mockImplementation(async (scope) =>
      scope === 'team'
        ? listing(connector('demo', 'Demo'), connector('slack', 'Slack'))
        : listing(connector('gmail', 'Gmail', { scope: 'personal' })),
    );
    getConnectorStats.mockImplementation(async (id) => statsWith(id === 'gmail' ? 4 : 0));

    await useDemoDataStore.getState().checkRealData();

    expect(useDemoDataStore.getState().realDataIndexed).toBe(true);
    expect(getConnectorStats).not.toHaveBeenCalledWith('demo');
  });

  it('is false while nothing but the demo has indexed records', async () => {
    getActiveConnectors.mockImplementation(async (scope) =>
      scope === 'team' ? listing(connector('demo', 'Demo'), connector('slack', 'Slack')) : listing(),
    );
    getConnectorStats.mockResolvedValue(statsWith(0));

    await useDemoDataStore.getState().checkRealData();

    expect(useDemoDataStore.getState().realDataIndexed).toBe(false);
  });

  it('skips a connector whose stats fail and keeps looking', async () => {
    getActiveConnectors.mockImplementation(async (scope) =>
      scope === 'team' ? listing(connector('broken', 'Jira'), connector('drive', 'Drive')) : listing(),
    );
    getConnectorStats.mockImplementation(async (id) => {
      if (id === 'broken') throw new Error('500');
      return statsWith(2);
    });

    await useDemoDataStore.getState().checkRealData();

    expect(useDemoDataStore.getState().realDataIndexed).toBe(true);
  });

  it('does not look again once real data was found', async () => {
    getActiveConnectors.mockResolvedValue(listing(connector('slack', 'Slack')));
    getConnectorStats.mockResolvedValue(statsWith(1));

    await useDemoDataStore.getState().checkRealData();
    await useDemoDataStore.getState().checkRealData();

    expect(getConnectorStats).toHaveBeenCalledTimes(1);
  });
});

describe('reset', () => {
  it('drops a real-data answer that arrives after it', async () => {
    let answer!: (value: { items: unknown[] }) => void;
    searchAllRecords.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)) as never);
    const pending = useDemoDataStore.getState().checkRealData();

    useDemoDataStore.getState().reset();
    answer({ items: [{ id: 'r1' }] });
    await pending;

    expect(useDemoDataStore.getState().realDataIndexed).toBeNull();
  });

  it('forgets the demo, so the chat landing drops its demo extras straight away', async () => {
    useDemoDataStore.getState().setTeamConnectors([connector('demo', 'Demo')]);

    useDemoDataStore.getState().reset();

    expect(useDemoDataStore.getState().demoConnectors).toEqual([]);
    expect(useDemoDataStore.getState().realDataIndexed).toBeNull();
  });
});
