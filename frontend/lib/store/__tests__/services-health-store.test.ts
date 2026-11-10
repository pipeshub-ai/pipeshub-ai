import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// The Python services come up about a minute after the Node API. A failed
// background check used to wait the full 10-minute interval before the next
// one, so a page blocked by <ServiceGate> stayed blocked long after they were up.
vi.mock('@/lib/api/axios-instance', () => ({
  apiClient: { get: vi.fn() },
}));

import { apiClient } from '@/lib/api/axios-instance';
import { useServicesHealthStore } from '../services-health-store';

const get = vi.mocked(apiClient.get);

const SECOND = 1000;
const MINUTE = 60 * SECOND;

function answer(connector: 'healthy' | 'unhealthy') {
  get.mockImplementation(async (url: string) => {
    if (url === '/api/v1/health') {
      return { data: { status: 'healthy', services: {}, serviceNames: {} } };
    }
    const status = connector === 'healthy' ? 'healthy' : 'unhealthy';
    return { data: { status, services: { query: 'healthy', connector } } };
  });
}

/** Health checks sent so far (each one is two requests). */
const checks = () => get.mock.calls.length / 2;

describe('services-health-store background polling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    get.mockReset();
    useServicesHealthStore.setState({ backgroundCheckFailed: false, appServices: null });
  });

  afterEach(() => {
    useServicesHealthStore.getState().stopBackgroundPolling();
    vi.useRealTimers();
  });

  it('checks again within seconds after a failed check, and sees the recovery', async () => {
    answer('unhealthy');
    useServicesHealthStore.getState().startBackgroundPolling();
    await vi.advanceTimersByTimeAsync(0);
    expect(checks()).toBe(1);
    expect(useServicesHealthStore.getState().appServices?.connector).toBe('unhealthy');

    answer('healthy');
    await vi.advanceTimersByTimeAsync(10 * SECOND);

    expect(checks()).toBe(2);
    expect(useServicesHealthStore.getState().appServices?.connector).toBe('healthy');
    expect(useServicesHealthStore.getState().backgroundCheckFailed).toBe(false);
  });

  it('goes back to the slow interval once everything is healthy', async () => {
    answer('healthy');
    useServicesHealthStore.getState().startBackgroundPolling();
    await vi.advanceTimersByTimeAsync(9 * MINUTE);
    expect(checks()).toBe(1);

    await vi.advanceTimersByTimeAsync(1 * MINUTE);
    expect(checks()).toBe(2);
  });

  it('keeps checking every 10 seconds while a service stays down', async () => {
    answer('unhealthy');
    useServicesHealthStore.getState().startBackgroundPolling();
    await vi.advanceTimersByTimeAsync(30 * SECOND);

    expect(checks()).toBe(4);
  });

  it('checks once more after the API server itself could not be reached', async () => {
    get.mockRejectedValue(new Error('Network Error'));
    useServicesHealthStore.getState().startBackgroundPolling();
    await vi.advanceTimersByTimeAsync(0);
    expect(useServicesHealthStore.getState().apiServerReachable).toBe(false);

    answer('healthy');
    await vi.advanceTimersByTimeAsync(10 * SECOND);

    expect(useServicesHealthStore.getState().apiServerReachable).toBe(true);
  });

  it('stops checking when polling stops, even with a check in flight', async () => {
    let release: () => void = () => {};
    get.mockImplementation(() => new Promise((resolve) => {
      release = () => resolve({ data: { status: 'unhealthy', services: {} } });
    }));
    useServicesHealthStore.getState().startBackgroundPolling();
    useServicesHealthStore.getState().stopBackgroundPolling();
    release();
    await vi.advanceTimersByTimeAsync(10 * MINUTE);

    expect(checks()).toBe(1);
  });

  it('runs one chain of checks when restarted while a check is in flight', async () => {
    answer('unhealthy');
    const store = useServicesHealthStore.getState();
    store.startBackgroundPolling();
    store.stopBackgroundPolling();
    store.startBackgroundPolling();
    await vi.advanceTimersByTimeAsync(10 * SECOND);

    // Two first checks (one per start), then one follow-up, not two.
    expect(checks()).toBe(3);
  });

  it('drops a check that finishes after polling restarted, so it cannot undo a newer one', async () => {
    const pending: Array<(connector: 'healthy' | 'unhealthy') => void> = [];
    get.mockImplementation((url: string) => new Promise((resolve) => {
      pending.push((connector) => resolve(url === '/api/v1/health'
        ? { data: { status: 'healthy', services: {}, serviceNames: {} } }
        : { data: { status: connector, services: { query: 'healthy', connector } } }));
    }));
    const store = useServicesHealthStore.getState();
    store.startBackgroundPolling(); // old run: its check is slow
    store.stopBackgroundPolling();
    store.startBackgroundPolling(); // new run
    const [oldInfra, oldServices, newInfra, newServices] = pending;

    newInfra('healthy');
    newServices('healthy');
    await vi.advanceTimersByTimeAsync(0);
    oldInfra('unhealthy');
    oldServices('unhealthy');
    await vi.advanceTimersByTimeAsync(0);

    expect(useServicesHealthStore.getState().appServices?.connector).toBe('healthy');
    expect(useServicesHealthStore.getState().backgroundCheckFailed).toBe(false);
  });

  it('ignores a second start while polling', async () => {
    answer('healthy');
    useServicesHealthStore.getState().startBackgroundPolling();
    useServicesHealthStore.getState().startBackgroundPolling();
    await vi.advanceTimersByTimeAsync(0);

    expect(checks()).toBe(1);
  });
});
