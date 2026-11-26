import 'reflect-metadata';
import crypto from 'crypto';
import { expect } from 'chai';
import { InMemoryKeyValueStore } from '../../../../src/libs/keyValueStore/providers/InMemoryKeyValueStore';
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import sinon from 'sinon';
import { KeyValueStoreFactory } from '../../../../src/libs/keyValueStore/keyValueStoreFactory';
import { StoreType } from '../../../../src/libs/keyValueStore/constants/KeyValueStoreType';
import {
  createStore,
  runCli,
} from '../../../../src/libs/keyValueStore/rotation/rotateSecretKeyCli';
import { platformCipherKey } from '../../../../src/libs/encryptor/secret-key-ring';
import { rotateEncryptedValues } from '../../../../src/libs/keyValueStore/rotation/rotateSecretKey';

// Both pass the key ring's strength rule: each is a write target in some test.
const OLD = 'old-secret-key-for-tests-0001-Rk7!';
const NEW = 'new-secret-key-for-tests-0002-Zq8!';
const hex = (s: string) => crypto.createHash('sha256').update(s).digest('hex');
// getInstance is a process-wide singleton, so build independent ciphers directly.
const cipher = (s: string) =>
  EncryptionService.withRing('aes-256-gcm', [hex(s), platformCipherKey(s)]);

describe('rotateEncryptedValues', () => {
  let store: InMemoryKeyValueStore<string>;
  beforeEach(async () => {
    // Make the singleton hold a third key, to prove the tool does not use it.
    EncryptionService.getInstance(
      'aes-256-gcm',
      hex('some-other-key-entirely'),
    );
    store = new InMemoryKeyValueStore<string>();
    await store.createKey(
      '/services/aiModels',
      cipher(OLD).encrypt('{"llm":[{"apiKey":"k1"}]}'),
    );
    await store.createKey(
      '/scoped/services/aiModels',
      cipher(OLD).encrypt('{"llm":[]}'),
    );
    await store.createKey('/services/endpoints', '{"api":"http://x"}');
    await store.createKey('/services/flag', 'true');
  });

  it('rotates encrypted values and leaves plain values', async () => {
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    expect(r).to.deep.include({ rotated: 2, plaintext: 2, failed: [] });
    expect(
      cipher(NEW).decrypt((await store.getKey('/services/aiModels'))!),
    ).to.equal('{"llm":[{"apiKey":"k1"}]}');
    expect(
      cipher(NEW).decrypt((await store.getKey('/scoped/services/aiModels'))!),
    ).to.equal('{"llm":[]}');
    expect(await store.getKey('/services/endpoints')).to.equal(
      '{"api":"http://x"}',
    );
    expect(await store.getKey('/services/flag')).to.equal('true');
  });

  it('reads a value sealed under the HKDF key of the old secret (SECRET_KEY_KDF=hkdf)', async () => {
    const hkdf = EncryptionService.withKey(
      'aes-256-gcm',
      platformCipherKey(OLD),
    );
    await store.createKey('/hkdf', hkdf.encrypt('{"a":1}'));
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    expect(r).to.deep.include({ rotated: 3, failed: [] });
    expect(cipher(NEW).decrypt((await store.getKey('/hkdf'))!)).to.equal(
      '{"a":1}',
    );
  });

  it('keeps the JSON-quoted form some writers use', async () => {
    await store.createKey('/q', JSON.stringify(cipher(OLD).encrypt('{"a":1}')));
    await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    const raw = (await store.getKey('/q'))!;
    expect(raw.startsWith('"')).to.equal(true);
    expect(cipher(NEW).decrypt(JSON.parse(raw))).to.equal('{"a":1}');
  });

  it('writes nothing on a dry run', async () => {
    const before = await store.getKey('/services/aiModels');
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: true });
    expect(r.rotated).to.equal(2);
    expect(await store.getKey('/services/aiModels')).to.equal(before);
  });

  it('aborts before any write when an encrypted-format value cannot be decrypted, naming only the key', async () => {
    await store.createKey(
      '/bad',
      cipher('third-unrelated-key').encrypt('SENSITIVE-VALUE'),
    );
    const before = await store.getKey('/services/aiModels');
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    expect(r.failed).to.deep.equal(['/bad']);
    expect(r.rotated).to.equal(0);
    expect(await store.getKey('/services/aiModels')).to.equal(before);
    expect(JSON.stringify(r)).to.not.contain('SENSITIVE-VALUE');
  });

  it('is safe to re-run after a partial run', async () => {
    await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    // simulate: one key still on the old key, the other already rotated
    await store.updateValue(
      '/scoped/services/aiModels',
      cipher(OLD).encrypt('{"llm":[]}'),
    );
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    expect(r).to.deep.include({ rotated: 1, alreadyRotated: 1, failed: [] });
    expect(
      cipher(NEW).decrypt((await store.getKey('/scoped/services/aiModels'))!),
    ).to.equal('{"llm":[]}');
  });

  it('reports a value that changed during the run instead of overwriting it', async () => {
    const racing = Object.create(store) as InMemoryKeyValueStore<string>;
    racing.compareAndSet = async (k, e, n) => {
      if (k === '/services/aiModels')
        await store.updateValue(k, cipher(OLD).encrypt('changed'));
      return store.compareAndSet(k, e, n);
    };
    const r = await rotateEncryptedValues(racing, OLD, NEW, { dryRun: false });
    expect(r.conflicts).to.deep.equal(['/services/aiModels']);
    expect(
      cipher(OLD).decrypt((await store.getKey('/services/aiModels'))!),
    ).to.equal('changed');
    expect(r.rotated).to.equal(1);
  });

  it('skips a key that cannot be read as a string (wrong type) without crashing', async () => {
    const odd = Object.create(store) as InMemoryKeyValueStore<string>;
    odd.getAllKeys = async () => [
      ...(await store.getAllKeys()),
      'bull:queue:wrongtype',
    ];
    odd.getKey = async (k) => {
      if (k === 'bull:queue:wrongtype')
        throw new Error(
          'WRONGTYPE Operation against a key holding the wrong kind of value',
        );
      return store.getKey(k);
    };
    const r = await rotateEncryptedValues(odd, OLD, NEW, { dryRun: false });
    expect(r.skipped).to.deep.equal(['bull:queue:wrongtype']);
    expect(r.rotated).to.equal(2);
  });

  it('refuses identical or empty secrets', async () => {
    let err: Error | undefined;
    await rotateEncryptedValues(store, OLD, OLD, { dryRun: false }).catch(
      (e) => (err = e),
    );
    expect(err).to.be.instanceOf(Error);
  });
  it('rotates an encrypted field inside plain JSON (the storage setting shape)', async () => {
    const s3 = cipher(OLD).encrypt(
      '{"accessKeyId":"AK","secretAccessKey":"SK"}',
    );
    const azure = cipher(OLD).encrypt(
      'DefaultEndpointsProtocol=https;AccountKey=x',
    );
    await store.createKey(
      '/services/storage',
      JSON.stringify({
        storageType: 's3',
        s3,
        nested: { azureBlob: azure, keep: 'plain' },
      }),
    );
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    expect(r.failed).to.deep.equal([]);
    expect(r.rotated).to.equal(3);
    const v = JSON.parse((await store.getKey('/services/storage'))!);
    expect(v.storageType).to.equal('s3');
    expect(v.nested.keep).to.equal('plain');
    expect(cipher(NEW).decrypt(v.s3)).to.equal(
      '{"accessKeyId":"AK","secretAccessKey":"SK"}',
    );
    expect(cipher(NEW).decrypt(v.nested.azureBlob)).to.equal(
      'DefaultEndpointsProtocol=https;AccountKey=x',
    );
  });

  it('aborts when an encrypted field inside plain JSON cannot be decrypted', async () => {
    const bad = cipher('third-unrelated-key').encrypt('x');
    await store.createKey('/services/storage', JSON.stringify({ s3: bad }));
    const before = await store.getKey('/services/aiModels');
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    expect(r.failed).to.deep.equal(['/services/storage']);
    expect(await store.getKey('/services/aiModels')).to.equal(before);
  });

  it('aborts before any write on a read error that is not WRONGTYPE', async () => {
    const before = await store.getKey('/services/aiModels');
    const flaky = Object.create(store) as InMemoryKeyValueStore<string>;
    flaky.getKey = async (k) => {
      if (k === '/scoped/services/aiModels')
        throw new Error('Connection is closed.');
      return store.getKey(k);
    };
    let err: Error | undefined;
    await rotateEncryptedValues(flaky, OLD, NEW, { dryRun: false }).catch(
      (e) => (err = e),
    );
    expect(err?.message).to.contain('/scoped/services/aiModels');
    expect(await store.getKey('/services/aiModels')).to.equal(before);
  });

  it('leaves keys with an expiry alone and reports them', async () => {
    const withTtl = Object.assign(Object.create(store), {
      hasExpiry: async (k: string) => k === '/scoped/services/aiModels',
    }) as InMemoryKeyValueStore<string> & {
      hasExpiry: (k: string) => Promise<boolean>;
    };
    const before = await store.getKey('/scoped/services/aiModels');
    const r = await rotateEncryptedValues(withTtl, OLD, NEW, { dryRun: false });
    expect(r.expiring).to.deep.equal(['/scoped/services/aiModels']);
    expect(r.rotated).to.equal(1);
    expect(await store.getKey('/scoped/services/aiModels')).to.equal(before);
  });

  it('rotates an encrypted string at any depth', async () => {
    const depth = 20000;
    const enc = cipher(OLD).encrypt('deep');
    await store.createKey(
      '/deep',
      '['.repeat(depth) + JSON.stringify(enc) + ']'.repeat(depth),
    );
    const r = await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    expect(r.failed).to.deep.equal([]);
    expect(r.rotated).to.equal(3);
    const raw = (await store.getKey('/deep'))!;
    expect(raw.length).to.equal(2 * depth + enc.length + 2);
    expect(cipher(NEW).decrypt(raw.slice(depth + 1, -depth - 1))).to.equal(
      'deep',
    );
  });

  it('keeps the rest of a JSON value byte for byte, numbers past 2^53 included', async () => {
    const enc = cipher(OLD).encrypt('SK');
    const raw = `{ "id": 9007199254740993, "ratio": 1.50, "s3": "${enc}",\n  "keep": "plain" }`;
    await store.createKey('/services/storage', raw);
    await rotateEncryptedValues(store, OLD, NEW, { dryRun: false });
    const after = (await store.getKey('/services/storage'))!;
    const rotated = /"s3": "([^"]+)"/.exec(after)![1]!;
    expect(cipher(NEW).decrypt(rotated)).to.equal('SK');
    expect(after).to.equal(raw.replace(enc, rotated));
  });

  it('with skipUnreadable, rotates the rest and leaves every failed value exactly as it is', async () => {
    const lookalike = cipher('third-unrelated-key').encrypt('x');
    const mixed = JSON.stringify({ a: cipher(OLD).encrypt('A'), b: lookalike });
    await store.createKey('/bad', lookalike);
    await store.createKey('/mixed', mixed);
    const r = await rotateEncryptedValues(store, OLD, NEW, {
      dryRun: false,
      skipUnreadable: true,
    });
    expect(r.failed).to.deep.equal(['/bad', '/mixed']);
    expect(r.rotated).to.equal(2);
    expect(await store.getKey('/bad')).to.equal(lookalike);
    // The readable field is not moved either, so the value stays whole on the old key.
    expect(await store.getKey('/mixed')).to.equal(mixed);
    expect(
      cipher(NEW).decrypt((await store.getKey('/services/aiModels'))!),
    ).to.contain('k1');
  });

  it('lets the earlier-key run recover a mixed value that a skipUnreadable run left alone', async () => {
    const EARLIER = 'earlier-secret-key-0123456789';
    const mixed = JSON.stringify({
      a: cipher(OLD).encrypt('A'),
      b: cipher(EARLIER).encrypt('B'),
    });
    // The value as a skipUnreadable run leaves it (previous test): unchanged.
    await store.createKey('/mixed', mixed);
    const first = await rotateEncryptedValues(store, EARLIER, OLD, {
      dryRun: false,
    });
    expect(first.failed).to.deep.equal([]);
    const second = await rotateEncryptedValues(store, OLD, NEW, {
      dryRun: false,
    });
    expect(second.failed).to.deep.equal([]);
    const m = JSON.parse((await store.getKey('/mixed'))!);
    expect(cipher(NEW).decrypt(m.a)).to.equal('A');
    expect(cipher(NEW).decrypt(m.b)).to.equal('B');
  });

  it('refuses, before any write, a new secret the key ring refuses on the hardened profile, naming why', async () => {
    const before = await store.getKey('/services/aiModels');
    for (const [n, reason] of [
      ['k3P9vQ2mX7tL1wZ8', 'shorter than 32 characters'],
      [
        'abcdefghijklmnopqrstuvwxyzABCDEFGHIJ',
        'repeated or sequential pattern',
      ],
      ['aaaaaaaaaaaaaaaaaaaaaaaaaaabcdefgh', 'repeats too few characters'],
    ] as const) {
      let err: Error | undefined;
      await rotateEncryptedValues(store, OLD, n, { dryRun: false }).catch(
        (e) => (err = e),
      );
      expect(err?.message, n).to.contain(reason);
      expect(err?.message, n).to.not.contain(n);
    }
    expect(await store.getKey('/services/aiModels')).to.equal(before);
  });

  it('rejects empty and placeholder secrets', async () => {
    for (const [o, n] of [
      ['', NEW],
      [OLD, ''],
      [OLD, 'my-secret'],
      [OLD, 'short'],
    ] as const) {
      let err: Error | undefined;
      await rotateEncryptedValues(store, o, n, { dryRun: false }).catch(
        (e) => (err = e),
      );
      expect(err, `${o}/${n}`).to.be.instanceOf(Error);
    }
  });

  describe('CLI', () => {
    it('builds the store the API builds when only PIPESHUB_STORE is set', async () => {
      const saved = { ...process.env };
      try {
        process.env.PIPESHUB_STORE = 'postgres';
        delete process.env.KV_STORE_TYPE;
        process.env.SECRET_KEY =
          'bebe4bcfae5282d8e801761bb8acf01feb1080daa1d12f0fb516bb12861487e6';
        const build = sinon
          .stub(KeyValueStoreFactory, 'createStore')
          .returns({} as never);
        await createStore();
        expect(build.firstCall.args[0]).to.equal(StoreType.Postgres);
      } finally {
        sinon.restore();
        process.env = saved;
      }
    });

    const run = async (opts: {
      env?: NodeJS.ProcessEnv;
      argv?: string[];
      st?: any;
    }) => {
      const out: string[] = [];
      const code = await runCli({
        env: opts.env ?? { OLD_SECRET_KEY: OLD, NEW_SECRET_KEY: NEW },
        argv: opts.argv ?? ['node', 'cli'],
        createStore: async () => opts.st ?? store,
        log: (l) => out.push(l),
        error: (l) => out.push(l),
      });
      return { code, out: out.join('\n') };
    };

    it('is a dry run without --apply', async () => {
      const before = await store.getKey('/services/aiModels');
      const { code, out } = await run({});
      expect(code).to.equal(0);
      expect(out).to.contain('DRY RUN');
      expect(out).to.not.contain('APPLIED');
      expect(await store.getKey('/services/aiModels')).to.equal(before);
    });

    it('writes with --apply, prints APPLIED and the reminder, and no values', async () => {
      const { code, out } = await run({ argv: ['node', 'cli', '--apply'] });
      expect(code).to.equal(0);
      expect(out).to.contain('APPLIED');
      expect(out).to.contain('not rotated');
      expect(out).to.not.contain('apiKey');
      expect(
        cipher(NEW).decrypt((await store.getKey('/services/aiModels'))!),
      ).to.contain('k1');
    });

    it('exits 2 when a secret is missing', async () => {
      expect((await run({ env: { OLD_SECRET_KEY: OLD } })).code).to.equal(2);
    });

    it('exits 1 and names the key when a value cannot be decrypted', async () => {
      await store.createKey('/bad', cipher('third-unrelated-key').encrypt('x'));
      const { code, out } = await run({ argv: ['node', 'cli', '--apply'] });
      expect(code).to.equal(1);
      expect(out).to.contain('failed: /bad');
      expect(out).to.not.contain('APPLIED');
    });

    it('applies and leaves the failed keys alone with --skip-unreadable', async () => {
      const lookalike = cipher('third-unrelated-key').encrypt('x');
      await store.createKey('/bad', lookalike);
      const { code, out } = await run({
        argv: ['node', 'cli', '--apply', '--skip-unreadable'],
      });
      expect(code).to.equal(0);
      expect(out).to.contain('failed: /bad');
      expect(out).to.contain('APPLIED');
      expect(await store.getKey('/bad')).to.equal(lookalike);
      expect(
        cipher(NEW).decrypt((await store.getKey('/services/aiModels'))!),
      ).to.contain('k1');
    });

    it('exits 1 when the store has no keys', async () => {
      const { code, out } = await run({
        st: new InMemoryKeyValueStore<string>(),
      });
      expect(code).to.equal(1);
      expect(out).to.contain('No keys found');
    });

    it('exits 1 and does not print APPLIED when a write did not apply', async () => {
      const racing = Object.create(store) as InMemoryKeyValueStore<string>;
      racing.compareAndSet = async () => false;
      const { code, out } = await run({
        argv: ['node', 'cli', '--apply'],
        st: racing,
      });
      expect(code).to.equal(1);
      expect(out).to.contain('INCOMPLETE');
      expect(out).to.not.contain('APPLIED');
    });

    it('exits 1 on a new secret the key ring would refuse', async () => {
      const { code } = await run({
        env: { OLD_SECRET_KEY: OLD, NEW_SECRET_KEY: 'k3P9vQ2mX7tL1wZ8' },
        argv: ['n', 'c', '--apply'],
      });
      expect(code).to.equal(1);
    });

    it('exits 1 on a placeholder new secret', async () => {
      const { code } = await run({
        env: { OLD_SECRET_KEY: OLD, NEW_SECRET_KEY: 'my-secret' },
        argv: ['n', 'c', '--apply'],
      });
      expect(code).to.equal(1);
    });
  });
});
