/**
 * Which connectors are waiting on the server to turn sync on or off. Every surface
 * that starts the change (the card's switch, the setup dialog) writes here, so the
 * card shows the wait wherever it began.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useConnectorsStore } from '../store';

const store = () => useConnectorsStore.getState();

describe('syncTogglePending', () => {
  beforeEach(() => store().reset());

  it('sets, reads and clears the pending direction', () => {
    store().setSyncTogglePending('c1', 'on');
    expect(store().syncTogglePending.c1).toBe('on');

    store().clearSyncTogglePending('c1');
    expect('c1' in store().syncTogglePending).toBe(false);
  });

  it('keeps each connector separate', () => {
    store().setSyncTogglePending('c1', 'on');
    store().setSyncTogglePending('c2', 'off');
    store().clearSyncTogglePending('c1');
    expect(store().syncTogglePending).toEqual({ c2: 'off' });
  });

  it('is dropped with the connector caches', () => {
    store().setSyncTogglePending('c1', 'off');
    store().removeConnectorInstanceCaches('c1');
    expect('c1' in store().syncTogglePending).toBe(false);
  });

  it('is dropped when the connector is removed', () => {
    store().setSyncTogglePending('c1', 'on');
    store().removeConnectorInstance('c1');
    expect('c1' in store().syncTogglePending).toBe(false);
  });
});
