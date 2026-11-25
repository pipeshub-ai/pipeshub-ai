/**
 * CPD10-92 (found on the fault stack, IX-DD-2b under `on`): a head read that
 * arrives between the store returning and the driver noticing waits for the
 * reconnection, briefly; one on a store that stays away is refused at the
 * grace, never left to mongoose's buffer timeout.
 */
import { EventEmitter } from 'node:events';
import { expect } from 'chai';
import sinon from 'sinon';
import { ConnectionStates } from 'mongoose';
import {
  connectedWithin,
  RECONNECT_GRACE_MS,
} from '../../../src/libs/repositories/control-plane/head-revisions/mongo';

class FakeConnection extends EventEmitter {
  readyState: number = ConnectionStates.disconnected;

  reconnect(): void {
    this.readyState = ConnectionStates.connected;
    this.emit('connected');
  }
}

describe('CPD10-92: head reads across a reconnection', () => {
  let clock: sinon.SinonFakeTimers;
  beforeEach(() => {
    clock = sinon.useFakeTimers();
  });
  afterEach(() => clock.restore());

  it('a connected store is read at once', async () => {
    const connection = new FakeConnection();
    connection.readyState = ConnectionStates.connected;
    await connectedWithin(connection);
    expect(connection.listenerCount('connected')).to.equal(0);
  });

  it('a read just before the driver reconnects waits for it and goes on', async () => {
    const connection = new FakeConnection();
    let settled = false;
    const read = connectedWithin(connection).then(() => {
      settled = true;
    });
    await clock.tickAsync(RECONNECT_GRACE_MS - 1);
    expect(settled).to.equal(false);
    connection.reconnect();
    await read;
    expect(connection.listenerCount('connected')).to.equal(0);
  });

  it('a store that stays away is refused at the grace, and nothing is left listening', async () => {
    const connection = new FakeConnection();
    const read = connectedWithin(connection).catch((error: unknown) => error);
    await clock.tickAsync(RECONNECT_GRACE_MS);
    expect(await read).to.be.instanceOf(Error);
    expect(connection.listenerCount('connected')).to.equal(0);
  });
});
