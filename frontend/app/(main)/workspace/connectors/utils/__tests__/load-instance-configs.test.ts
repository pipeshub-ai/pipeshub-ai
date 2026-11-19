import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearSessionCache } from '@/lib/api/session-cache';
import { onSwrNamespaceRevalidate, SWR_NS } from '@/lib/api/swr-cache';
import { CONNECTOR_OAUTH_POST_MESSAGE } from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
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

  it('keeps a config whose page was left while it was being read, since the set is then cached as read', async () => {
    let answer: (value: ConnectorConfig) => void = () => {};
    api.getConnectorConfig.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));

    const load = loadInstanceConfigs(['a']);
    listed('personal-1');
    answer(config('a'));
    await expect(load).resolves.toBe(true);

    expect(useConnectorsStore.getState().instanceConfigs).toEqual({ a: config('a') });
  });
});

describe('an OAuth sign-in finished in another window', () => {
  it('refreshes the cached instance configs, even with no panel open', () => {
    const refreshed = vi.fn();
    const stop = onSwrNamespaceRevalidate(SWR_NS.connectorConfigs, refreshed);

    window.dispatchEvent(
      new MessageEvent('message', { data: { type: CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS }, origin: window.location.origin }),
    );

    expect(refreshed).toHaveBeenCalledTimes(1);
    stop();
  });

  it('ignores the same message from another origin', () => {
    const refreshed = vi.fn();
    const stop = onSwrNamespaceRevalidate(SWR_NS.connectorConfigs, refreshed);

    window.dispatchEvent(
      new MessageEvent('message', { data: { type: CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS }, origin: 'https://evil.test' }),
    );

    expect(refreshed).not.toHaveBeenCalled();
    stop();
  });
});
