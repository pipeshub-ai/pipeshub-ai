import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { KeyValueStoreFactory } from '../../../src/libs/keyValueStore/keyValueStoreFactory';
import * as edition from '../../../src/edition';

// The value codec of the edition (`kvValueCodec`): none by default; given one, every write is encoded and every read and
// watch decoded with the value's key, and a compare-and-set compares decoded values but leaves the atomic compare to the store.
describe('KeyValueStoreService value codec', () => {
  const config: any = {
    storeType: 'redis',
    storeConfig: {},
    redisConfig: {},
    secretKey: 'k',
    algorithm: 'aes-256-gcm',
  };
  let store: any;
  let kept: Map<string, unknown>;

  function service(): KeyValueStoreService {
    (KeyValueStoreService as any).instance = undefined;
    return KeyValueStoreService.getInstance(config);
  }

  beforeEach(() => {
    kept = new Map();
    store = {
      createKey: sinon.stub().callsFake(async (k: string, v: unknown) => {
        kept.set(k, v);
      }),
      updateValue: sinon.stub().callsFake(async (k: string, v: unknown) => {
        kept.set(k, v);
      }),
      getKey: sinon.stub().callsFake(async (k: string) => kept.get(k) ?? null),
      compareAndSet: sinon
        .stub()
        .callsFake(async (k: string, e: unknown, n: unknown) => {
          if ((kept.get(k) ?? null) !== e) return false;
          kept.set(k, n);
          return true;
        }),
      watchKey: sinon
        .stub()
        .callsFake(async (k: string, cb: (v: unknown) => void) => {
          cb(kept.get(k) ?? null);
        }),
      healthCheck: sinon.stub().resolves(true),
    };
    sinon.stub(KeyValueStoreFactory, 'createStore').returns(store);
    sinon
      .stub(
        require('../../../src/libs/services/logger.service').Logger,
        'getInstance',
      )
      .returns(createMockLogger());
  });

  afterEach(() => sinon.restore());

  it('keeps every value as given by default', async () => {
    expect(edition.kvValueCodec()).to.equal(null);
    const s = service();
    await s.set('/a', 'v');
    expect(kept.get('/a')).to.equal('v');
    expect(await s.get('/a')).to.equal('v');
  });

  it("encodes and decodes with the value's key when the edition has a codec", async () => {
    sinon.stub(edition, 'kvValueCodec').returns({
      encode: (key: string, v: unknown) => `enc(${key}|${v})`,
      decode: (key: string, v: unknown) =>
        String(v).replace(`enc(${key}|`, '').replace(/\)$/, ''),
    });
    const s = service();
    await s.set('/a', 'v');
    expect(kept.get('/a')).to.equal('enc(/a|v)');
    expect(await s.get('/a')).to.equal('v');
    const seen: unknown[] = [];
    await s.watchKey('/a', (v) => seen.push(v));
    expect(seen).to.deep.equal(['v']);
    expect(await s.compareAndSet('/a', 'other', 'w')).to.equal(false);
    expect(await s.compareAndSet('/a', 'v', 'w')).to.equal(true);
    expect(kept.get('/a')).to.equal('enc(/a|w)');
  });
});
