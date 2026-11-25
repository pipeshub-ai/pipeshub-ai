/**
 * What every claim-check PayloadStore must do (PS-1..PS-8), and every store
 * that also keeps PayloadReferences (PR-1..PR-4, T5) or writes in a
 * transaction (TP-1..TP-3, R3 M8): the in-memory store
 * (`payload-store.contract.test.ts`), MongoDB and platform storage on its
 * local backend (`payload-store.mongo.itest.ts`) and PostgreSQL
 * (`payload-store.pg.itest.ts`).
 */
import { expect } from 'chai';
import { createHash, randomBytes } from 'node:crypto';
import { PayloadStoreUnavailableError } from '../../../../src/modules/agent-harness/domain/errors';
import {
  PayloadReferences,
  PayloadStore,
  TransactionalPayloadStore,
} from '../../../../src/modules/agent-harness/ports/payload-store';

export const payloadHash = (bytes: Buffer): string =>
  `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

export interface PayloadStoreFixture {
  store: PayloadStore;
  /** Deletes the stored object behind `ref`, as a lost blob or a purge would. */
  remove(orgId: string, ref: string): Promise<void>;
}

export interface PayloadStoreContractOptions {
  /** A store of the same kind whose backend fails (an adapter with no backend has none). */
  unavailable?: () => Promise<{ store: PayloadStore; restore(): Promise<void> }>;
  /** A document store: the name the other document store gives the same payload (R46-CARRY: a carried payload keeps its name). */
  carriedName?: (ref: string) => string;
}

/** An org id in the shape every store takes (PostgreSQL checks it). */
const newOrg = (): string => randomBytes(12).toString('hex');

export function describePayloadStoreContract(
  name: string,
  create: () => Promise<PayloadStoreFixture>,
  options: PayloadStoreContractOptions = {},
): void {
  describe(`PayloadStore contract: ${name}`, function () {
    this.timeout(30_000);
    let f: PayloadStoreFixture;
    let org: string;

    beforeEach(async () => {
      f = await create();
      org = newOrg();
    });

    it('PS-1: get returns exactly the bytes put, by the ref put answered', async () => {
      const bytes = Buffer.concat([Buffer.from(Array.from({ length: 256 }, (_, i) => i)), randomBytes(4_096)]);
      const ref = await f.store.put(org, payloadHash(bytes), bytes, { sessionId: 'ses-1', runId: 'run-1' });
      expect(ref).to.be.a('string').and.not.equal('');
      expect((await f.store.get(org, ref))?.equals(bytes)).to.equal(true);
    });

    it('PS-2: putting the same content again answers the same ref', async () => {
      const bytes = randomBytes(128);
      const first = await f.store.put(org, payloadHash(bytes), bytes);
      const second = await f.store.put(org, payloadHash(bytes), bytes, { sessionId: 'ses-2' });
      expect(second).to.equal(first);
      expect((await f.store.get(org, first))?.equals(bytes)).to.equal(true);
    });

    it("PS-3: another org's ref reads as absent", async () => {
      const bytes = randomBytes(64);
      const ref = await f.store.put(org, payloadHash(bytes), bytes);
      expect(await f.store.get(newOrg(), ref)).to.equal(null);
    });

    it('PS-4: the same content in two orgs is two objects, each readable only by its org', async () => {
      const other = newOrg();
      const bytes = randomBytes(64);
      const mine = await f.store.put(org, payloadHash(bytes), bytes);
      const theirs = await f.store.put(other, payloadHash(bytes), bytes);
      expect(theirs).to.not.equal(mine);
      expect((await f.store.get(org, mine))?.equals(bytes)).to.equal(true);
      expect((await f.store.get(other, theirs))?.equals(bytes)).to.equal(true);
      expect(await f.store.get(org, theirs)).to.equal(null);
    });

    it('PS-5: an object that is gone reads as absent, not as a failure', async () => {
      const bytes = randomBytes(64);
      const ref = await f.store.put(org, payloadHash(bytes), bytes);
      await f.remove(org, ref);
      expect(await f.store.get(org, ref)).to.equal(null);
    });

    it("PS-6: a ref of another store's form reads as absent", async () => {
      for (const ref of ['', 'memory:x:y', 'mongo:x:y', 'https://example.invalid/agent-harness/other.bin']) {
        expect(await f.store.get(org, ref), ref).to.equal(null);
      }
    });

    it('PS-7: concurrent puts of one content all answer one ref; of distinct contents, distinct refs', async () => {
      const same = randomBytes(256);
      const refs = await Promise.all(Array.from({ length: 8 }, () => f.store.put(org, payloadHash(same), same)));
      expect(new Set(refs).size).to.equal(1);
      const distinct = Array.from({ length: 8 }, () => randomBytes(64));
      const distinctRefs = await Promise.all(distinct.map((bytes) => f.store.put(org, payloadHash(bytes), bytes)));
      expect(new Set(distinctRefs).size).to.equal(distinct.length);
      const read = await Promise.all(distinctRefs.map((ref) => f.store.get(org, ref)));
      read.forEach((bytes, i) => expect(bytes?.equals(distinct[i]!), String(i)).to.equal(true));
    });

    const { carriedName } = options;
    if (carriedName !== undefined) {
      it('PS-9: a payload the document-store move carried reads by the name the other store gave it, and only for its org', async () => {
        const bytes = randomBytes(96);
        const ref = await f.store.put(org, payloadHash(bytes), bytes);
        const other = carriedName(ref);
        expect(other).to.not.equal(ref);
        expect((await f.store.get(org, other))?.equals(bytes)).to.equal(true);
        expect(await f.store.get(newOrg(), other)).to.equal(null);
      });
    }

    const { unavailable } = options;
    if (unavailable !== undefined) {
      it('PS-8: a failing backend is "unavailable" on put and get, never a missing payload', async () => {
        const bytes = randomBytes(64);
        const ref = await f.store.put(org, payloadHash(bytes), bytes);
        const down = await unavailable();
        try {
          for (const [operation, work] of [
            ['get', () => down.store.get(org, ref)],
            ['put', () => down.store.put(org, payloadHash(bytes), bytes)],
          ] as const) {
            let error: unknown;
            try {
              await work();
            } catch (caught) {
              error = caught;
            }
            expect(error, operation).to.be.instanceOf(PayloadStoreUnavailableError);
            expect((error as PayloadStoreUnavailableError).operation).to.equal(operation);
          }
        } finally {
          await down.restore();
        }
      });
    }
  });
}

export interface PayloadReferencesFixture<Tx> {
  store: PayloadStore & PayloadReferences<Tx>;
  /** A transaction for `orgId`'s writes: a PostgreSQL unit runs as one organisation. */
  transact<T>(orgId: string, work: (tx: Tx) => Promise<T>): Promise<T>;
  /** What GC reads: whether a committed event references the payload. */
  isReferenced(orgId: string, ref: string): Promise<boolean>;
}

export interface PayloadReferencesContractOptions {
  /** The store marks inside the caller's transaction, so an aborted one marks nothing. */
  transactional?: boolean;
}

export function describePayloadReferencesContract<Tx>(
  name: string,
  create: () => Promise<PayloadReferencesFixture<Tx>>,
  options: PayloadReferencesContractOptions = {},
): void {
  describe(`PayloadReferences contract: ${name}`, function () {
    this.timeout(30_000);
    let f: PayloadReferencesFixture<Tx>;
    let org: string;
    const owned = async (orgId: string): Promise<string> => {
      const bytes = randomBytes(64);
      return f.store.put(orgId, payloadHash(bytes), bytes, { sessionId: 'ses-1', runId: 'run-1' });
    };

    beforeEach(async () => {
      f = await create();
      org = newOrg();
    });

    it('PR-1: a stored payload is unreferenced until a commit marks it; the others stay unreferenced', async () => {
      const [cited, uncited] = [await owned(org), await owned(org)];
      expect(await f.isReferenced(org, cited)).to.equal(false);
      await f.transact(org, (tx) => f.store.markReferenced(org, [cited], tx));
      expect(await f.isReferenced(org, cited)).to.equal(true);
      expect(await f.isReferenced(org, uncited)).to.equal(false);
    });

    it("PR-2: another org's refs and refs to nothing are ignored", async () => {
      const other = newOrg();
      const theirs = await owned(other);
      await f.transact(org, (tx) => f.store.markReferenced(org, [theirs, 'mongo:nothing:here', 'memory:nothing:here'], tx));
      expect(await f.isReferenced(other, theirs)).to.equal(false);
      await f.transact(org, (tx) => f.store.markReferenced(org, [], tx));
    });

    it('PR-3: two commits citing one payload both succeed and leave it referenced', async () => {
      const ref = await owned(org);
      await Promise.all([
        f.transact(org, (tx) => f.store.markReferenced(org, [ref], tx)),
        f.transact(org, (tx) => f.store.markReferenced(org, [ref], tx)),
      ]);
      expect(await f.isReferenced(org, ref)).to.equal(true);
    });

    if (options.transactional === true) {
      it('PR-4: a commit that aborts marks nothing', async () => {
        const ref = await owned(org);
        const aborted = new Error('the append failed after marking');
        let error: unknown;
        try {
          await f.transact(org, async (tx) => {
            await f.store.markReferenced(org, [ref], tx);
            throw aborted;
          });
        } catch (caught) {
          error = caught;
        }
        expect(error).to.equal(aborted);
        expect(await f.isReferenced(org, ref)).to.equal(false);
      });
    }
  });
}

export interface TransactionalPayloadFixture<Tx> {
  store: PayloadStore & PayloadReferences<Tx> & TransactionalPayloadStore<Tx>;
  transact<T>(orgId: string, work: (tx: Tx) => Promise<T>): Promise<T>;
  isReferenced(orgId: string, ref: string): Promise<boolean>;
}

/**
 * A payload stored and cited in one transaction (R3 M8, TP-1..TP-3): the
 * mark that follows its write sees it, even in a transaction whose snapshot
 * a read fixed first; with `transactional`, an aborted one leaves nothing.
 */
export function describeTransactionalPayloadStoreContract<Tx>(
  name: string,
  create: () => Promise<TransactionalPayloadFixture<Tx>>,
  options: PayloadReferencesContractOptions = {},
): void {
  describe(`TransactionalPayloadStore contract: ${name}`, function () {
    this.timeout(30_000);
    let f: TransactionalPayloadFixture<Tx>;
    let org: string;
    const putIn = (tx: Tx) => {
      const bytes = randomBytes(64);
      return f.store.putIn(tx, org, payloadHash(bytes), bytes, { sessionId: 'ses-1', runId: 'run-1' });
    };

    beforeEach(async () => {
      f = await create();
      org = newOrg();
    });

    it('TP-1: put and marked in one transaction after a first read: referenced, and it reads back', async () => {
      const ref = await f.transact(org, async (tx) => {
        await f.store.get(org, 'mongo:nothing:here');
        const stored = await putIn(tx);
        await f.store.markReferenced(org, [stored], tx);
        return stored;
      });
      expect(await f.isReferenced(org, ref)).to.equal(true);
      expect(await f.store.get(org, ref)).to.not.equal(null);
    });

    it('TP-2: put in a transaction and never cited: stored, unreferenced', async () => {
      const ref = await f.transact(org, (tx) => putIn(tx));
      expect(await f.store.get(org, ref)).to.not.equal(null);
      expect(await f.isReferenced(org, ref)).to.equal(false);
    });

    if (options.transactional === true) {
      it('TP-3: a transaction that aborts after the put leaves no payload', async () => {
        let ref = '';
        const aborted = new Error('the admission failed after the blob');
        await f
          .transact(org, async (tx) => {
            ref = await putIn(tx);
            throw aborted;
          })
          .catch((error: unknown) => {
            expect(error).to.equal(aborted);
          });
        expect(ref).to.not.equal('');
        expect(await f.store.get(org, ref)).to.equal(null);
      });
    }
  });
}
