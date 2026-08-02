import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';

/**
 * Regression cover for reconnect-after-stop.
 *
 * ioredis `quit()` closes a client permanently, so reusing it in connect()
 * leaves the producer dead: every later publish fails with "Error publishing
 * to Redis stream". The shared producer singleton is stopped by several
 * request handlers, so this is reachable in normal operation.
 */
describe('Redis Streams reconnect after disconnect', () => {
  const ioredisPath = require.resolve('ioredis');
  const svcPath = require.resolve(
    '../../../src/libs/services/redis-streams.service',
  );
  let original: NodeJS.Module | undefined;
  let clients: any[];

  const makeClient = () => {
    const c: any = new EventEmitter();
    c.status = 'ready';
    c.connect = sinon.stub().resolves();
    // Mirrors ioredis: status stays 'ready' right after quit() resolves,
    // which is exactly why a status check is not a reliable signal here.
    c.quit = sinon.stub().resolves();
    c.xadd = sinon.stub().resolves('1-0');
    c.pipeline = sinon.stub();
    return c;
  };

  const logger: any = {
    info: sinon.stub(), debug: sinon.stub(),
    warn: sinon.stub(), error: sinon.stub(),
  };
  const config: any = { type: 'redis', host: 'redis', port: 6379, db: 0 };

  let svc: any;

  beforeEach(() => {
    clients = [];
    original = require.cache[ioredisPath];
    const FakeRedis = function (this: any) {
      const c = makeClient();
      clients.push(c);
      return c;
    } as any;

    require.cache[ioredisPath] = {
      ...original!,
      exports: { Redis: FakeRedis, default: FakeRedis, RedisOptions: {} },
    } as any;
    delete require.cache[svcPath];
    svc = require('../../../src/libs/services/redis-streams.service');
  });

  afterEach(() => {
    if (original) require.cache[ioredisPath] = original;
    else delete require.cache[ioredisPath];
    delete require.cache[svcPath];
    sinon.restore();
  });

  it('replaces the producer client on disconnect so it can start again', async () => {
    class P extends svc.BaseRedisStreamsProducerConnection {}
    const producer = new (P as any)(config, logger);

    await producer.connect();
    await producer.publish('mail-events', { key: 'k', value: { a: 1 } });
    expect(clients.length).to.equal(1);

    await producer.disconnect();
    // A dead client must not be carried forward.
    expect(clients.length).to.equal(2);

    await producer.connect();
    await producer.publish('mail-events', { key: 'k', value: { a: 1 } });

    expect(clients[1].xadd.called).to.be.true;
    expect(producer.isConnected()).to.be.true;
  });

  it('still swaps the client when quit() rejects', async () => {
    class P extends svc.BaseRedisStreamsProducerConnection {}
    const producer = new (P as any)(config, logger);

    await producer.connect();
    clients[0].quit.rejects(new Error('connection reset'));

    await producer.disconnect();

    expect(clients.length).to.equal(2);
    await producer.connect();
    await producer.publish('mail-events', { key: 'k', value: { a: 1 } });
    expect(clients[1].xadd.called).to.be.true;
  });

  it('does not create a client when disconnecting an unstarted producer', async () => {
    class P extends svc.BaseRedisStreamsProducerConnection {}
    const producer = new (P as any)(config, logger);

    await producer.disconnect();

    expect(clients.length).to.equal(1);
  });
});
