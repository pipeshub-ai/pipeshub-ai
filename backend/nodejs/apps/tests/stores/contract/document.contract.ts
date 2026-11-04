/**
 * Contract every DocumentRepository must meet. Store-neutral: the store under
 * test supplies the repository and an id generator.
 */
import { expect } from 'chai';
import type {
  DocumentRepository,
  NewDocument,
} from '../../../src/libs/repositories/document';
import { ConflictError } from '../../../src/libs/repositories/errors';
import { StorageVendor } from '../../../src/modules/storage/types/storage.service.types';
import { DocumentBusy } from '../../../src/libs/repositories/document/types';
import { isSettled } from '../../../src/modules/storage/utils/storage-fingerprint';

export interface HiddenState {
  writeSeq: number;
  writers: { t: string; until: number }[];
}

export interface DocumentFixture {
  repo: DocumentRepository;
  newId(): string;
  /** Writes the hidden write state of one row straight into the store, bypassing the repository. */
  setHiddenState(orgId: string, id: string, state: HiddenState): Promise<void>;
  /** Reads it back the same way. */
  getHiddenState(orgId: string, id: string): Promise<HiddenState>;
  /**
   * A value that changes when the stored row is rewritten (PostgreSQL: `xmin`). Optional: a store without one skips only the
   * "row not rewritten" half of C10.
   */
  rowStamp?(orgId: string, id: string): Promise<string>;
  /**
   * Runs `body` while the caller holds an open unit of work of the store (a transaction), then rolls that unit back. Used by C13.
   */
  inCallerUnit?(orgId: string, body: () => Promise<void>): Promise<void>;
}

const HEX = /^[0-9a-f]{24}$/;
// A delete claim younger than this blocks a write of the revision; an older one is from a delete that died.
const STALE = 15 * 60 * 1000;
const fence = (now: number): { now: number; staleMs: number } => ({ now, staleMs: STALE });

export function describeDocumentContract(fx: DocumentFixture): void {
  const { repo } = fx;
  const mk = (orgId: string, over: Partial<NewDocument> = {}): NewDocument => ({
    orgId,
    initiatorUserId: null,
    documentName: `doc-${fx.newId()}`,
    isVersionedFile: false,
    storageVendor: StorageVendor.Local,
    extension: '.txt',
    ...over,
  });

  describe('create and read', () => {
    it('returns string ids and the stored defaults', async () => {
      const org = fx.newId();
      const user = fx.newId();
      const row = await repo.create(mk(org, { initiatorUserId: user, documentPath: `${org}/PipesHub/a` }));
      expect(row._id).to.match(HEX);
      expect(row.orgId).to.equal(org);
      expect(row.initiatorUserId).to.equal(user);
      expect(row.isDeleted).to.equal(false);
      expect(row.mutationCount).to.equal(1);
      expect(row.versionHistory).to.deep.equal([]);
      expect(row.__v).to.equal(0);
      expect(JSON.parse(JSON.stringify(row)).orgId).to.equal(org);
    });

    it('get finds a deleted row, getLive does not, and neither crosses orgs', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org));
      await repo.update(org, row._id, { isDeleted: true, deletedByUserId: fx.newId() });
      expect((await repo.get(org, row._id))?.isDeleted).to.equal(true);
      expect(await repo.getLive(org, row._id)).to.equal(null);
      expect(await repo.getLive(org, row._id, true)).to.equal(null);
      expect(await repo.get(fx.newId(), row._id)).to.equal(null);
    });

    it('getLive: the lean read has the same fields, without the schema defaults', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org));
      const full = await repo.getLive(org, row._id);
      const lean = await repo.getLive(org, row._id, true);
      expect(lean?._id).to.equal(row._id);
      expect(lean?.orgId).to.equal(org);
      expect(full?.versionHistory).to.deep.equal([]);
      expect(lean?.documentName).to.equal(full?.documentName);
    });

    it('a malformed id is not found, never a crash for the caller', async () => {
      let found: unknown = null;
      let err: unknown;
      try {
        found = await repo.get(fx.newId(), 'not-an-id');
      } catch (e) {
        err = e;
      }
      // Either answer is today's behaviour (the store rejects the id); it must not hang or return a row.
      expect(found).to.equal(null);
      expect(err === undefined || err instanceof Error).to.equal(true);
    });
  });

  describe('idempotent create', () => {
    const claim = (key: string, fingerprint = 'fp') => ({ key, fingerprint });

    it('a second attempt gets the first attempt\'s row', async () => {
      const org = fx.newId();
      const user = fx.newId();
      const info = mk(org, { initiatorUserId: user });
      const first = await repo.createIdempotent(info, claim('k1'), {});
      const second = await repo.createIdempotent(info, claim('k1'), {});
      expect(first.replayed).to.equal(false);
      expect(second.replayed).to.equal(true);
      expect(second.document._id).to.equal(first.document._id);
      expect(second.document.idempotencyFingerprint).to.equal('fp');
    });

    it('settles attempts racing each other: one creates, the rest replay it', async () => {
      const org = fx.newId();
      const user = fx.newId();
      await repo.ensureIndexes();
      const results = await Promise.all(
        Array.from({ length: 12 }, () =>
          repo.createIdempotent(mk(org, { initiatorUserId: user, documentName: 'raced' }), claim('race'), {}),
        ),
      );
      expect(results.filter((r) => !r.replayed)).to.have.length(1);
      expect(new Set(results.map((r) => r.document._id)).size).to.equal(1);
    });

    it('keys are scoped to the org and the principal', async () => {
      const org = fx.newId();
      const a = await repo.createIdempotent(mk(org, { initiatorUserId: fx.newId() }), claim('k'), {});
      const b = await repo.createIdempotent(mk(org, { initiatorUserId: fx.newId() }), claim('k'), {});
      const c = await repo.createIdempotent(mk(fx.newId(), { initiatorUserId: fx.newId() }), claim('k'), {});
      const d = await repo.createIdempotent(mk(org, { initiatorUserId: null }), claim('k'), {});
      const e = await repo.createIdempotent(mk(org, { initiatorUserId: null }), claim('k'), {});
      expect([a, b, c, d].every((r) => !r.replayed)).to.equal(true);
      expect(e.replayed).to.equal(true);
      expect(e.document._id).to.equal(d.document._id);
    });

    it('first-attempt fields are written once, by the creating attempt', async () => {
      const org = fx.newId();
      const info = mk(org);
      const first = await repo.createIdempotent(info, claim('lease'), { uploadLeaseToken: 't1', uploadLeaseExpiresAt: 99 });
      const second = await repo.createIdempotent(info, claim('lease'), { uploadLeaseToken: 't2', uploadLeaseExpiresAt: 77 });
      expect(first.document.uploadLeaseToken).to.equal('t1');
      expect(second.document.uploadLeaseToken).to.equal('t1');
    });

    it('a plain create without a key never replays', async () => {
      const org = fx.newId();
      const a = await repo.create(mk(org, { documentName: 'same' }));
      const b = await repo.create(mk(org, { documentName: 'same' }));
      expect(a._id).to.not.equal(b._id);
    });
  });

  describe('update', () => {
    it('writes the patch, appends versions in order, and bumps nothing else', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org, { isVersionedFile: true }));
      const saved = await repo.update(
        org,
        row._id,
        { mutationCount: 2, sizeInBytes: 10, s3: { url: 'u' } },
        { pushVersions: [{ version: 0, note: 'a' }, { version: 1, note: 'b' }] },
      );
      expect(saved?.mutationCount).to.equal(2);
      expect(saved?.sizeInBytes).to.equal(10);
      expect(saved?.s3).to.deep.equal({ url: 'u' });
      expect(saved?.versionHistory?.map((v) => v.version)).to.deep.equal([0, 1]);
      const again = await repo.update(org, row._id, {}, { pushVersions: [{ version: 2 }] });
      expect(again?.versionHistory?.map((v) => v.version)).to.deep.equal([0, 1, 2]);
      expect((await repo.get(org, row._id))?.versionHistory).to.have.length(3);
    });

    it('a key set to undefined clears the field', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org));
      await repo.update(org, row._id, { isDeleted: true, deletedByUserId: fx.newId() });
      const cleared = await repo.update(org, row._id, { isDeleted: false, deletedByUserId: undefined });
      expect(cleared?.isDeleted).to.equal(false);
      expect(cleared).to.not.have.property('deletedByUserId');
    });

    it('answers null for a row that is gone or in another org', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org));
      expect(await repo.update(fx.newId(), row._id, { mutationCount: 5 })).to.equal(null);
      await repo.remove(row._id, org);
      expect(await repo.update(org, row._id, { mutationCount: 5 })).to.equal(null);
    });

    it('only the attempt holding the lease can finish the upload, and it clears the lease', async () => {
      const org = fx.newId();
      const created = await repo.createIdempotent(mk(org), { key: 'up', fingerprint: 'f' }, { uploadLeaseToken: 'mine', uploadLeaseExpiresAt: Date.now() + 60_000 });
      const id = created.document._id;
      expect(await repo.update(org, id, { local: { url: 'x' } }, { holdLease: 'someone-else' })).to.equal(null);
      expect((await repo.get(org, id))?.local).to.equal(undefined);
      const done = await repo.update(org, id, { local: { url: 'x' } }, { holdLease: 'mine' });
      expect(done?.local).to.deep.equal({ url: 'x' });
      expect(done).to.not.have.property('uploadLeaseToken');
    });
  });

  describe('delete', () => {
    it('remove deletes by id, scoped to the org when one is given', async () => {
      const org = fx.newId();
      const a = await repo.create(mk(org));
      const b = await repo.create(mk(org));
      await repo.remove(a._id, fx.newId());
      expect(await repo.get(org, a._id)).to.not.equal(null);
      await repo.remove(a._id, org);
      await repo.remove(b._id);
      expect(await repo.get(org, a._id)).to.equal(null);
      expect(await repo.get(org, b._id)).to.equal(null);
    });

    it('removeUnstored keeps a row once its file is stored', async () => {
      const org = fx.newId();
      const empty = await repo.create(mk(org));
      const stored = await repo.create(mk(org, { local: { url: 'u' } }));
      await repo.removeUnstored(empty._id, StorageVendor.Local);
      await repo.removeUnstored(stored._id, StorageVendor.Local);
      expect(await repo.get(org, empty._id)).to.equal(null);
      expect(await repo.get(org, stored._id)).to.not.equal(null);
    });

    it('an unfinished direct upload goes only while it is still awaiting its file', async () => {
      const org = fx.newId();
      const waiting = await repo.create(mk(org, { awaitingDirectUpload: true }));
      const arrived = await repo.create(mk(org, { awaitingDirectUpload: true }));
      await repo.clearAwaitingDirectUpload(org, arrived._id);
      expect(await repo.removeAwaitingDirectUpload(org, arrived._id)).to.equal(false);
      expect(await repo.removeAwaitingDirectUpload(fx.newId(), waiting._id)).to.equal(false);
      expect(await repo.removeAwaitingDirectUpload(org, waiting._id)).to.equal(true);
      expect(await repo.get(org, waiting._id)).to.equal(null);
      expect(await repo.get(org, arrived._id)).to.not.equal(null);
    });
  });

  describe('upload lease', () => {
    it('one of two retries takes over an unfinished upload; a finished one cannot be taken', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org));
      const [a, b] = await Promise.all([
        repo.claimUpload(row._id, StorageVendor.Local, 'a', 60_000),
        repo.claimUpload(row._id, StorageVendor.Local, 'b', 60_000),
      ]);
      expect([a, b].filter(Boolean)).to.have.length(1);
      const owner = a ? 'a' : 'b';
      expect(await repo.claimUpload(row._id, StorageVendor.Local, 'c', 60_000)).to.equal(null);
      await repo.releaseUpload(row._id, 'not-the-owner');
      expect(await repo.claimUpload(row._id, StorageVendor.Local, 'c', 60_000)).to.equal(null);
      await repo.releaseUpload(row._id, owner);
      expect((await repo.claimUpload(row._id, StorageVendor.Local, 'c', 60_000))?.uploadLeaseToken).to.equal('c');
      await repo.update(org, row._id, { local: { url: 'u' } });
      await repo.releaseUpload(row._id, 'c');
      expect(await repo.claimUpload(row._id, StorageVendor.Local, 'd', 60_000)).to.equal(null);
    });

    it('a lapsed lease can be taken', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org, { uploadLeaseToken: 'old', uploadLeaseExpiresAt: Date.now() - 1 }));
      expect((await repo.claimUpload(row._id, StorageVendor.Local, 'new', 1000))?.uploadLeaseToken).to.equal('new');
    });
  });

  describe('path prefix', () => {
    it('subtree returns the path and everything beneath it by whole segments, deleted rows included', async () => {
      const org = fx.newId();
      const base = `${org}/PipesHub/x`;
      const at = await repo.create(mk(org, { documentPath: base }));
      const below = await repo.create(mk(org, { documentPath: `${base}/y/z` }));
      const gone = await repo.create(mk(org, { documentPath: `${base}/y` }));
      await repo.update(org, gone._id, { isDeleted: true });
      await repo.create(mk(org, { documentPath: `${base}2` }));
      await repo.create(mk(org, { documentPath: `${base}2/q` }));
      await repo.create(mk(org, { documentPath: `${base}-` }));
      await repo.create(mk(fx.newId(), { documentPath: base }));
      const rows = await repo.subtree(org, base);
      expect(rows.map((r) => r._id).sort()).to.deep.equal([at._id, below._id, gone._id].sort());
      const deleted = rows.find((r) => r._id === gone._id);
      expect(deleted?.isDeleted).to.equal(true);
      expect(Object.keys(rows[0]).sort()).to.deep.equal(
        ['_id', 'documentName', 'documentPath', 'extension', 'isVersionedFile', 'versionHistory', 'isDeleted'].filter((k) => k in rows[0]).sort(),
      );
    });

    it('rewriteTree swaps the prefix on every listed row and applies each row\'s own fields', async () => {
      const org = fx.newId();
      const old = `${org}/PipesHub/old`;
      const a = await repo.create(mk(org, { documentPath: old, local: { url: 'u', localPath: 'file://old/a' } }));
      const b = await repo.create(mk(org, { documentPath: `${old}/sub/deep` }));
      const other = await repo.create(mk(org, { documentPath: `${old}/untouched` }));
      await repo.rewriteTree(old, `${org}/PipesHub/new`, [
        { id: a._id, set: { 'local.localPath': 'file://new/a' } },
        { id: b._id, set: {} },
      ]);
      const after = async (id: string) => repo.get(org, id);
      expect((await after(a._id))?.documentPath).to.equal(`${org}/PipesHub/new`);
      expect((await after(a._id))?.local).to.deep.equal({ url: 'u', localPath: 'file://new/a' });
      expect((await after(b._id))?.documentPath).to.equal(`${org}/PipesHub/new/sub/deep`);
      expect((await after(other._id))?.documentPath).to.equal(`${old}/untouched`);
    });

    it('setFieldsMany changes the named fields of each listed row', async () => {
      const org = fx.newId();
      const one = await repo.create(mk(org, { documentPath: `${org}/PipesHub/a` }));
      const two = await repo.create(mk(org, { documentPath: `${org}/PipesHub/b` }));
      const untouched = await repo.create(mk(org, { documentPath: `${org}/PipesHub/c` }));
      await repo.setFieldsMany([
        { id: one._id, set: { documentPath: `${org}/PipesHub/x` } },
        { id: two._id, set: { documentPath: `${org}/PipesHub/y`, 'local.localPath': 'p' } },
      ]);
      await repo.setFieldsMany([]);
      expect((await repo.get(org, one._id))?.documentPath).to.equal(`${org}/PipesHub/x`);
      expect((await repo.get(org, two._id))?.documentPath).to.equal(`${org}/PipesHub/y`);
      expect((await repo.get(org, untouched._id))?.documentPath).to.equal(`${org}/PipesHub/c`);
    });

    it('setFields changes named fields of one row', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org, { local: { url: 'u', localPath: 'p' } }));
      await repo.setFields(row._id, { 'local.localPath': 'q', customMetadata: [{ key: 'k', value: 1 }] });
      const got = await repo.get(org, row._id);
      expect(got?.local).to.deep.equal({ url: 'u', localPath: 'q' });
      expect(got?.customMetadata?.[0]).to.include({ key: 'k', value: 1 });
    });
  });

  describe('virtual records and folders', () => {
    it('listByVirtualRecord finds the envelope documents wherever filed, plus the flat path, in the org only', async () => {
      const org = fx.newId();
      const flat = `${org}/PipesHub/records/v1`;
      const rec = await repo.create(mk(org, { documentName: 'record_v1', documentPath: `${org}/PipesHub/a/b` }));
      const meta = await repo.create(mk(org, { documentName: 'metadata_v1', documentPath: `${org}/PipesHub/c` }));
      const flatDoc = await repo.create(mk(org, { documentName: 'whatever', documentPath: flat }));
      await repo.create(mk(org, { documentName: 'record_v10', documentPath: `${org}/PipesHub/a/b` }));
      await repo.create(mk(fx.newId(), { documentName: 'record_v1' }));
      const ids = (await repo.listByVirtualRecord(org, 'v1', flat)).map((r) => r._id).sort();
      expect(ids).to.deep.equal([rec._id, meta._id, flatDoc._id].sort());
      const relocate = await repo.listForRelocate(org, 'v1', flat);
      expect(relocate.map((r) => r._id).sort()).to.deep.equal(ids);
      expect(relocate[0]).to.not.have.property('sizeInBytes');
    });

    it('a content revision is listed by listByVirtualRecord but left out of listForRelocate', async () => {
      const org = fx.newId();
      const flat = `${org}/PipesHub/records/v1`;
      const rec = await repo.create(mk(org, { documentName: 'record_v1', documentPath: `${org}/PipesHub/a` }));
      const rev = await repo.createArtifactSetOnce(
        mk(org, {
          documentName: 'revision_r1',
          artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: `${org}/PipesHub/record-revisions/v1/r1` },
        }),
        fence(Date.now()),
      );
      const ids = (await repo.listByVirtualRecord(org, 'v1', flat)).map((r) => r._id).sort();
      expect(ids).to.deep.equal([rec._id, rev._id].sort());
      expect((await repo.listForRelocate(org, 'v1', flat)).map((r) => r._id)).to.deep.equal([rec._id]);
    });

    it('createArtifactSetOnce settles on one row per revision, and listArtifactSets lists the live ones oldest first', async () => {
      const org = fx.newId();
      const set = (rev: string): NewDocument =>
        mk(org, {
          documentName: `revision_${rev}`,
          artifactSet: { virtualRecordId: 'v1', rev, root: `${org}/PipesHub/record-revisions/v1/${rev}` },
        });
      const [a, b] = await Promise.all([repo.createArtifactSetOnce(set('r1'), fence(Date.now())), repo.createArtifactSetOnce(set('r1'), fence(Date.now()))]);
      expect(b._id).to.equal(a._id);
      expect(a.artifactSet).to.deep.equal({ virtualRecordId: 'v1', rev: 'r1', root: `${org}/PipesHub/record-revisions/v1/r1` });
      const second = await repo.createArtifactSetOnce(set('r2'), fence(Date.now()));
      const other = await repo.createArtifactSetOnce(
        mk(fx.newId(), { artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: 'x' } }),
        fence(Date.now()),
      );
      expect(other._id).to.not.equal(a._id);
      await repo.update(org, second._id, { isDeleted: true });
      expect((await repo.listArtifactSets(org, 'v1')).map((r) => r._id)).to.deep.equal([a._id]);
    });

    describe('the delete fence of a content revision', () => {
      const T0 = 1_000_000_000_000;
      const revision = async (org: string, rev = 'r1', at = T0): Promise<string> => {
        const input = mk(org, {
          documentName: `revision_${rev}`,
          artifactSet: { virtualRecordId: 'v1', rev, root: `${org}/PipesHub/record-revisions/v1/${rev}` },
        });
        await repo.createArtifactSetOnce(input, fence(at));
        // The first call stamps the row with the real clock; a second one moves the stamp to `at`.
        return (await repo.createArtifactSetOnce(input, fence(at)))._id;
      };
      const stamp = async (org: string, id: string): Promise<number | undefined> => (await repo.get(org, id))?.updatedAt;

      it('a write moves the write stamp, so a delete that listed the revision earlier no longer matches', async () => {
        const org = fx.newId();
        const id = await revision(org);
        const listed = await stamp(org, id);
        expect(listed).to.equal(T0);
        await repo.createArtifactSetOnce(
          mk(org, { documentName: 'revision_r1', artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: 'x' } }),
          fence(T0 + 5000),
        );
        expect(await stamp(org, id)).to.equal(T0 + 5000);
        const claim = await repo.claimArtifactSetForDelete(org, id, { now: T0 + 6000, staleMs: STALE, ifUnchangedSince: listed });
        expect(claim).to.equal('changed');
        expect((await repo.get(org, id))?.artifactSet?.retiringAt).to.equal(undefined);
      });

      it('a delete claims the revision without moving the write stamp, and a write is then refused', async () => {
        const org = fx.newId();
        const id = await revision(org);
        const claimed = await repo.claimArtifactSetForDelete(org, id, { now: T0 + 1000, staleMs: STALE, ifUnchangedSince: T0 });
        expect(claimed).to.not.equal(null);
        expect(claimed).to.not.equal('changed');
        expect((claimed as { artifactSet?: { retiringAt?: number } }).artifactSet?.retiringAt).to.equal(T0 + 1000);
        expect(await stamp(org, id)).to.equal(T0);
        const again = mk(org, { documentName: 'revision_r1', artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: 'x' } });
        let refused: unknown;
        try {
          await repo.createArtifactSetOnce(again, fence(T0 + 2000));
        } catch (error) {
          refused = error;
        }
        expect(refused).to.be.instanceOf(ConflictError);
        expect(await stamp(org, id)).to.equal(T0);
      });

      it('a second delete is refused while the first claim is fresh and may take over a stale one', async () => {
        const org = fx.newId();
        const id = await revision(org);
        await repo.claimArtifactSetForDelete(org, id, { now: T0 + 1000, staleMs: STALE, ifUnchangedSince: T0 });
        expect(await repo.claimArtifactSetForDelete(org, id, { now: T0 + 2000, staleMs: STALE, ifUnchangedSince: T0 })).to.equal('changed');
        const later = T0 + 1000 + STALE + 1;
        const taken = await repo.claimArtifactSetForDelete(org, id, { now: later, staleMs: STALE, ifUnchangedSince: T0 });
        expect((taken as { artifactSet?: { retiringAt?: number } }).artifactSet?.retiringAt).to.equal(later);
      });

      it('a write clears a claim that is older than the stale bound', async () => {
        const org = fx.newId();
        const id = await revision(org);
        await repo.claimArtifactSetForDelete(org, id, { now: T0 + 1000, staleMs: STALE, ifUnchangedSince: T0 });
        const later = T0 + 1000 + STALE + 1;
        const written = await repo.createArtifactSetOnce(
          mk(org, { documentName: 'revision_r1', artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: 'x' } }),
          fence(later),
        );
        expect(written._id).to.equal(id);
        expect(written.artifactSet?.retiringAt).to.equal(undefined);
        expect(await stamp(org, id)).to.equal(later);
      });

      it('an unfenced delete claims whatever is there, and a revision that is not there answers null', async () => {
        const org = fx.newId();
        const id = await revision(org);
        expect(await repo.claimArtifactSetForDelete(org, id, { now: T0 + 1000, staleMs: STALE })).to.not.equal(null);
        expect(await repo.claimArtifactSetForDelete(org, fx.newId(), { now: T0, staleMs: STALE, ifUnchangedSince: T0 })).to.equal(null);
        const plain = await repo.create(mk(org, { documentName: 'not-a-revision' }));
        expect(await repo.claimArtifactSetForDelete(org, plain._id, { now: T0, staleMs: STALE, ifUnchangedSince: T0 })).to.equal(null);
        expect(await repo.claimArtifactSetForDelete(fx.newId(), id, { now: T0, staleMs: STALE })).to.equal(null);
      });

      it('the final delete is fenced on the delete\'s own claim values: a writer that took the claim back keeps the row', async () => {
        const org = fx.newId();
        const id = await revision(org);
        await repo.claimArtifactSetForDelete(org, id, { now: T0 + 1000, staleMs: STALE, ifUnchangedSince: T0 });
        // a renewal moves the claim; the delete knows both values
        await repo.renewArtifactSetClaim(org, id, [T0 + 1000], T0 + 2000);
        expect((await repo.get(org, id))?.artifactSet?.retiringAt).to.equal(T0 + 2000);
        // a renewal that does not know the current value changes nothing
        await repo.renewArtifactSetClaim(org, id, [T0 + 500], T0 + 3000);
        expect((await repo.get(org, id))?.artifactSet?.retiringAt).to.equal(T0 + 2000);
        // a writer takes the claim back (stale), so the delete's values no longer match
        await repo.createArtifactSetOnce(
          mk(org, { documentName: 'revision_r1', artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: 'x' } }),
          fence(T0 + 2000 + STALE + 1),
        );
        await repo.removeClaimedArtifactSet(id, org, [T0 + 1000, T0 + 2000]);
        expect(await repo.get(org, id)).to.not.equal(null);
        // a claim that still stands is removed
        await repo.claimArtifactSetForDelete(org, id, { now: T0 + 9_000_000, staleMs: STALE });
        await repo.removeClaimedArtifactSet(id, org, [T0 + 1000, T0 + 9_000_000]);
        expect(await repo.get(org, id)).to.equal(null);
      });

      it('listArtifactSets carries the claim, so a listing shows a delete in progress', async () => {
        const org = fx.newId();
        const id = await revision(org);
        await repo.claimArtifactSetForDelete(org, id, { now: T0 + 1000, staleMs: STALE });
        const [listed] = await repo.listArtifactSets(org, 'v1');
        expect(listed._id).to.equal(id);
        expect(listed.artifactSet?.retiringAt).to.equal(T0 + 1000);
        expect(listed.updatedAt).to.equal(T0);
      });
    });

    it('rows with identical timestamps keep one order: envelopes newest first with ids descending, revisions oldest first with ids ascending', async () => {
      const org = fx.newId();
      const t = 5000;
      const envelopes = [] as string[];
      for (let i = 0; i < 4; i++) {
        envelopes.push((await repo.create(mk(org, { documentName: 'record_tie', documentPath: `${org}/PipesHub/p${i}`, createdAt: t })))._id);
      }
      expect((await repo.listEnvelopes(org, 'tie')).map((r) => r._id)).to.deep.equal([...envelopes].sort().reverse());
      const revisions = [] as string[];
      for (let i = 0; i < 4; i++) {
        const r = await repo.createArtifactSetOnce(
          mk(org, {
            documentName: `revision_t${i}`,
            createdAt: t,
            artifactSet: { virtualRecordId: 'tie', rev: `t${i}`, root: `${org}/PipesHub/record-revisions/tie/t${i}` },
          }),
          fence(Date.now()),
        );
        revisions.push(r._id);
      }
      expect((await repo.listArtifactSets(org, 'tie')).map((r) => r._id)).to.deep.equal([...revisions].sort());
    });

    it('listEnvelopes answers the live record_ and metadata_ rows, newest first', async () => {
      const org = fx.newId();
      const old = await repo.create(mk(org, { documentName: 'record_v1', documentPath: `${org}/PipesHub/a`, createdAt: 1000 }));
      const fresh = await repo.create(mk(org, { documentName: 'metadata_v1', documentPath: `${org}/PipesHub/b`, createdAt: 2000 }));
      const dead = await repo.create(mk(org, { documentName: 'record_v1' }));
      await repo.update(org, dead._id, { isDeleted: true });
      await repo.create(mk(org, { documentName: 'record_v10' }));
      await repo.create(mk(fx.newId(), { documentName: 'record_v1' }));
      const rows = await repo.listEnvelopes(org, 'v1');
      expect(rows.map((r) => r._id)).to.deep.equal([fresh._id, old._id]);
      expect(rows[0]).to.include({ documentName: 'metadata_v1', documentPath: `${org}/PipesHub/b` });
    });

    it('listEnvelopes carries retiredAt and can hold back rows younger than olderThanSeconds', async () => {
      const org = fx.newId();
      const old = await repo.create(mk(org, { documentName: 'record_age', createdAt: 1000 }));
      const young = await repo.create(mk(org, { documentName: 'metadata_age', createdAt: Date.now() }));
      await repo.retire(org, old._id, 7);
      const all = await repo.listEnvelopes(org, 'age');
      expect(all.map((r) => [r._id, r.retiredAt ?? null])).to.deep.equal([[young._id, null], [old._id, 7]]);
      const aged = await repo.listEnvelopes(org, 'age', { olderThanSeconds: 3600 });
      expect(aged.map((r) => r._id)).to.deep.equal([old._id]);
      expect((await repo.listEnvelopes(org, 'age', { olderThanSeconds: Number.NaN })).length).to.equal(2);
    });

    it('retire marks a live row once, keeps the first time, and refuses other orgs and deleted rows', async () => {
      const org = fx.newId();
      const doc = await repo.create(mk(org, { documentName: 'record_r' }));
      expect(await repo.retire(org, doc._id, 100)).to.deep.equal({ retiredAt: 100 });
      expect(await repo.retire(org, doc._id, 200)).to.deep.equal({ retiredAt: 100 });
      expect(await repo.retire(fx.newId(), doc._id, 300)).to.equal(null);
      const dead = await repo.create(mk(org, { documentName: 'record_d' }));
      await repo.update(org, dead._id, { isDeleted: true });
      expect(await repo.retire(org, dead._id, 400)).to.equal(null);
    });

    it('pageFlatEnvelopes pages by id the live envelopes still filed at the flat path', async () => {
      const org = fx.newId();
      const prefix = `${org}/PipesHub/records/`;
      const a = await repo.create(mk(org, { documentName: 'record_v1', documentPath: `${prefix}v1` }));
      const b = await repo.create(mk(org, { documentName: 'metadata_v2', documentPath: `${prefix}v2` }));
      await repo.create(mk(org, { documentName: 'record_v3', documentPath: `${org}/PipesHub/a/b` }));
      await repo.create(mk(org, { documentName: 'other_v4', documentPath: `${prefix}v4` }));
      await repo.create(mk(fx.newId(), { documentName: 'record_v1', documentPath: `${prefix}v1` }));
      const first = await repo.pageFlatEnvelopes(org, prefix, undefined, 1);
      expect(first.map((r) => r._id)).to.deep.equal([a._id]);
      const next = await repo.pageFlatEnvelopes(org, prefix, first[0]._id, 10);
      expect(next.map((r) => r._id)).to.deep.equal([b._id]);
    });

    it('listByFolders matches the exact folder only', async () => {
      const org = fx.newId();
      const f = `${org}/PipesHub/artifacts/c1`;
      const inFolder = await repo.create(mk(org, { documentPath: f }));
      await repo.create(mk(org, { documentPath: `${f}/deeper` }));
      await repo.create(mk(org, { documentPath: `${org}/PipesHub/artifacts/c2` }));
      const rows = await repo.listByFolders(org, [f, `${org}/PipesHub/conversations/c1`]);
      expect(rows.map((r) => r._id)).to.deep.equal([inFolder._id]);
    });

    it('findExistingIds returns live ids of the org only', async () => {
      const org = fx.newId();
      const live = await repo.create(mk(org));
      const dead = await repo.create(mk(org));
      await repo.update(org, dead._id, { isDeleted: true });
      const foreign = await repo.create(mk(fx.newId()));
      const found = await repo.findExistingIds(org, [live._id, dead._id, foreign._id, fx.newId()]);
      expect(found).to.deep.equal([live._id]);
    });
  });

  describe('document store contract (state hidden)', () => {
    // The write state of a row (a counter and the writers in flight) must stay out of every row a method returns, however the
    // store reads it: the callers serialise rows into HTTP responses and logs.
    const LEAK = /"(writeSeq|writers|write_seq)"/;

    it('no method that returns rows returns the write state', async () => {
      const org = fx.newId();
      const flat = `${org}/PipesHub/records/v1`;
      const scope = { connectorId: 'conn', prefixes: [`${org}/PipesHub/records/conn`] };
      const hidden = async (id: string): Promise<void> =>
        fx.setHiddenState(org, id, { writeSeq: 7, writers: [{ t: 'tok', until: Date.now() + 60_000 }] });
      const envelope = await repo.create(
        mk(org, { documentName: 'record_v1', documentPath: flat, customMetadata: [{ key: 'connectorId', value: 'conn' }] }),
      );
      const inScope = await repo.create(mk(org, { documentPath: `${scope.prefixes[0]}/a` }));
      const tagged = await repo.create(
        mk(org, { documentPath: `${org}/PipesHub/flat/x`, customMetadata: [{ key: 'connectorId', value: 'conn' }] }),
      );
      const folder = await repo.create(mk(org, { documentPath: `${org}/PipesHub/folder` }));
      const rev = await repo.createArtifactSetOnce(
        mk(org, { documentName: 'revision_r1', artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: `${org}/PipesHub/record-revisions/v1/r1` } }),
        fence(Date.now()),
      );
      const keyed = await repo.createIdempotent(mk(org, { initiatorUserId: fx.newId() }), { key: 'k', fingerprint: 'fp' }, {});
      const unfinished = await repo.create(mk(org));
      const toRetire = await repo.create(mk(org, { documentName: 'record_v1', documentPath: flat }));
      const deleted = await repo.create(mk(org, { documentPath: `${org}/PipesHub/gone` }));
      const rows = [envelope, inScope, tagged, folder, rev, keyed.document, unfinished, toRetire, deleted];
      for (const r of rows) await hidden(r._id);
      // Rows built by the repository are plain objects or models; either way their JSON is what callers send out.
      const seen: [string, unknown][] = [];
      const call = async (name: string, fn: () => Promise<unknown>): Promise<void> => {
        seen.push([name, await fn()]);
      };
      await call('get', () => repo.get(org, envelope._id));
      await call('getLive', () => repo.getLive(org, envelope._id));
      await call('getLive lean', () => repo.getLive(org, envelope._id, true));
      await call('listByVirtualRecord', () => repo.listByVirtualRecord(org, 'v1', flat));
      await call('listForRelocate', () => repo.listForRelocate(org, 'v1', flat));
      await call('listEnvelopes', () => repo.listEnvelopes(org, 'v1'));
      await call('pageFlatEnvelopes', () => repo.pageFlatEnvelopes(org, `${org}/PipesHub/records/`, undefined, 10));
      await call('listByFolders', () => repo.listByFolders(org, [`${org}/PipesHub/folder`]));
      await call('subtree', () => repo.subtree(org, `${org}/PipesHub`));
      await call('listArtifactSets', () => repo.listArtifactSets(org, 'v1'));
      await call('pageByConnector', () => repo.pageByConnector(org, scope, undefined, 10));
      await call('streamTaggedOutsidePrefixes', async () => {
        const out: unknown[] = [];
        for await (const d of repo.streamTaggedOutsidePrefixes(org, scope)) out.push(d);
        return out;
      });
      await call('createIdempotent (replay)', () => repo.createIdempotent(mk(org, { initiatorUserId: keyed.document.initiatorUserId ?? null }), { key: 'k', fingerprint: 'fp' }, {}));
      await call('claimUpload', () => repo.claimUpload(unfinished._id, StorageVendor.Local, 'lease', 60_000));
      await call('update', () => repo.update(org, inScope._id, { sizeInBytes: 5 }));
      await call('update (delete)', () => repo.update(org, deleted._id, { isDeleted: true, deletedByUserId: fx.newId() }));
      await call('retire', () => repo.retire(org, toRetire._id, 100));
      await call('createArtifactSetOnce (again)', () =>
        repo.createArtifactSetOnce(
          mk(org, { documentName: 'revision_r1', artifactSet: { virtualRecordId: 'v1', rev: 'r1', root: `${org}/PipesHub/record-revisions/v1/r1` } }),
          fence(Date.now()),
        ),
      );
      await call('claimArtifactSetForDelete', () => repo.claimArtifactSetForDelete(org, rev._id, { now: Date.now(), staleMs: STALE }));
      // A create reads the row back: its own return value is checked after the state was set on a sibling row.
      await call('create', () => repo.create(mk(org)));
      for (const [name, value] of seen) {
        const flatRows = Array.isArray(value) ? value : [value];
        // A call that found nothing proves nothing: every call above has a row to return.
        expect(value, `${name} found no row`).to.not.equal(null);
        if (Array.isArray(value)) expect(value.length, `${name} returned no rows`).to.be.greaterThan(0);
        for (const row of flatRows) expect(JSON.stringify(row), `${name} leaked the write state`).to.not.match(LEAK);
      }
    });

    it('a row read after its state was set has the same fields as before', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org, { documentPath: `${org}/PipesHub/same` }));
      const before = JSON.parse(JSON.stringify(await repo.get(org, row._id)));
      await fx.setHiddenState(org, row._id, { writeSeq: 3, writers: [{ t: 'a', until: 1 }] });
      expect(JSON.parse(JSON.stringify(await repo.get(org, row._id)))).to.deep.equal(before);
    });

    it('a write by the repository leaves the stored state alone', async () => {
      const org = fx.newId();
      const row = await repo.create(mk(org));
      await fx.setHiddenState(org, row._id, { writeSeq: 9, writers: [{ t: 'a', until: 1 }] });
      await repo.update(org, row._id, { sizeInBytes: 1 });
      await repo.setFields(row._id, { documentPath: `${org}/PipesHub/moved` });
      expect((await repo.get(org, row._id))?.sizeInBytes).to.equal(1);
      expect(await fx.getHiddenState(org, row._id)).to.deep.equal({ writeSeq: 9, writers: [{ t: 'a', until: 1 }] });
      expect(JSON.stringify(await repo.get(org, row._id))).to.not.match(LEAK);
    });
  });

  describe('document store contract (fingerprint)', () => {
    const TOK = (n: string): string => `${n}-${fx.newId()}`;
    const stateOf = (org: string, id: string) => repo.peekWriteState(org, id).then((x) => {
      if (!x) throw new Error(`no row ${id}`);
      return x;
    });
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const tokens = (st: { writers: { t: string }[] }): string[] => st.writers.map((w) => w.t).sort();
    const row = async (org: string) => repo.create(mk(org, { documentPath: `${org}/PipesHub/fp` }));
    /** Waits (on the store's clock) until `until` has passed. */
    const pastExpiry = async (until: number): Promise<void> => {
      for (let i = 0; i < 100 && (await repo.serverClockMs()) <= until; i++) await sleep(25);
    };

    it('C1: 50 parallel begins get 50 distinct sequence numbers and 50 tokens; the 65th live writer is refused; 50 ends empty the list', async () => {
      const org = fx.newId();
      const r = await row(org);
      const toks = Array.from({ length: 50 }, () => TOK('p'));
      const begun = await Promise.all(toks.map((t) => repo.beginWrite(org, r._id, t, 60_000)));
      const seqs = begun.map((b) => b?.writeSeq);
      expect(new Set(seqs).size).to.equal(50);
      expect(Math.max(...(seqs as number[]))).to.equal(50);
      expect(tokens(await stateOf(org, r._id))).to.deep.equal([...toks].sort());
      await Promise.all(toks.map((t) => repo.endWrite(org, r._id, t)));
      const after = await stateOf(org, r._id);
      expect(after.writers).to.deep.equal([]);
      expect(after.writeSeq).to.equal(100);

      const full = await row(org);
      const sixtyFour = Array.from({ length: 64 }, () => TOK('c'));
      await Promise.all(sixtyFour.map((t) => repo.beginWrite(org, full._id, t, 60_000)));
      let refused: unknown;
      try {
        await repo.beginWrite(org, full._id, TOK('65th'), 60_000);
      } catch (e) {
        refused = e;
      }
      expect(refused, 'the 65th concurrent writer').to.be.instanceOf(DocumentBusy);
      const st = await stateOf(org, full._id);
      expect(st.writers.length).to.equal(64);
      expect(st.writeSeq, 'a refused begin wrote nothing').to.equal(64);
    });

    it('C2: A begins, B begins, B ends, A ends (and the other order): settled only after the last end', async () => {
      for (const order of [['B', 'A'], ['A', 'B']]) {
        const org = fx.newId();
        const r = await row(org);
        const t = { A: TOK('a'), B: TOK('b') } as Record<string, string>;
        const [first, second] = order as [string, string];
        await repo.beginWrite(org, r._id, t.A!, 60_000);
        await repo.beginWrite(org, r._id, t.B!, 60_000);
        await repo.endWrite(org, r._id, t[first]!);
        const mid = await stateOf(org, r._id);
        expect(isSettled(mid.writers, await repo.serverClockMs()), `after ${first} ended`).to.equal(false);
        await repo.endWrite(org, r._id, t[second]!);
        const end = await stateOf(org, r._id);
        expect(isSettled(end.writers, await repo.serverClockMs())).to.equal(true);
        expect(end.writeSeq).to.equal(4);
      }
    });

    it('C3: ending A twice leaves B\'s token in place', async () => {
      const org = fx.newId();
      const r = await row(org);
      const a = TOK('a');
      const b = TOK('b');
      await repo.beginWrite(org, r._id, a, 60_000);
      await repo.beginWrite(org, r._id, b, 60_000);
      await repo.endWrite(org, r._id, a);
      await repo.endWrite(org, r._id, a);
      const st = await stateOf(org, r._id);
      expect(tokens(st)).to.deep.equal([b]);
      expect(isSettled(st.writers, await repo.serverClockMs())).to.equal(false);
    });

    it('C4: bumpSeq during a write leaves the tokens unchanged', async () => {
      const org = fx.newId();
      const r = await row(org);
      const a = TOK('a');
      const begun = await repo.beginWrite(org, r._id, a, 60_000);
      const before = await stateOf(org, r._id);
      const bumped = await repo.bumpSeq(org, r._id);
      expect(bumped?.writeSeq).to.equal((begun?.writeSeq ?? 0) + 1);
      const after = await stateOf(org, r._id);
      expect(after.writers).to.deep.equal(before.writers);
      expect(await repo.bumpSeq(org, fx.newId())).to.equal(null);
    });

    it('C5: a writer that never ended stops counting once its window passed on the store clock; the next begin prunes it', async () => {
      const org = fx.newId();
      const r = await row(org);
      const crashed = TOK('x');
      await repo.beginWrite(org, r._id, crashed, 300);
      const live = await stateOf(org, r._id);
      expect(isSettled(live.writers, await repo.serverClockMs())).to.equal(false);
      await pastExpiry(live.writers[0]!.until);
      expect(isSettled((await stateOf(org, r._id)).writers, await repo.serverClockMs())).to.equal(true);
      const next = TOK('n');
      await repo.beginWrite(org, r._id, next, 60_000);
      expect(tokens(await stateOf(org, r._id))).to.deep.equal([next]);
    });

    it('C6: ending a pruned token still bumps the sequence and never errors; a missing row or another org is null', async () => {
      const org = fx.newId();
      const r = await row(org);
      const gone = TOK('gone');
      await repo.beginWrite(org, r._id, gone, 200);
      await pastExpiry((await stateOf(org, r._id)).writers[0]!.until);
      await repo.beginWrite(org, r._id, TOK('later'), 60_000); // prunes `gone`
      const before = (await stateOf(org, r._id)).writeSeq;
      await repo.endWrite(org, r._id, gone);
      expect((await stateOf(org, r._id)).writeSeq).to.equal(before + 1);
      expect(await repo.beginWrite(org, fx.newId(), TOK('m'), 1000)).to.equal(null);
      expect(await repo.beginWrite(fx.newId(), r._id, TOK('o'), 1000)).to.equal(null);
      await repo.endWrite(org, fx.newId(), gone);
      expect(await repo.peekWriteState(fx.newId(), r._id)).to.equal(null);
    });

    it('C7: adoptDirectUpload matches once under 20 parallel calls; correctSize is compare-and-set; markAwaitingDirectUpload moves path, flag, url and sequence together', async () => {
      const org = fx.newId();
      const r = await row(org);
      const marked = await repo.markAwaitingDirectUpload(org, r._id, {
        documentPath: `${org}/PipesHub/awaiting`,
        awaitingDirectUpload: true,
        s3: { url: 'https://b.s3.us-east-1.amazonaws.com/k' },
      });
      expect(marked?.row.awaitingDirectUpload).to.equal(true);
      expect(marked?.row.documentPath).to.equal(`${org}/PipesHub/awaiting`);
      expect(marked?.row.s3?.url).to.equal('https://b.s3.us-east-1.amazonaws.com/k');
      expect(marked?.writeSeq).to.equal(1);
      expect(JSON.stringify(marked?.row)).to.not.match(/"(writeSeq|writers|write_seq)"/);
      expect(await repo.markAwaitingDirectUpload(org, fx.newId(), { awaitingDirectUpload: true })).to.equal(null);
      const results = await Promise.all(Array.from({ length: 20 }, () => repo.adoptDirectUpload(org, r._id, 5)));
      expect(results.filter(Boolean).length).to.equal(1);
      const adopted = await repo.get(org, r._id);
      expect(adopted?.awaitingDirectUpload ?? false).to.equal(false);
      expect(adopted?.sizeInBytes).to.equal(5);
      expect((await stateOf(org, r._id)).writeSeq, 'one adoption, one bump').to.equal(2);
      await repo.correctSize(org, r._id, 4, 9);
      expect((await repo.get(org, r._id))?.sizeInBytes, 'a stale expectation changes nothing').to.equal(5);
      await repo.correctSize(org, r._id, 5, 7);
      expect((await repo.get(org, r._id))?.sizeInBytes).to.equal(7);
      expect((await stateOf(org, r._id)).writeSeq, 'correctSize does not bump').to.equal(2);
    });

    it('C8: a slow writer\'s renewed token survives a fast writer; a renew of a pruned token does not bring it back', async () => {
      const org = fx.newId();
      const r = await row(org);
      const slow = TOK('slow');
      const fast = TOK('fast');
      await repo.beginWrite(org, r._id, slow, 400);
      await repo.renewWrite(org, r._id, slow, 60_000);
      await repo.beginWrite(org, r._id, fast, 60_000);
      await repo.endWrite(org, r._id, fast);
      expect(tokens(await stateOf(org, r._id))).to.deep.equal([slow]);

      const pruned = TOK('pruned');
      await repo.beginWrite(org, r._id, pruned, 150);
      await pastExpiry((await stateOf(org, r._id)).writers.find((w) => w.t === pruned)!.until);
      await repo.beginWrite(org, r._id, TOK('after'), 60_000); // prunes `pruned`
      await repo.renewWrite(org, r._id, pruned, 60_000);
      expect((await stateOf(org, r._id)).writers.map((w) => w.t)).to.not.include(pruned);
      expect((await stateOf(org, r._id)).writers.map((w) => w.t)).to.include(slow);
    });

    it('C9: the store clock decides, not the pod clock (the pod clock one hour ahead)', async () => {
      const org = fx.newId();
      const r = await row(org);
      const t0 = Date.now();
      const realNow = Date.now;
      Date.now = () => realNow() + 3_600_000;
      try {
        await repo.beginWrite(org, r._id, TOK('skew'), 60_000);
        const server = await repo.serverClockMs();
        expect(Math.abs(server - t0), 'serverClockMs').to.be.lessThan(5000);
      } finally {
        Date.now = realNow;
      }
      const until = (await stateOf(org, r._id)).writers[0]!.until;
      expect(until - t0, 'the token lapses a window after the STORE time').to.be.within(55_000, 65_000);
    });

    it('C10: renewing on an empty list is a no-op and does not rewrite the row', async () => {
      const org = fx.newId();
      const r = await row(org);
      await repo.bumpSeq(org, r._id);
      const stamp = await fx.rowStamp?.(org, r._id);
      const before = await stateOf(org, r._id);
      await repo.renewWrite(org, r._id, TOK('nobody'), 60_000);
      expect(await stateOf(org, r._id)).to.deep.equal(before);
      if (fx.rowStamp) expect(await fx.rowStamp(org, r._id), 'row rewritten').to.equal(stamp);
    });

    it('C11: renewing after the token ended does not bring it back or touch the others', async () => {
      const org = fx.newId();
      const r = await row(org);
      const a = TOK('a');
      const b = TOK('b');
      await repo.beginWrite(org, r._id, a, 60_000);
      await repo.beginWrite(org, r._id, b, 60_000);
      await repo.endWrite(org, r._id, a);
      const before = await stateOf(org, r._id);
      await repo.renewWrite(org, r._id, a, 120_000);
      expect(await stateOf(org, r._id)).to.deep.equal(before);
      await repo.endWrite(org, r._id, b);
      await repo.renewWrite(org, r._id, b, 120_000);
      expect((await stateOf(org, r._id)).writers).to.deep.equal([]);
    });

    it('C12: a delayed older renew cannot shorten a token', async () => {
      const org = fx.newId();
      const r = await row(org);
      const a = TOK('a');
      await repo.beginWrite(org, r._id, a, 1000);
      await repo.renewWrite(org, r._id, a, 120_000);
      const longer = (await stateOf(org, r._id)).writers[0]!.until;
      await repo.renewWrite(org, r._id, a, 100);
      expect((await stateOf(org, r._id)).writers[0]!.until).to.equal(longer);
    });

    it('C13: a token begun while the caller\'s unit is open is visible at once and survives that unit\'s rollback', async function () {
      if (!fx.inCallerUnit) return;
      const org = fx.newId();
      const r = await row(org);
      const t = TOK('unit');
      await fx.inCallerUnit(org, async () => {
        const begun = await repo.beginWrite(org, r._id, t, 60_000);
        expect(typeof begun?.writeSeq).to.equal('number');
        // another connection (outside the caller's unit) sees it before that unit commits
        expect(tokens(await stateOf(org, r._id))).to.deep.equal([t]);
        await repo.renewWrite(org, r._id, t, 90_000);
        await repo.bumpSeq(org, r._id);
      });
      const st = await stateOf(org, r._id);
      expect(tokens(st), 'token lost with the rolled back unit').to.deep.equal([t]);
      expect(st.writeSeq).to.equal(2);
    });

    it('getWithFingerprintState returns the row without the state, the counters, and the clock taken first', async () => {
      const org = fx.newId();
      const r = await row(org);
      const a = TOK('a');
      await repo.beginWrite(org, r._id, a, 60_000);
      const t0 = await repo.serverClockMs();
      const got = await repo.getWithFingerprintState(org, r._id);
      expect(got?.row._id).to.equal(r._id);
      expect(JSON.stringify(got?.row)).to.not.match(/"(writeSeq|writers|write_seq)"/);
      expect(got?.writeSeq).to.equal(1);
      expect(tokens(got!)).to.deep.equal([a]);
      expect(got!.serverNow).to.be.within(t0 - 5000, t0 + 5000);
      expect(await repo.getWithFingerprintState(org, fx.newId())).to.equal(null);
      expect(await repo.getWithFingerprintState(fx.newId(), r._id)).to.equal(null);
    });

    it('the write-state calls leave updatedAt alone and return rows without the state (F15b)', async () => {
      const org = fx.newId();
      const r = await row(org);
      const a = TOK('a');
      const stamp = (await repo.get(org, r._id))?.updatedAt;
      await repo.beginWrite(org, r._id, a, 60_000);
      await repo.renewWrite(org, r._id, a, 60_000);
      await repo.bumpSeq(org, r._id);
      await repo.endWrite(org, r._id, a);
      await repo.markAwaitingDirectUpload(org, r._id, { awaitingDirectUpload: true });
      await repo.adoptDirectUpload(org, r._id, 3);
      const after = await repo.get(org, r._id);
      expect(JSON.stringify(after)).to.not.match(/"(writeSeq|writers|write_seq)"/);
      expect(after?.updatedAt, 'only markAwaitingDirectUpload is a real document change').to.be.at.least(stamp ?? 0);
      const org2 = fx.newId();
      const r2 = await row(org2);
      const s2 = (await repo.get(org2, r2._id))?.updatedAt;
      await repo.beginWrite(org2, r2._id, a, 60_000);
      await repo.renewWrite(org2, r2._id, a, 60_000);
      await repo.bumpSeq(org2, r2._id);
      await repo.endWrite(org2, r2._id, a);
      expect((await repo.get(org2, r2._id))?.updatedAt).to.equal(s2);
    });
  });

  describe('connector scope', () => {
    const scopeOf = (org: string, id: string) => ({
      connectorId: id,
      prefixes: [`${org}/PipesHub/records/${id}`, `${org}/PipesHub/WebConnector/${id}`],
    });
    const tag = (id: string) => [{ key: 'connectorId', value: id }];

    it('pages keyset by id ascending over prefix and tagged rows, not siblings', async () => {
      const org = fx.newId();
      const c = 'conn1';
      const sc = scopeOf(org, c);
      const created = [];
      created.push(await repo.create(mk(org, { documentPath: sc.prefixes[0] })));
      created.push(await repo.create(mk(org, { documentPath: `${sc.prefixes[0]}/x/y` })));
      created.push(await repo.create(mk(org, { documentPath: `${sc.prefixes[1]}/z` })));
      created.push(await repo.create(mk(org, { documentPath: `${org}/PipesHub/elsewhere`, customMetadata: tag(c) })));
      await repo.create(mk(org, { documentPath: `${sc.prefixes[0]}2/sibling` }));
      await repo.create(mk(org, { documentPath: `${org}/PipesHub/elsewhere`, customMetadata: tag('other') }));
      await repo.create(mk(fx.newId(), { documentPath: sc.prefixes[0] }));
      const want = created.map((r) => r._id).sort();

      const seen: string[] = [];
      let after: string | undefined;
      for (let pages = 0; pages < 10; pages++) {
        const page = await repo.pageByConnector(org, sc, after, 2);
        seen.push(...page.map((p) => p._id));
        if (page.length < 2) break;
        after = page[page.length - 1]._id;
      }
      expect(seen).to.deep.equal(want);
      expect(Object.keys((await repo.pageByConnector(org, sc, undefined, 1))[0]).sort()).to.deep.equal(['_id', 'documentName', 'documentPath']);
    });

    it('streams tagged rows filed outside the prefixes, and deleteByConnector removes all of them', async () => {
      const org = fx.newId();
      const c = 'conn2';
      const sc = scopeOf(org, c);
      const inside = await repo.create(mk(org, { documentPath: `${sc.prefixes[0]}/a` }));
      const taggedInside = await repo.create(mk(org, { documentPath: `${sc.prefixes[1]}/a`, customMetadata: tag(c) }));
      const outside = await repo.create(mk(org, { documentPath: `${org}/PipesHub/flat/v`, customMetadata: tag(c) }));
      const keep = await repo.create(mk(org, { documentPath: `${org}/PipesHub/flat/w`, customMetadata: tag('keep') }));
      const streamed: string[] = [];
      for await (const d of repo.streamTaggedOutsidePrefixes(org, sc)) streamed.push(d._id);
      expect(streamed).to.deep.equal([outside._id]);
      expect(await repo.deleteByConnector(fx.newId(), sc)).to.equal(0);
      expect(await repo.deleteByConnector(org, sc)).to.equal(3);
      for (const gone of [inside, taggedInside, outside]) expect(await repo.get(org, gone._id)).to.equal(null);
      expect(await repo.get(org, keep._id)).to.not.equal(null);
    });
  });
}
