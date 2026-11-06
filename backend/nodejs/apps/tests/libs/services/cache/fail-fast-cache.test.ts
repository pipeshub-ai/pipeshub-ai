import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  FAIL_FAST_CACHE_RETRY_MS,
  FAIL_FAST_CACHE_TIMEOUT_MS,
  FailFastCache,
} from '../../../../src/libs/services/cache/fail-fast-cache';
import { ICacheService } from '../../../../src/libs/services/cache/cacheService.interface';

const never = <T>(): Promise<T> => new Promise<T>(() => {});

type StubbedCache = { [K in keyof ICacheService]: sinon.SinonStub };

function innerCache(): StubbedCache {
  return {
    get: sinon.stub().resolves('v'),
    set: sinon.stub().resolves(),
    delete: sinon.stub().resolves(),
    increment: sinon.stub().resolves(3),
    disconnect: sinon.stub().resolves(),
    isConnected: sinon.stub().returns(true),
  };
}

describe('FailFastCache', () => {
  let clock: sinon.SinonFakeTimers;
  let inner: StubbedCache;
  let warn: sinon.SinonStub;
  let cache: FailFastCache;

  beforeEach(() => {
    clock = sinon.useFakeTimers({ now: 1_000_000 });
    inner = innerCache();
    warn = sinon.stub();
    cache = new FailFastCache(inner, { warn });
  });

  afterEach(() => {
    clock.restore();
    sinon.restore();
  });

  it('passes calls, values and options through and leaves no timer behind', async () => {
    expect(await cache.get('k', { namespace: 'n' })).to.equal('v');
    await cache.set('k', 1, { ttl: 30 });
    await cache.delete('k');
    expect(await cache.increment('c')).to.equal(3);
    expect(inner.get.calledOnceWith('k', { namespace: 'n' })).to.equal(true);
    expect(inner.set.calledOnceWith('k', 1, { ttl: 30 })).to.equal(true);
    expect(inner.delete.calledOnceWith('k')).to.equal(true);
    expect(inner.increment.calledOnceWith('c')).to.equal(true);
    expect(clock.countTimers()).to.equal(0);
    expect(cache.isConnected()).to.equal(true);
    expect(warn.called).to.equal(false);
  });

  it('a hung read is a miss once the timeout passes', async () => {
    inner.get.returns(never());
    let settled = false;
    const read = cache.get('k').then((value) => {
      settled = true;
      return value;
    });
    await clock.tickAsync(FAIL_FAST_CACHE_TIMEOUT_MS - 1);
    expect(settled).to.equal(false);
    await clock.tickAsync(1);
    expect(await read).to.equal(null);
    expect(FAIL_FAST_CACHE_TIMEOUT_MS).to.equal(250);
  });

  it('after a failure every call skips the inner cache until the window ends', async () => {
    inner.get.rejects(new Error('down'));
    expect(await cache.get('k')).to.equal(null);
    expect(cache.isConnected()).to.equal(false);

    await cache.set('k', 1);
    await cache.delete('k');
    expect(await cache.get('k')).to.equal(null);
    let error: unknown;
    await cache.increment('c').catch((e: unknown) => (error = e));
    expect(error).to.be.instanceOf(Error);
    expect(inner.get.callCount).to.equal(1);
    expect(inner.set.called || inner.delete.called || inner.increment.called).to.equal(false);

    await clock.tickAsync(FAIL_FAST_CACHE_RETRY_MS - 1);
    expect(await cache.get('k')).to.equal(null);
    expect(inner.get.callCount).to.equal(1);

    await clock.tickAsync(1);
    inner.get.resolves('back');
    expect(cache.isConnected()).to.equal(true);
    expect(await cache.get('k')).to.equal('back');
    expect(inner.get.callCount).to.equal(2);
    expect(FAIL_FAST_CACHE_RETRY_MS).to.equal(5_000);
  });

  it('when the window ends one call tries the inner cache, and the others skip until it answers', async () => {
    inner.get.rejects(new Error('down'));
    await cache.get('k');
    await clock.tickAsync(FAIL_FAST_CACHE_RETRY_MS);

    let answer: (value: string) => void = () => {};
    inner.get.returns(new Promise<string>((resolve) => (answer = resolve)));
    const probe = cache.get('k');
    const others = [cache.get('k'), cache.get('k')];
    expect(inner.get.callCount).to.equal(2);
    expect(cache.isConnected()).to.equal(false);
    expect(await Promise.all(others)).to.deep.equal([null, null]);

    answer('back');
    expect(await probe).to.equal('back');
    inner.get.resolves('v');
    expect(await Promise.all([cache.get('a'), cache.get('b')])).to.deep.equal(['v', 'v']);
    expect(inner.get.callCount).to.equal(4);
    expect(cache.isConnected()).to.equal(true);
  });

  it('a failed write is dropped and a failed increment rejects with the cause', async () => {
    inner.set.rejects(new Error('set failed'));
    await cache.set('k', 1);
    await clock.tickAsync(FAIL_FAST_CACHE_RETRY_MS);
    inner.increment.rejects(new Error('incr failed'));
    let error: unknown;
    await cache.increment('c').catch((e: unknown) => (error = e));
    expect((error as Error).message).to.equal('incr failed');
  });

  it('a hung increment rejects at the timeout', async () => {
    inner.increment.returns(never());
    const result = cache.increment('c').catch((e: unknown) => e);
    await clock.tickAsync(FAIL_FAST_CACHE_TIMEOUT_MS);
    expect(((await result) as Error).message).to.match(/timed out after 250 ms/);
  });

  it('warns at most once every 30 s, however many calls fail', async () => {
    inner.get.rejects(new Error('down'));
    for (let i = 0; i < 4; i += 1) {
      await cache.get('k');
      await clock.tickAsync(FAIL_FAST_CACHE_RETRY_MS);
    }
    expect(inner.get.callCount).to.equal(4);
    expect(warn.callCount).to.equal(1);
    expect(warn.firstCall.args[1]).to.deep.equal({ error: 'down' });

    await clock.tickAsync(30_000);
    await cache.get('k');
    expect(warn.callCount).to.equal(2);
  });

  it('reports the inner cache as disconnected when it is', () => {
    inner.isConnected.returns(false);
    expect(cache.isConnected()).to.equal(false);
  });

  it('disconnects the inner cache', async () => {
    await cache.disconnect();
    expect(inner.disconnect.calledOnce).to.equal(true);
  });
});
