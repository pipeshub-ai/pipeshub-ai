/**
 * O-1: per-org envelope encryption of journal content (REPORT 6.16). The
 * Mongo replica-set half is `data-protection.mongo.itest.ts`.
 */
import { expect } from 'chai';
import { createDecipheriv, createHash } from 'node:crypto';
import {
  InMemoryJournalStore,
  InMemoryPayloadStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryDataKeyStore } from '../../../src/modules/agent-harness/adapters/in-memory-data-key-store';
import { KmsKeyEncryptionKey } from '../../../src/modules/agent-harness/adapters/crypto/kms-key-encryption-key';
import { platformKeyEncryptionKey } from '../../../src/modules/agent-harness/adapters/crypto/hkdf-key-encryption-key';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  DATA_KEY_CACHE_TTL_MS,
  DATA_KEY_ROTATE_AFTER_MS,
  DATA_KEY_ROTATE_AFTER_SEALS,
  SealedContent,
  SEALED_FORMAT_VERSION,
} from '../../../src/modules/agent-harness/domain/data-protection';
import {
  JournalContentUnavailableError,
  JournalPayloadMissingError,
} from '../../../src/modules/agent-harness/domain/errors';
import {
  JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
  JournalEvent,
  JournalEventInput,
} from '../../../src/modules/agent-harness/domain/journal';
import { RunProjector } from '../../../src/modules/agent-harness/projection/run-projector';
import {
  DURABLE_SCENARIOS,
  journalOnlySteps,
  projectorContext,
  readJournal,
  runSteps,
} from '../../contracts/support/durable';
import {
  dataKeyRing,
  FakeKms,
  TEST_KEK_SECRET,
  testKek,
} from './support/data-protection';
import { position, turnStarted, workerEvent } from './support/journal-fixtures';

const SENTINEL = 'SENTINEL-4f1c-secret-customer-text';
const DEK_LOG_SENTINEL = 'never-log-key-material';

const recording = (): { logger: HarnessLogger; lines: unknown[][] } => {
  const lines: unknown[][] = [];
  const at =
    (level: string) =>
    (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([level, message, meta]);
    };
  return {
    lines,
    logger: {
      debug: at('debug'),
      info: at('info'),
      warn: at('warn'),
      error: at('error'),
    },
  };
};

const secretOutcome = (text = SENTINEL): JournalEventInput =>
  workerEvent('tool.outcome', {
    invocationId: 'inv-1',
    status: 'ok',
    receipt: { note: text },
    labels: { integrity: 'external' },
  });

const expectMissing = async (
  work: Promise<unknown>,
  reason: JournalPayloadMissingError['reason'],
): Promise<void> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(JournalPayloadMissingError);
    expect((error as JournalPayloadMissingError).reason).to.equal(reason);
    return;
  }
  expect.fail(`expected journal_payload_missing (${reason})`);
};

const expectUnavailable = async (
  work: Promise<unknown>,
  reason: JournalContentUnavailableError['reason'],
): Promise<void> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(JournalContentUnavailableError);
    expect((error as JournalContentUnavailableError).reason).to.equal(reason);
    return;
  }
  expect.fail(`expected unavailable (${reason})`);
};

describe('O-1 envelope encryption of the journal', () => {
  const setup = (options: Parameters<typeof dataKeyRing>[0] = {}) => {
    const ring = dataKeyRing(options);
    const raw = new InMemoryJournalStore();
    const payloads = new InMemoryPayloadStore();
    return {
      ...ring,
      raw,
      payloads,
      journal: new SealedJournalStore(raw, ring.cipher, options.logger),
      service: new JournalService(payloads, ring.cipher),
    };
  };

  const append = async (
    h: ReturnType<typeof setup>,
    events: JournalEventInput[],
    overrides: Partial<Parameters<typeof position>[0]> = {},
  ) => {
    const prepared = await h.service.prepare({
      ...position(overrides),
      writer: 'worker',
      events,
    });
    return h.journal.append(prepared, undefined);
  };

  describe('O-1a/O-1c at rest and transparent', () => {
    it('stores no plaintext data and keeps the envelope plain', async () => {
      const h = setup();
      await append(h, [turnStarted(1), secretOutcome()], {
        attemptId: 'attempt-1',
        fence: 3,
      });
      const stored = await h.raw.readStream('org-1', 'session-1', 0, 10);
      expect(JSON.stringify(stored)).not.to.contain(SENTINEL);
      for (const event of stored) {
        expect(event.data).to.equal(undefined);
        expect(event.sealed?.v).to.equal(SEALED_FORMAT_VERSION);
        expect(event.sealed?.kv).to.equal(1);
        expect(event.sealed?.kid).to.be.a('string');
      }
      expect(stored[1]).to.include({
        orgId: 'org-1',
        streamId: 'session-1',
        seq: 2,
        runId: 'run-1',
        attemptId: 'attempt-1',
        fence: 3,
        type: 'tool.outcome',
        critical: true,
        writer: 'worker',
        visibility: 'owner',
      });
      const read = await h.journal.readStream('org-1', 'session-1', 0, 10);
      expect(read.map((event) => event.data)).to.deep.equal([
        turnStarted(1).data,
        secretOutcome().data,
      ]);
      expect(read.every((event) => event.sealed === undefined)).to.equal(true);
    });

    it('O-1b: claim-checked payload bytes are sealed and addressed by what is stored', async () => {
      const h = setup();
      const big = secretOutcome(
        `${SENTINEL}${'x'.repeat(JOURNAL_INLINE_PAYLOAD_MAX_BYTES)}`,
      );
      await append(h, [big]);
      const [stored] = await h.raw.readStream('org-1', 'session-1', 0, 1);
      const ref = stored!.payloadRef!;
      const bytes = h.payloads.objects.get(ref.ref)!;
      expect(bytes.toString('latin1')).not.to.contain(SENTINEL);
      expect(ref.size).to.equal(bytes.length);
      expect(ref.hash).to.equal(
        `sha256:${createHash('sha256').update(bytes).digest('hex')}`,
      );
      const [read] = await h.service.resolve(
        await h.journal.readStream('org-1', 'session-1', 0, 1),
      );
      expect(read?.data).to.deep.equal(big.data);
    });

    it('O-1c: every golden journal projects to the same frames after sealing', async () => {
      for (const scenario of DURABLE_SCENARIOS) {
        const golden = readJournal(scenario);
        const h = setup();
        for (const event of golden.events) {
          const { seq: _seq, ts: _ts, ...rest } = event;
          _seq;
          _ts;
          await h.journal.append(
            {
              orgId: rest.orgId,
              sessionId: rest.sessionId,
              streamId: rest.streamId,
              runId: rest.runId ?? golden.runId,
              ...(rest.attemptId !== undefined
                ? { attemptId: rest.attemptId }
                : {}),
              ...(rest.fence !== undefined ? { fence: rest.fence } : {}),
              writer: rest.writer,
              events: [
                {
                  type: rest.type,
                  schemaVersion: rest.schemaVersion,
                  critical: rest.critical,
                  visibility: rest.visibility,
                  ...(rest.labels !== undefined ? { labels: rest.labels } : {}),
                  data: rest.data ?? {},
                },
              ],
            },
            undefined,
          );
        }
        const opened = await h.journal.readStream(
          golden.events[0]!.orgId,
          golden.events[0]!.streamId,
          0,
          10_000,
        );
        const sameTimes = opened.map(
          (event, index): JournalEvent => ({
            ...event,
            ts: golden.events[index]!.ts,
          }),
        );
        expect(sameTimes, scenario).to.deep.equal(golden.events);
        const project = (events: JournalEvent[]) =>
          runSteps(
            new RunProjector(projectorContext(golden)),
            journalOnlySteps({ ...golden, events }),
          );
        expect(project(sameTimes), scenario).to.deep.equal(
          project(golden.events),
        );
      }
    });
  });

  describe('O-1d crypto-shredding', () => {
    it('makes an org unreadable, leaves other orgs, and reaches cached replicas within the TTL', async () => {
      let now = 1_000_000;
      const store = new InMemoryDataKeyStore();
      const h = setup({ store, now: () => now });
      const replica = dataKeyRing({ store, now: () => now });
      const replicaJournal = new SealedJournalStore(h.raw, replica.cipher);
      await append(h, [secretOutcome()]);
      await append(h, [secretOutcome()], { orgId: 'org-2' });
      const big = secretOutcome(
        `${SENTINEL}${'x'.repeat(JOURNAL_INLINE_PAYLOAD_MAX_BYTES)}`,
      );
      await append(h, [big]);
      expect(
        await replicaJournal.readStream('org-1', 'session-1', 0, 10),
      ).to.have.length(2);

      expect(await h.keys.shred('org-1')).to.equal(1);
      await expectMissing(
        h.journal.readStream('org-1', 'session-1', 0, 10),
        'shredded',
      );
      const [checked] = await h.raw.readStream('org-1', 'session-1', 1, 1);
      await expectMissing(h.service.resolve([checked!]), 'shredded');
      expect(
        await h.journal.readStream('org-2', 'session-1', 0, 10),
      ).to.have.length(1);

      // The replica still holds the unwrapped key until its cache expires.
      expect(
        await replicaJournal.readStream('org-1', 'session-1', 0, 1),
      ).to.have.length(1);
      now += DATA_KEY_CACHE_TTL_MS;
      await expectMissing(
        replicaJournal.readStream('org-1', 'session-1', 0, 1),
        'shredded',
      );
    });

    it('a write after shredding starts a fresh key that old content cannot use', async () => {
      const h = setup();
      await append(h, [secretOutcome()]);
      const [before] = await h.raw.readStream('org-1', 'session-1', 0, 1);
      await h.keys.shred('org-1');
      await append(h, [turnStarted(2)]);
      const [, after] = await h.raw.readStream('org-1', 'session-1', 0, 2);
      expect(after?.sealed?.kid).not.to.equal(before?.sealed?.kid);
      await expectMissing(
        h.journal.readStream('org-1', 'session-1', 0, 1),
        'shredded',
      );
      expect(
        (await h.journal.readStream('org-1', 'session-1', 1, 1))[0]?.data,
      ).to.deep.equal({ turn: 2 });
    });
  });

  describe('O-1e rotation', () => {
    it('seals new writes with the new version and keeps older versions readable', async () => {
      const h = setup();
      await append(h, [turnStarted(1)]);
      const rotated = await h.keys.rotate('org-1');
      expect(rotated.version).to.equal(2);
      await append(h, [turnStarted(2)]);
      const stored = await h.raw.readStream('org-1', 'session-1', 0, 10);
      expect(stored.map((event) => event.sealed?.kv)).to.deep.equal([1, 2]);
      expect(stored[1]?.sealed?.kid).to.equal(rotated.kid);
      expect(
        (await h.journal.readStream('org-1', 'session-1', 0, 10)).map(
          (e) => e.data,
        ),
      ).to.deep.equal([{ turn: 1 }, { turn: 2 }]);
    });

    it('O-1h: moves new writes to a fresh version once the active key is old', async () => {
      let now = 5_000;
      const h = setup({ now: () => now });
      await append(h, [turnStarted(1)]);
      now += DATA_KEY_ROTATE_AFTER_MS;
      await append(h, [turnStarted(2)]);
      const stored = await h.raw.readStream('org-1', 'session-1', 0, 10);
      expect(stored.map((event) => event.sealed?.kv)).to.deep.equal([1, 2]);
      expect(
        await h.journal.readStream('org-1', 'session-1', 0, 10),
      ).to.have.length(2);
    });

    it('KR-1: moves new writes to a fresh version once the seal count reaches the limit', async () => {
      const { logger, lines } = recording();
      const store = new InMemoryDataKeyStore();
      const h = setup({
        store,
        logger,
        keyOptions: { rotateAfterSeals: 10, sealReservation: 4 },
      });
      for (let turn = 1; turn <= 12; turn += 1) {
        await append(h, [turnStarted(turn)]);
      }
      const stored = await h.raw.readStream('org-1', 'session-1', 0, 20);
      // Blocks of 4: totals 4 and 8 fit under 10, the third block (12) does not.
      expect(stored.map((event) => event.sealed?.kv)).to.deep.equal([
        1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2,
      ]);
      const [first] = await store.list('org-1');
      expect(store.seals.get(first!.kid)).to.equal(12);
      expect(
        (await h.journal.readStream('org-1', 'session-1', 0, 20)).map(
          (event) => event.data,
        ),
      ).to.deep.equal(
        Array.from({ length: 12 }, (_, index) => ({ turn: index + 1 })),
      );
      expect(
        lines
          .filter(([, message]) => message === 'data_key.created')
          .map(([, , meta]) => (meta as { reason: string }).reason),
      ).to.deep.equal(['first_write', 'seal_count']);
      expect(DATA_KEY_ROTATE_AFTER_SEALS).to.equal(2 ** 28);
    });

    it('KR-2: replicas never seal past the limit under one key and converge on one version at a time', async () => {
      const store = new InMemoryDataKeyStore();
      const keyOptions = { rotateAfterSeals: 10, sealReservation: 3 };
      const replicas = Array.from({ length: 3 }, () =>
        dataKeyRing({ store, keyOptions }),
      );
      // A replica that reserved a block and died: its seals still count.
      await dataKeyRing({ store, keyOptions }).keys.active('org-1');
      const used = new Map<string, number>();
      for (let round = 0; round < 8; round += 1) {
        const keys = await Promise.all(
          replicas.map((replica) => replica.keys.active('org-1')),
        );
        for (const key of keys) {
          used.set(key.kid, (used.get(key.kid) ?? 0) + 1);
        }
      }
      const versions = await store.list('org-1');
      expect(versions.length).to.be.greaterThan(1);
      expect(versions.map((key) => key.version)).to.deep.equal(
        versions.map((_, index) => index + 1),
      );
      for (const key of versions) {
        expect(used.get(key.kid) ?? 0).to.be.at.most(10);
        expect(store.seals.get(key.kid) ?? 0).to.be.at.least(
          used.get(key.kid) ?? 0,
        );
      }
      // The dead replica's block is counted, never used.
      expect(store.seals.get(versions[0]!.kid)).to.be.greaterThan(
        used.get(versions[0]!.kid)!,
      );
    });
  });

  describe('O-1f tampering and mixed fleets', () => {
    const tampered = async (
      change: (sealed: SealedContent, events: JournalEvent[]) => void,
    ) => {
      const h = setup();
      await append(h, [turnStarted(1), secretOutcome()]);
      const stored = await h.raw.readStream('org-1', 'session-1', 0, 10);
      change(stored[1]!.sealed!, stored);
      const tamperedJournal = new SealedJournalStore(
        Object.assign(Object.create(h.raw) as InMemoryJournalStore, { readStream: async () => stored }),
        h.cipher,
      );
      return tamperedJournal.readStream('org-1', 'session-1', 0, 10);
    };

    it('refuses a flipped ciphertext byte as corrupt', async () => {
      await expectMissing(
        tampered((sealed) => {
          const ct = Buffer.from(sealed.ct, 'base64');
          ct[0] = (ct[0] ?? 0) ^ 0xff;
          sealed.ct = ct.toString('base64');
        }),
        'corrupt',
      );
    });

    it('refuses content moved to another event (AAD) as corrupt', async () => {
      await expectMissing(
        tampered((sealed, events) => {
          events[0]!.sealed = { ...sealed };
        }),
        'corrupt',
      );
    });

    it('refuses an unknown sealed format as retryable, never misreads it', async () => {
      await expectUnavailable(
        tampered((sealed) => {
          sealed.v = SEALED_FORMAT_VERSION + 1;
        }),
        'unsupported_version',
      );
    });

    it('refuses a truncated claim-checked blob as corrupt, and an unknown blob format as retryable', async () => {
      const h = setup();
      const big = secretOutcome('x'.repeat(JOURNAL_INLINE_PAYLOAD_MAX_BYTES));
      await append(h, [big]);
      const [stored] = await h.raw.readStream('org-1', 'session-1', 0, 1);
      const ref = stored!.payloadRef!;
      const original = h.payloads.objects.get(ref.ref)!;
      const reference = (bytes: Buffer) => ({
        ...stored!,
        payloadRef: {
          ref: ref.ref,
          size: bytes.length,
          hash: `sha256:${createHash('sha256').update(bytes).digest('hex')}`,
        },
      });
      const truncated = original.subarray(0, 20);
      h.payloads.objects.set(ref.ref, truncated);
      await expectMissing(h.service.resolve([reference(truncated)]), 'corrupt');
      const future = Buffer.from(original);
      future[4] = SEALED_FORMAT_VERSION + 1;
      h.payloads.objects.set(ref.ref, future);
      await expectUnavailable(
        h.service.resolve([reference(future)]),
        'unsupported_version',
      );
    });

    it('O-1i: refuses a stored event with plaintext data, or with no content at all', async () => {
      const h = setup();
      h.raw.appendNow({
        ...position(),
        writer: 'worker',
        events: [turnStarted(1)],
      });
      await expectMissing(
        h.journal.readStream('org-1', 'session-1', 0, 1),
        'plaintext',
      );
      h.raw.appendNow({
        ...position(),
        writer: 'worker',
        events: [{ ...turnStarted(2), data: undefined as never }],
      });
      await expectMissing(
        h.journal.readStream('org-1', 'session-1', 1, 1),
        'missing',
      );
    });
  });

  describe('O-1g keys', () => {
    it("the platform KEK is the deployment SECRET_KEY's, read on first use: every replica derives the same one", async () => {
      const saved = process.env.SECRET_KEY;
      try {
        delete process.env.SECRET_KEY;
        const unset = platformKeyEncryptionKey();
        expect(() => unset.id).to.throw('SECRET_KEY environment variable is required');
        process.env.SECRET_KEY = '4a548e7c602a2872ec983e28e3e80d5fc8aa81bf422510d120daaba403c7988d';
        const one = platformKeyEncryptionKey();
        const two = platformKeyEncryptionKey();
        expect(one.id).to.equal(two.id);
        expect(one.id).to.equal(
          testKek(createHash('sha256').update('4a548e7c602a2872ec983e28e3e80d5fc8aa81bf422510d120daaba403c7988d').digest()).id,
        );
        const context = { orgId: 'o', kid: 'k', version: 1 };
        const wrapped = await one.wrap(Buffer.alloc(32, 3), context);
        expect(await two.unwrap(wrapped, context)).to.deep.equal(Buffer.alloc(32, 3));
        process.env.SECRET_KEY = 'ce1807e913c97047dafef68295a8968c894cc8d1d8cc2c38fa84e06e7d5c0f06';
        expect(platformKeyEncryptionKey().id).to.not.equal(one.id);
      } finally {
        if (saved === undefined) delete process.env.SECRET_KEY;
        else process.env.SECRET_KEY = saved;
      }
    });

    it('derives the KEK with HKDF: independent of the configuration cipher key, identified by a fingerprint', async () => {
      const kek = testKek();
      const wrapped = Buffer.from(
        await kek.wrap(Buffer.alloc(32, 1), {
          orgId: 'o',
          kid: 'k',
          version: 1,
        }),
        'base64',
      );
      // The platform's configuration cipher uses the secret itself as its key.
      const decipher = createDecipheriv(
        'aes-256-gcm',
        TEST_KEK_SECRET,
        wrapped.subarray(0, 12),
      );
      decipher.setAuthTag(wrapped.subarray(12, 28));
      expect(() =>
        Buffer.concat([
          decipher.update(wrapped.subarray(28)),
          decipher.final(),
        ]),
      ).to.throw();
      expect(kek.id).to.match(/^hkdf:[0-9a-f]{16}$/);
      expect(testKek(Buffer.alloc(32, 8)).id).not.to.equal(kek.id);
      expect(kek.id).not.to.contain(
        TEST_KEK_SECRET.toString('hex').slice(0, 16),
      );
    });

    it('a wrong KEK refuses to unwrap: retryable, logged without key material', async () => {
      const store = new InMemoryDataKeyStore();
      const h = setup({ store });
      await append(h, [secretOutcome()]);
      const { logger, lines } = recording();
      const wrong = dataKeyRing({
        store,
        kek: testKek(Buffer.alloc(32, 9)),
        logger,
      });
      const journal = new SealedJournalStore(h.raw, wrong.cipher, logger);
      await expectUnavailable(
        journal.readStream('org-1', 'session-1', 0, 1),
        'unwrap_failed',
      );
      const logged = JSON.stringify(lines);
      expect(logged).to.contain('data_key.unwrap_failed');
      expect(logged).to.contain('journal.content_unreadable');
      const [key] = await store.list('org-1');
      expect(logged).not.to.contain(key!.wrapped);
      expect(logged).not.to.contain(TEST_KEK_SECRET.toString('hex'));
      expect(logged).not.to.contain(SENTINEL);
    });

    it('KMS seam: the same round trip, rotation and shred over a fake KMS', async () => {
      const kms = new FakeKms(['kms-key-1']);
      const h = setup({ kek: new KmsKeyEncryptionKey(kms, 'kms-key-1') });
      await append(h, [secretOutcome()]);
      await h.keys.rotate('org-1');
      await append(h, [turnStarted(2)]);
      const [first] = await h.store.list('org-1');
      expect(first?.kekId).to.equal('kms:kms-key-1');
      const fresh = dataKeyRing({
        store: h.store,
        kek: new KmsKeyEncryptionKey(kms, 'kms-key-1'),
      });
      const read = await new SealedJournalStore(h.raw, fresh.cipher).readStream(
        'org-1',
        'session-1',
        0,
        10,
      );
      expect(read.map((event) => event.data)).to.deep.equal([
        secretOutcome().data,
        { turn: 2 },
      ]);
      expect(kms.decrypts).to.equal(2);
      const other = dataKeyRing({
        store: h.store,
        kek: new KmsKeyEncryptionKey(new FakeKms(['kms-key-1']), 'kms-key-1'),
      });
      await expectUnavailable(
        new SealedJournalStore(h.raw, other.cipher).readStream(
          'org-1',
          'session-1',
          0,
          1,
        ),
        'unwrap_failed',
      );
      await h.keys.shred('org-1');
      await expectMissing(
        h.journal.readStream('org-1', 'session-1', 0, 1),
        'shredded',
      );
    });
  });

  describe('O-1h key cache', () => {
    it('reads the store once per org within the TTL and evicts beyond the org bound', async () => {
      let now = 0;
      const store = new InMemoryDataKeyStore();
      const ring = dataKeyRing({
        store,
        now: () => now,
        keyOptions: { maxCachedOrgs: 2 },
      });
      await ring.keys.active('a');
      const reads = store.reads;
      await ring.keys.active('a');
      expect(store.reads).to.equal(reads);
      await ring.keys.active('b');
      await ring.keys.active('c');
      const beforeEvicted = store.reads;
      await ring.keys.active('a');
      expect(store.reads).to.equal(beforeEvicted + 1);
      now += DATA_KEY_CACHE_TTL_MS;
      await ring.keys.active('a');
      expect(store.reads).to.equal(beforeEvicted + 2);
    });

    it('concurrent first writes on several replicas converge on one key', async () => {
      const store = new InMemoryDataKeyStore();
      const replicas = Array.from({ length: 5 }, () => dataKeyRing({ store }));
      const keys = await Promise.all(
        replicas.map((replica) => replica.keys.active('org-1')),
      );
      expect(new Set(keys.map((key) => key.kid)).size).to.equal(1);
      expect(await store.list('org-1')).to.have.length(1);
      expect(keys.every((key) => key.key.equals(keys[0]!.key))).to.equal(true);
    });

    it('logs key creation and shredding with ids only', async () => {
      const { logger, lines } = recording();
      const ring = dataKeyRing({ logger });
      const key = await ring.keys.active('org-1');
      await ring.keys.rotate('org-1');
      await ring.keys.shred('org-1');
      expect(
        lines.map(([level, message]) => `${String(level)}:${String(message)}`),
      ).to.deep.equal([
        'info:data_key.created',
        'info:data_key.created',
        'info:data_key.shredded',
      ]);
      expect(lines[0]![2]).to.include({
        orgId: 'org-1',
        version: 1,
        reason: 'first_write',
      });
      expect(lines[1]![2]).to.include({ version: 2, reason: 'rotation' });
      expect(lines[2]![2]).to.include({ orgId: 'org-1', versions: 2 });
      const logged = JSON.stringify(lines);
      expect(logged).not.to.contain(key.key.toString('base64'));
      expect(logged).not.to.contain(key.key.toString('hex'));
      expect(logged).not.to.contain(DEK_LOG_SENTINEL);
    });
  });
});
