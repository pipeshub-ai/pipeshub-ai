import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearSessionCache } from '@/lib/api/session-cache';
import type { Connector, ConnectorConfig } from '../../types';

const api = vi.hoisted(() => ({ getConnectorConfig: vi.fn() }));
vi.mock('../../api', () => ({ ConnectorsApi: api }));

import { useConnectorsStore } from '../../store';
import { loadInstanceConfigs } from '../load-instance-configs';

const config = (name: string) => ({ name }) as unknown as ConnectorConfig;
const listed = (...ids: string[]) =>
  useConnectorsStore.setState({ activeConnectors: ids.map((_key) => ({ _key }) as Connector) });

beforeEach(() => {
  clearSessionCache();
  useConnectorsStore.getState().reset();
  api.getConnectorConfig.mockReset();
  api.getConnectorConfig.mockImplementation(async (id: string) => config(id));
  listed('a', 'b');
});

describe('loadInstanceConfigs', () => {
  it('reads every instance config into the store', async () => {
    await expect(loadInstanceConfigs(['a', 'b'])).resolves.toBe(true);

    expect(useConnectorsStore.getState().instanceConfigs).toEqual({ a: config('a'), b: config('b') });
  });

  it('keeps the configs it could read and rejects, so the set is read again next time', async () => {
    api.getConnectorConfig.mockImplementation(async (id: string) => {
      if (id === 'b') throw new Error('500');
      return config(id);
    });

    await expect(loadInstanceConfigs(['a', 'b'])).rejects.toThrow();

    expect(useConnectorsStore.getState().instanceConfigs).toEqual({ a: config('a') });
  });

  it('drops a config that arrives after sign-out', async () => {
    let answer: (value: ConnectorConfig) => void = () => {};
    api.getConnectorConfig.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));

    const load = loadInstanceConfigs(['a']);
    clearSessionCache();
    answer(config('a'));
    await load;

    expect(useConnectorsStore.getState().instanceConfigs).toEqual({});
  });

  it('drops a config for an instance removed while it was being read', async () => {
    let answer: (value: ConnectorConfig) => void = () => {};
    api.getConnectorConfig.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));

    const load = loadInstanceConfigs(['a']);
    useConnectorsStore.getState().removeConnectorInstance('a');
    answer(config('a'));
    await load;

    expect(useConnectorsStore.getState().instanceConfigs).toEqual({});
  });
});
