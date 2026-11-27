import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { KeyValueStoreFactory } from '../../../src/libs/keyValueStore/keyValueStoreFactory';

// An encrypted value the Python services stored JSON-encoded ("iv:ct:tag", with the quotes) reads as the text inside, as the
// values this service writes do; plain values, JSON text and anything else read as kept; a compare-and-set against the text
// that get returned succeeds on either stored form, with the store's own compare as the check.
describe('KeyValueStoreService stored forms', () => {
  const C = `${'a1'.repeat(12)}:${'b2'.repeat(8)}:${'c3'.repeat(16)}`;
  let kept: Map<string, unknown>;
  let store: any;

  function service(): KeyValueStoreService {
    (KeyValueStoreService as any).instance = undefined;
    return KeyValueStoreService.getInstance({
      storeType: 'redis',
      storeConfig: {},
      redisConfig: {},
      secretKey: 'k',
      algorithm: 'aes-256-gcm',
    } as any);
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

  it('reads a JSON-encoded ciphertext as its text, and everything else as kept', async () => {
    const s = service();
    kept.set('/q', JSON.stringify(C));
    kept.set('/raw', C);
    kept.set('/json', '{"a":1}');
    kept.set('/str', '"just a string"');
    expect(await s.get('/q')).to.equal(C);
    expect(await s.get('/raw')).to.equal(C);
    expect(await s.get('/json')).to.equal('{"a":1}');
    expect(await s.get('/str')).to.equal('"just a string"');
  });

  it('compare-and-sets against the text get returned, on either stored form, once', async () => {
    const s = service();
    kept.set('/q', JSON.stringify(C));
    expect(await s.compareAndSet('/q', C, 'new')).to.equal(true);
    expect(kept.get('/q')).to.equal('new');
    kept.set('/q', JSON.stringify(C));
    expect(await s.compareAndSet('/q', 'other', 'new')).to.equal(false);
    expect(kept.get('/q')).to.equal(JSON.stringify(C));
    expect(store.compareAndSet.callCount).to.equal(3); // one retry for the quoted form; a real mismatch is not retried
  });
  it('keeps the quoted form when a compare-and-set replaces a quoted ciphertext with another', async () => {
    const s = service();
    const next = `${'d4'.repeat(12)}:${'e5'.repeat(8)}:${'f6'.repeat(16)}`;
    kept.set('/lease', JSON.stringify(C));
    expect(await s.compareAndSet('/lease', C, next)).to.equal(true);
    expect(kept.get('/lease')).to.equal(JSON.stringify(next)); // still the form its earlier readers read
    expect(await s.get('/lease')).to.equal(next);
  });
});
