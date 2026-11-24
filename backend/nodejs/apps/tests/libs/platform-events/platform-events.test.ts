import { expect } from 'chai';
import {
  emitPlatformEvent,
  onPlatformEvent,
  platformEventStats,
  resetPlatformEvents,
  setPlatformEventErrorReporter,
} from '../../../src/libs/platform-events';

describe('libs/platform-events', () => {
  beforeEach(() => resetPlatformEvents());
  afterEach(() => resetPlatformEvents());

  it('emits with no listeners without doing anything', () => {
    expect(() =>
      emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 }),
    ).not.to.throw();
    expect(platformEventStats().handlerErrors).to.equal(0);
  });

  it('does not read the payload when nobody listens', () => {
    const payload = new Proxy(
      { target: 't', removed: 1 },
      {
        get(): never {
          throw new Error('payload was read');
        },
      },
    );
    expect(() => emitPlatformEvent('docstore.ttl.swept', payload)).not.to.throw();
    expect(platformEventStats().handlerErrors).to.equal(0);
  });

  it('calls a listener synchronously with the payload and the name', () => {
    const seen: unknown[] = [];
    onPlatformEvent('docstore.ttl.swept', (payload, name) => {
      seen.push([name, payload.target, payload.removed]);
    });
    emitPlatformEvent('docstore.ttl.swept', { target: 'outbox', removed: 3 });
    expect(seen).to.deep.equal([['docstore.ttl.swept', 'outbox', 3]]);
  });

  it('only calls the listeners of the emitted name', () => {
    let calls = 0;
    onPlatformEvent('docstore.ttl.sweep_failed', () => {
      calls += 1;
    });
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    expect(calls).to.equal(0);
  });

  it('passes the payload by reference, without cloning', () => {
    // Why listeners must not mutate: every listener and the emitter share one object.
    const payload = { target: 't', removed: 1 };
    const received: unknown[] = [];
    onPlatformEvent('docstore.ttl.swept', (p) => {
      received.push(p);
    });
    onPlatformEvent('*', (p) => {
      received.push(p);
    });
    emitPlatformEvent('docstore.ttl.swept', payload);
    expect(received).to.have.length(2);
    expect(received[0]).to.equal(payload);
    expect(received[1]).to.equal(payload);
  });

  it('gives listeners an empty object when the event has no payload', () => {
    let received: unknown;
    onPlatformEvent('docstore.row_policy.violated', (p) => {
      received = p;
    });
    emitPlatformEvent('docstore.row_policy.violated');
    expect(received).to.deep.equal({});
  });

  it('runs named listeners in subscription order, then wildcard listeners', () => {
    const order: string[] = [];
    onPlatformEvent('*', () => {
      order.push('any-1');
    });
    onPlatformEvent('docstore.ttl.swept', () => {
      order.push('named-1');
    });
    onPlatformEvent('docstore.ttl.swept', () => {
      order.push('named-2');
    });
    onPlatformEvent('*', () => {
      order.push('any-2');
    });
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    expect(order).to.deep.equal(['named-1', 'named-2', 'any-1', 'any-2']);
  });

  it('calls wildcard listeners for every event, with its name', () => {
    const names: string[] = [];
    onPlatformEvent('*', (_payload, name) => {
      names.push(name);
    });
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    emitPlatformEvent('docstore.ttl.sweep_failed', { target: 't' });
    expect(names).to.deep.equal([
      'docstore.ttl.swept',
      'docstore.ttl.sweep_failed',
    ]);
  });

  it('keeps a throwing listener away from the emitter, counts it and still runs the rest', () => {
    const reported: [string, unknown][] = [];
    setPlatformEventErrorReporter((name, error) => {
      reported.push([name, error]);
    });
    const boom = new Error('boom');
    let later = 0;
    onPlatformEvent('docstore.ttl.swept', () => {
      throw boom;
    });
    onPlatformEvent('docstore.ttl.swept', () => {
      later += 1;
    });
    onPlatformEvent('*', () => {
      later += 1;
    });
    expect(() =>
      emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 }),
    ).not.to.throw();
    expect(later).to.equal(2);
    expect(platformEventStats().handlerErrors).to.equal(1);
    expect(reported).to.deep.equal([['docstore.ttl.swept', boom]]);
  });

  it('counts a listener whose promise rejects', async () => {
    onPlatformEvent('docstore.ttl.swept', async () => {
      throw new Error('later');
    });
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    await new Promise((resolve) => setImmediate(resolve));
    expect(platformEventStats().handlerErrors).to.equal(1);
  });

  it('survives an error reporter that throws', () => {
    setPlatformEventErrorReporter(() => {
      throw new Error('reporter');
    });
    onPlatformEvent('docstore.ttl.swept', () => {
      throw new Error('listener');
    });
    expect(() =>
      emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 }),
    ).not.to.throw();
    expect(platformEventStats().handlerErrors).to.equal(1);
  });

  it('stops calling a listener once it unsubscribes; unsubscribing twice is harmless', () => {
    let a = 0;
    let b = 0;
    const offA = onPlatformEvent('docstore.ttl.swept', () => {
      a += 1;
    });
    onPlatformEvent('docstore.ttl.swept', () => {
      b += 1;
    });
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    offA();
    offA();
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    expect([a, b]).to.deep.equal([1, 2]);
  });

  it('removes only the subscription that was cancelled when one handler is added twice', () => {
    let calls = 0;
    const handler = (): void => {
      calls += 1;
    };
    const off = onPlatformEvent('docstore.ttl.swept', handler);
    onPlatformEvent('docstore.ttl.swept', handler);
    off();
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    expect(calls).to.equal(1);
  });

  it('lets a listener unsubscribe during an emit without skipping the others', () => {
    const order: string[] = [];
    const off = onPlatformEvent('docstore.ttl.swept', () => {
      order.push('first');
      off();
    });
    onPlatformEvent('docstore.ttl.swept', () => {
      order.push('second');
    });
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    emitPlatformEvent('docstore.ttl.swept', { target: 't', removed: 1 });
    expect(order).to.deep.equal(['first', 'second', 'second']);
  });
});
