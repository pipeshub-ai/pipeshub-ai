/**
 * The storage fingerprint on a real document store and one real file store (STOR-FP). One file for every leg:
 *
 *   STORAGE_TYPE   local (default) | s3 | azureBlob        the file store (S3_ENDPOINT, S3_KEY, S3_SECRET / AZURE_CONNECTION_STRING)
 *   DOC_STORE      mongo (default; MONGO_IT_URI) | postgres (PG_TEST_BINS, PG_TEST_PGBOUNCER, PG_TEST_MIGRATE_PYTHON)
 *
 * The controllers run in this process behind their real router; the document repository and the storage adapter are wrapped
 * (tests/support/fingerprint-faults.ts) so one writer or reader of a schedule can be stopped at a named point while others run.
 * Tests that exist for one kind of file store are defined only for it at definition time (no skip, no pending).
 *
 * F12 replays the schedules of the reference model (tests/storage/fingerprint-model/model.py) on this stack. Two documented readings:
 *  - The stack's begin is one atomic operation (it prunes expired tokens and adds its own), so a replay cannot stop between the model's
 *    B1 (prune) and B2 (begin). The expectation is the model run with each writer's B1 next to its B2 (`pruneAdjacent`), a schedule of
 *    the same model.
 *  - A schedule in which a heartbeat is held back for longer than the window (`compliant: false`) is the documented residual: it is
 *    replayed and counted, but its `trusted` values are not asserted.
 */
import 'reflect-metadata';
import { execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { PgDocumentRepository } from '../../src/libs/repositories/document/pg';
import { MongoDocumentRepository } from '../../src/libs/repositories/document/mongo';
import { setDocumentRepository, type DocumentRepository } from '../../src/libs/repositories/document';
import { DocumentModel } from '../../src/modules/storage/schema/document.schema';
import { StorageController } from '../../src/modules/storage/controllers/storage.controller';
import { writeWindowHooks } from '../../src/modules/storage/controllers/storage.write-window';
import { scopedStorageServiceJwtGenerator } from '../../src/libs/utils/createJwt';
import { ServerClockUnavailable } from '../../src/libs/repositories/document/types';
import { makeOrgContext, runWithOrgContext } from '../../src/libs/context/orgContext';
import { actorOf, faultAdapter, faultRepository, newFaults, tagActor, type Faults } from '../support/fingerprint-faults';
import { finish, pruneAdjacent, runModel, type Schedule } from '../support/fingerprint-model';
import { startStorageApp, type StorageApp } from '../support/storage-stack';
import { PG_BINS, haveStack, startPgStack, type PgStack } from './pg-store-harness';

const BACKEND = (process.env.STORAGE_TYPE ?? 'local') as 'local' | 's3' | 'azureBlob';
// FP_DOC_STORE lets the PostgreSQL store suite run this file without changing DOC_STORE for the other suites of its process.
const STORE = (process.env.FP_DOC_STORE ?? process.env.DOC_STORE) === 'postgres' ? 'postgres' : 'mongo';
const SECRET = 'storage-fingerprint-scoped-secret';
const ORG = '64d000000000000000000b01';
const USER = '64d000000000000000000b02';
const WINDOW_MS = 1000;
const MODEL = path.join(__dirname, '../storage/fingerprint-model/model.py');
const REPLAY_SETS = ['s1_1w_1r', 's2_1w_dupend_1r', 's3_1w_2r', 's4_2w_1r', 's5_2w_2r', 's6_ticks', 's7_crash', 's8_nc15'] as const;
const REPLAY_COUNTS: Record<string, number> = {
  s1_1w_1r: 55, s2_1w_dupend_1r: 76, s3_1w_2r: 330, s4_2w_1r: 220, s5_2w_2r: 120, s6_ticks: 40, s7_crash: 200, s8_nc15: 1,
};

const sha = (b: Buffer | string): string => createHash('sha256').update(b).digest('hex');
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
const inOrg = <T>(fn: () => T): T =>
  runWithOrgContext(makeOrgContext({ orgId: ORG, userId: null, kind: 'service', source: 'scoped_jwt' }), fn);
const LEAK = /"(writeSeq|writers|write_seq)"/;

interface Fp {
  version: number;
  value: string;
  settled: boolean;
  verified: boolean;
  object: { size: number; etag: string; lastModified: string | null } | null;
  objectMissing: boolean;
  objectUnverifiable: boolean;
  reason?: string;
  trusted: boolean;
}
type Doc = Record<string, any> & { _id: string; storageFingerprint?: Fp };

const suite = (STORE === 'postgres' ? haveStack && PG_BINS.length > 0 : Boolean(process.env.MONGO_IT_URI)) ? describe : describe.skip;

suite('document fingerprint', function () {
  this.timeout(180_000);
  let app: StorageApp;
  let pg: PgStack | undefined;
  let mongo: MongoService | undefined;
  let real: DocumentRepository;
  let faults: Faults;
  let realAdapter: any;
  let home: string;
  const saved = { home: process.env.HOME, window: process.env.STORAGE_WRITE_WINDOW_MS };
  let counter = 0;

  const auth = { authorization: `Bearer ${scopedStorageServiceJwtGenerator(ORG, SECRET, USER)}` };
  const url = (p: string, q: Record<string, string> = {}): string => {
    const qs = new URLSearchParams(q).toString();
    return `${app.origin}/api/v1/document/internal${p}${qs ? `?${qs}` : ''}`;
  };
  const call = async (method: string, p: string, o: { q?: Record<string, string>; body?: BodyInit; json?: unknown; actor?: string; headers?: Record<string, string>; manual?: boolean } = {}) => {
    const headers: Record<string, string> = { ...auth, ...o.headers };
    if (o.actor) headers['x-test-actor'] = o.actor;
    let body = o.body;
    if (o.json !== undefined) {
      headers['content-type'] = 'application/json';
      body = JSON.stringify(o.json);
    }
    const res = await fetch(url(p, o.q), { method, headers, ...(body !== undefined && { body }), ...(o.manual && { redirect: 'manual' as const }) });
    const text = Buffer.from(await res.arrayBuffer());
    return { status: res.status, headers: res.headers, raw: text, text: text.toString('utf8') };
  };
  const json = (r: { text: string }): any => {
    try { return JSON.parse(r.text); } catch { return {}; }
  };
  const form = (name: string, bytes: Buffer | string, fields: Record<string, string> = {}): FormData => {
    const f = new FormData();
    f.append('file', new Blob([bytes], { type: 'text/plain' }), `${name}.txt`);
    for (const [k, v] of Object.entries(fields)) f.append(k, v);
    return f;
  };

  /** A PUT of raw bytes through a presigned link (no content type: the link is signed without one). */
  const putTo = async (link: string, bytes: Buffer | string): Promise<number> => {
    const headers: Record<string, string> = BACKEND === 'azureBlob' ? { 'x-ms-blob-type': 'BlockBlob' } : {};
    const res = await fetch(link, { method: 'PUT', body: Buffer.from(bytes), headers });
    await res.arrayBuffer();
    return res.status;
  };
  /** Starts an upload. On local disk the bytes are stored at once; S3 and Azure answer a redirect to a presigned link (every size). */
  const startUpload = async (bytes: Buffer | string, name: string, extra: Record<string, string> = {}, headers: Record<string, string> = {}, actor?: string) => {
    const r = await call('POST', '/upload', {
      body: form(name, bytes, { documentPath: `PipesHub/fp/${name}`, isVersionedFile: 'true', documentName: name, ...extra }),
      manual: true,
      headers,
      actor,
    });
    return { ...r, doc: json(r) as Doc, link: r.headers.get('location'), id: r.headers.get('x-document-id') };
  };
  const upload = async (bytes: Buffer | string = 'first bytes', name = `fp-${++counter}-${randomUUID().slice(0, 6)}`): Promise<Doc> => {
    const r = await startUpload(bytes, name);
    if (r.status === 200) return r.doc;
    expect(r.status, r.text).to.equal(301);
    expect(await putTo(r.link as string, bytes)).to.be.within(200, 201);
    return { ...(r.doc.document ?? {}), _id: r.id as string };
  };
  const putBuffer = (id: string, bytes: Buffer | string, actor?: string) => call('PUT', `/${id}/buffer`, { body: form('x', bytes), actor });
  const nextVersion = (id: string, bytes: Buffer | string) => call('POST', `/${id}/uploadNextVersion`, { body: form('x', bytes, { nextVersionNote: 'n', currentVersionNote: 'c' }) });
  const rollBack = (id: string, version: number) => call('POST', `/${id}/rollBack`, { json: { version, note: 'back' } });
  const info = async (id: string, verify = true, actor?: string) => {
    const r = await call('GET', `/${id}`, { q: verify ? { verify: '1' } : {}, actor });
    return { ...r, doc: json(r) as Doc };
  };
  const fp = async (id: string): Promise<Fp> => {
    const r = await info(id);
    expect(r.status, r.text).to.equal(200);
    return r.doc.storageFingerprint as Fp;
  };
  const row = (id: string): Promise<Doc | null> => inOrg(() => real.get(ORG, id)) as Promise<Doc | null>;
  const objectBytes = async (id: string): Promise<Buffer> => {
    const doc = (await row(id)) as any;
    const res = await realAdapter.getBufferFromStorageService(doc);
    return res.data as Buffer;
  };
  const state = (id: string) => inOrg(() => real.peekWriteState(ORG, id));
  const clock = () => inOrg(() => real.serverClockMs());
  /** The id of the keyed upload's row (it exists as soon as the first attempt started). */
  const row2 = async (name: string): Promise<string | undefined> => {
    for (let i = 0; i < 100; i++) {
      const found = pg
        ? (await pg.admin((c) => c.query<{ id: string }>('SELECT id FROM content.documents WHERE org_id = $1 AND document_name = $2', [ORG, name]))).rows[0]?.id
        : String((await DocumentModel.findOne({ orgId: ORG, documentName: name }).lean())?._id ?? '') || undefined;
      if (found) return found;
      await sleep(50);
    }
    return undefined;
  };
  const expireLease = async (id: string): Promise<void> => {
    if (pg) await pg.admin((c) => c.query("UPDATE content.documents SET upload_lease_expires_at = now() - interval '1 minute' WHERE id = $1", [id]));
    else await DocumentModel.updateOne({ _id: id }, { $set: { uploadLeaseExpiresAt: 1 } });
  };

  before(async () => {
    home = fs.mkdtempSync(path.join(os.tmpdir(), 'fp-home-'));
    process.env.HOME = home;
    process.env.STORAGE_WRITE_WINDOW_MS = String(WINDOW_MS);
    if (STORE === 'postgres') {
      pg = await startPgStack(PG_BINS[0] as string, 'content_stores');
      real = new PgDocumentRepository();
    } else {
      mongo = new MongoService({ uri: process.env.MONGO_IT_URI as string, db: `pipeshub_fp_${randomUUID().slice(0, 8)}` });
      await mongo.initialize();
      await DocumentModel.createCollection();
      await DocumentModel.syncIndexes();
      real = new MongoDocumentRepository();
    }
    faults = newFaults();
    setDocumentRepository(faultRepository(real, faults));
    // every adapter the controllers build is the real one of the leg inside the fault wrapper
    const original = StorageController.prototype.initializeStorageAdapter;
    sinon.stub(StorageController.prototype, 'initializeStorageAdapter').callsFake(async function (this: StorageController, req: any) {
      const adapter = await original.call(this, req);
      realAdapter ??= adapter;
      return faultAdapter(adapter, faults);
    });
    app = await startStorageApp({ storageType: BACKEND, scopedSecret: SECRET, name: 'fp-it', beforeRoutes: tagActor });
    app.controller.fingerprintTestPoint = (name) => faults.gates.hit(name);
    // build the real adapter once (outside any fault) for direct reads
    await upload('warm up');
  });

  after(async () => {
    sinon.restore();
    setDocumentRepository();
    await app?.close();
    if (pg) await pg.stop();
    if (mongo?.isConnected()) {
      await mongoose.connection.dropDatabase();
      await mongo.destroy();
    }
    if (saved.home === undefined) delete process.env.HOME; else process.env.HOME = saved.home;
    if (saved.window === undefined) delete process.env.STORAGE_WRITE_WINDOW_MS; else process.env.STORAGE_WRITE_WINDOW_MS = saved.window;
    fs.rmSync(home, { recursive: true, force: true });
  });

  beforeEach(() => {
    faults = newFaults();
    setDocumentRepository(faultRepository(real, faults));
    writeWindowHooks.heartbeat = 'auto';
    writeWindowHooks.onBegin = undefined;
  });
  afterEach(() => {
    faults.gates.releaseAll();
    delete process.env.STORAGE_FINGERPRINT_EPOCH;
  });

  // ---------------------------------------------------------------------------------------------------------------- W1-W3, W6
  it('F1 W1: a buffer write moves the fingerprint; the object size and etag are the new ones; settled and trusted', async () => {
    const d = await upload('old bytes');
    const before = await fp(d._id);
    expect(before).to.include({ trusted: true, settled: true, verified: true });
    expect((await putBuffer(d._id, 'new bytes!')).status).to.equal(200);
    const after = await fp(d._id);
    expect(after.value).to.not.equal(before.value);
    expect(after.object?.etag).to.not.equal(before.object?.etag);
    expect(after.object?.size).to.equal(Buffer.byteLength('new bytes!'));
    expect(after).to.include({ settled: true, trusted: true });
    expect(sha(await objectBytes(d._id))).to.equal(sha('new bytes!'));
  });

  it('F2 W2: uploadNextVersion moves the value, with and without a change since the last version', async () => {
    const d = await upload('v0 bytes');
    let last = (await fp(d._id)).value;
    for (const [label, prepare] of [
      ['plain next version', async () => undefined],
      ['current changed since the last version', async () => { await putBuffer(d._id, 'changed in place'); }],
    ] as const) {
      await prepare();
      const mid = (await fp(d._id)).value;
      const r = await nextVersion(d._id, `bytes of ${label}`);
      expect(r.status, r.text).to.equal(200);
      const now = await fp(d._id);
      expect(now.value, label).to.not.equal(mid);
      expect(now.value).to.not.equal(last);
      expect(now.trusted).to.equal(true);
      expect(now.object?.size).to.equal(Buffer.byteLength(`bytes of ${label}`));
      last = now.value;
    }
  });

  it('F3 W3: a rollback moves the value and the object is the restored version', async () => {
    const d = await upload('the first version');
    expect((await nextVersion(d._id, 'the second version')).status).to.equal(200);
    const before = await fp(d._id);
    const r = await rollBack(d._id, 0);
    expect(r.status, r.text).to.equal(200);
    const after = await fp(d._id);
    expect(after.value).to.not.equal(before.value);
    expect(after.trusted).to.equal(true);
    expect(sha(await objectBytes(d._id))).to.equal(sha('the first version'));
  });

  it('F6 W6: a new upload is trusted straight after its save', async () => {
    const d = await upload('brand new');
    const f = await fp(d._id);
    expect(f).to.include({ trusted: true, settled: true });
    expect(f.object?.size).to.equal(Buffer.byteLength('brand new'));
    expect((await state(d._id))?.writers).to.deep.equal([]);
  });

  // ------------------------------------------------------------------------------------------------------------- W4 / links
  if (BACKEND === 'local') {
    it('F4 local: a direct-upload link is not a write path; issuing it moves the unverified value, the object is unchanged', async () => {
      const d = await upload('stays');
      const before = await fp(d._id);
      const unverifiedBefore = (await info(d._id, false)).doc.storageFingerprint as Fp;
      const r = await call('POST', `/${d._id}/directUpload`);
      expect(r.status, r.text).to.equal(200);
      const unverifiedAfter = (await info(d._id, false)).doc.storageFingerprint as Fp;
      expect(unverifiedAfter.value).to.not.equal(unverifiedBefore.value);
      expect(unverifiedAfter).to.include({ verified: false, trusted: false });
      expect((await fp(d._id)).object?.etag).to.equal(before.object?.etag);
      expect(sha(await objectBytes(d._id))).to.equal(sha('stays'));
    });
  } else {
    it('F4 S3/Azure: issuing a link moves the unverified value; a PUT through it moves the etag; a PUT through the OLD link after a W1 write moves it again', async () => {
      const d = await upload('original');
      const link = async (): Promise<string> => json(await call('POST', `/${d._id}/directUpload`)).signedUrl as string;
      const put = async (u: string, b: string) => {
        expect(await putTo(u, b)).to.be.within(200, 201);
      };
      const e0 = (await fp(d._id)).object?.etag;
      const u0 = (await info(d._id, false)).doc.storageFingerprint as Fp;
      const oldLink = await link();
      const u1 = (await info(d._id, false)).doc.storageFingerprint as Fp;
      expect(u1.value).to.not.equal(u0.value);
      expect(u1.trusted).to.equal(false);
      await put(oldLink, 'through the link');
      const e1 = (await fp(d._id)).object?.etag;
      expect(e1).to.not.equal(e0);
      expect((await putBuffer(d._id, 'through the controller')).status).to.equal(200);
      const e2 = (await fp(d._id)).object?.etag;
      await put(oldLink, 'through the old link');
      const f3 = await fp(d._id);
      expect(f3.object?.etag).to.not.equal(e2);
      expect(f3.object?.size).to.equal(Buffer.byteLength('through the old link'));
    });
  }

  // ---------------------------------------------------------------------------------------------------------------- failures
  it('F7a: the save fails after the bytes landed: 5xx, the value moved, settled after the end, the object is the new bytes', async () => {
    const d = await upload('before');
    const before = await fp(d._id);
    faults.failRepo.set('update', new Error('save failed (test fault)'));
    const r = await putBuffer(d._id, 'landed anyway');
    expect(r.status).to.be.at.least(500);
    faults.failRepo.delete('update');
    const after = await fp(d._id);
    expect(after.value).to.not.equal(before.value);
    expect(after).to.include({ settled: true, trusted: true });
    expect(sha(await objectBytes(d._id))).to.equal(sha('landed anyway'));
  });

  it('F7b: the second clone of a rollback fails after the first wrote: 5xx, bytes changed, row unchanged, value moved, settled', async () => {
    const d = await upload('version zero');
    expect((await nextVersion(d._id, 'version one')).status).to.equal(200);
    const before = await fp(d._id);
    const rowBefore = await row(d._id);
    faults.adapterCalls.uploadDocumentToStorageService = 0;
    faults.failAdapterNth.set('uploadDocumentToStorageService', { n: 2, error: new Error('second clone failed (test fault)') });
    const r = await rollBack(d._id, 0);
    expect(r.status).to.be.at.least(500);
    faults.failAdapterNth.clear();
    const after = await fp(d._id);
    expect(sha(await objectBytes(d._id))).to.equal(sha('version zero'));
    expect(after.value).to.not.equal(before.value);
    expect(after).to.include({ settled: true });
    const rowAfter = await row(d._id);
    expect(rowAfter?.mutationCount).to.equal(rowBefore?.mutationCount);
    expect(rowAfter?.versionHistory?.length).to.equal(rowBefore?.versionHistory?.length);
  });

  it('F8: endWrite fails too: the token stays, not settled, not trusted; after the window it is settled, and the next begin prunes it', async () => {
    const d = await upload('x');
    faults.failEnd = 2;
    expect((await putBuffer(d._id, 'y')).status, 'the write itself still succeeds').to.equal(200);
    const mid = await fp(d._id);
    expect(mid).to.include({ settled: false, trusted: false });
    expect((await state(d._id))?.writers).to.have.length(1);
    await sleep(WINDOW_MS * 1.5);
    expect(await fp(d._id)).to.include({ settled: true, trusted: true });
    expect((await putBuffer(d._id, 'z')).status).to.equal(200);
    expect((await state(d._id))?.writers, 'the lapsed token was pruned by the next begin').to.deep.equal([]);
  });

  it('F8b: the server clock is unavailable: a write is 503 before any byte; a read answers "not trusted" with the reason', async () => {
    const d = await upload('untouched');
    faults.failRepo.set('beginWrite', new ServerClockUnavailable());
    const w = await putBuffer(d._id, 'never written');
    expect(w.status).to.equal(503);
    faults.failRepo.delete('beginWrite');
    expect(sha(await objectBytes(d._id))).to.equal(sha('untouched'));
    faults.failRepo.set('getWithFingerprintState', new ServerClockUnavailable());
    const r = await info(d._id);
    expect(r.status).to.equal(200);
    expect(r.doc.storageFingerprint).to.include({ trusted: false, reason: 'server_clock_unavailable' });
  });

  it('F9: the store write throws before the bytes land: the object is unchanged and the value still moved (a false "changed")', async () => {
    const d = await upload('as before');
    const before = await fp(d._id);
    faults.failAdapterBefore.set('updateBuffer', new Error('store down (test fault)'));
    expect((await putBuffer(d._id, 'never lands')).status).to.be.at.least(500);
    faults.failAdapterBefore.clear();
    const after = await fp(d._id);
    expect(sha(await objectBytes(d._id))).to.equal(sha('as before'));
    expect(after.value).to.not.equal(before.value);
    expect(after.object?.etag).to.equal(before.object?.etag);
    expect(after.trusted).to.equal(true);
  });

  it('F10: the row is removed between the read and the begin: 404 and the object is unchanged', async () => {
    const d = await upload('kept');
    const gate = faults.gates.arm('W', 'W:afterPrune');
    const pending = putBuffer(d._id, 'too late', 'W');
    await gate.reached();
    const bytes = await objectBytes(d._id);
    await inOrg(() => real.remove(d._id, ORG));
    gate.release();
    expect((await pending).status).to.equal(404);
    expect(sha(bytes)).to.equal(sha('kept'));
    if (BACKEND === 'local') {
      // the file may still be there (the row was the thing removed); what matters is that the write did not run
      expect(faults.adapterCalls.updateBuffer ?? 0).to.equal(0);
    }
  });

  it('F16: the object is gone: 200 objectMissing, not trusted; the object store failing: 503', async () => {
    const d = await upload('will vanish');
    // on S3 and Azure a finished upload is adopted by the first verified read; "missing" is said of an adopted document
    await fp(d._id);
    const doc = (await row(d._id)) as any;
    await realAdapter.deleteObject(doc);
    const gone = await info(d._id);
    expect(gone.status).to.equal(200);
    expect(gone.doc.storageFingerprint).to.include({ objectMissing: true, trusted: false, verified: true });
    expect(gone.doc.storageFingerprint?.object).to.equal(null);
    const e = await upload('will fail');
    faults.failAdapterBefore.set('statObject', new Error('head failed (test fault)'));
    expect((await info(e._id)).status).to.equal(503);
    faults.failAdapterBefore.clear();
  });

  // ------------------------------------------------------------------------------------------------------------------ races
  it('F11: 20 parallel writers, 20 times: writeSeq ends exactly start + 40 and no token is left', async () => {
    let lost = 0;
    for (let round = 0; round < 20; round++) {
      const d = await upload(`round ${round}`);
      const start = (await state(d._id))?.writeSeq as number;
      const startMutations = (await row(d._id))?.mutationCount as number;
      const results = await Promise.all(Array.from({ length: 20 }, (_, i) => putBuffer(d._id, `w${round}-${i}`)));
      expect(results.filter((r) => r.status !== 200).map((r) => r.status)).to.deep.equal([]);
      const end = await state(d._id);
      expect(end?.writeSeq, `round ${round}`).to.equal(start + 40);
      expect(end?.writers).to.deep.equal([]);
      if ((((await row(d._id))?.mutationCount as number) - startMutations) < 20) lost++;
    }
    // the control, informational: a counter read, incremented and saved by each writer can lose updates (it depends on timing, so it
    // is reported and not asserted); the sequence did not lose any
    process.stdout.write(`F11 control: mutationCount lost an increment in ${lost} of 20 rounds\n`);
  });

  it('F13a: a writer paused after its bytes: the unverified value differs and is not settled; a verified read is not trusted', async () => {
    const d = await upload('stable');
    const before = (await info(d._id, false)).doc.storageFingerprint as Fp;
    const gate = faults.gates.arm('W', 'W:afterBytes');
    const pending = putBuffer(d._id, 'in flight', 'W');
    await gate.reached();
    const mid = (await info(d._id, false)).doc.storageFingerprint as Fp;
    expect(mid.value).to.not.equal(before.value);
    expect(mid.settled).to.equal(false);
    expect(await fp(d._id)).to.include({ trusted: false, settled: false });
    gate.release();
    expect((await pending).status).to.equal(200);
    expect(await fp(d._id)).to.include({ trusted: true, settled: true });
  });

  it('F13b: a reader that read the row, then a write, then a revert to the old bytes (a stale link on S3/Azure, a controller write on local): the final value differs from every earlier trusted one', async () => {
    const d = await upload('bytes X');
    const trustedValues = new Set<string>([(await fp(d._id)).value]);
    const oldLink = BACKEND === 'local' ? undefined : (json(await call('POST', `/${d._id}/directUpload`)).signedUrl as string);
    const reader = { row: faults.gates.arm('R', 'R:afterRow'), stat: faults.gates.arm('R', 'R:afterStat'), reread: faults.gates.arm('R', 'R:afterReread') };
    const slow = info(d._id, true, 'R');
    await reader.row.reached();
    expect((await putBuffer(d._id, 'bytes Y')).status).to.equal(200);
    reader.row.release();
    await reader.stat.reached();
    // the revert: through a stale presigned link on S3/Azure; on local disk there is no link, the controller writes the old bytes
    if (oldLink) expect(await putTo(oldLink, 'bytes X')).to.be.within(200, 201);
    else expect((await putBuffer(d._id, 'bytes X')).status).to.equal(200);
    reader.stat.release();
    await reader.reread.reached();
    reader.reread.release();
    const r = await slow;
    expect(r.doc.storageFingerprint?.trusted, 'the sequence moved under the reader').to.equal(false);
    const final = await fp(d._id);
    expect(final.trusted).to.equal(true);
    expect(trustedValues.has(final.value), 'the old bytes under a new sequence').to.equal(false);
  });

  it('F19: a writer slower than the window stays live by heartbeat; a short writer does not settle the document; a blocked heartbeat lets the token lapse', async () => {
    const d = await upload('slow');
    const gate = faults.gates.arm('S', 'W:afterBytes');
    const slow = putBuffer(d._id, 'slow bytes', 'S');
    await gate.reached();
    for (let i = 0; i < 3; i++) {
      await sleep(WINDOW_MS);
      expect(await fp(d._id), `after ${i + 1} windows`).to.include({ settled: false, trusted: false });
    }
    expect((await putBuffer(d._id, 'quick')).status).to.equal(200);
    expect(await fp(d._id), 'the short writer ended; the slow one is still live').to.include({ settled: false, trusted: false });
    gate.release();
    expect((await slow).status).to.equal(200);
    expect(await fp(d._id)).to.include({ settled: true, trusted: true });

    faults.failRepo.set('renewWrite', new Error('db unreachable (test fault)'));
    const blocked = faults.gates.arm('B', 'W:afterBytes');
    const stuck = putBuffer(d._id, 'blocked', 'B');
    await blocked.reached();
    await sleep(WINDOW_MS * 1.6);
    expect((await fp(d._id)).settled, 'documented residual: the token lapsed under a live writer').to.equal(true);
    faults.failRepo.delete('renewWrite');
    blocked.release();
    await stuck;
  });

  // ------------------------------------------------------------------------------------------------------------------ edges
  it('F15: without verify every pre-existing field equals the stored row, the object store is not asked, verify=2 is 400, responses are no-store', async () => {
    const d = await upload('compat');
    const stored = JSON.parse(JSON.stringify(await row(d._id)));
    faults.adapterCalls = {};
    const r = await info(d._id, false);
    const { storageFingerprint, ...rest } = r.doc;
    expect(rest).to.deep.equal(stored);
    expect(storageFingerprint).to.include({ verified: false, trusted: false });
    expect(faults.adapterCalls.statObject ?? 0).to.equal(0);
    expect(r.headers.get('cache-control')).to.equal('no-store');
    expect((await call('GET', `/${d._id}`, { q: { verify: '2' } })).status).to.equal(400);
    expect((await call('GET', `/${d._id}`, { q: { verify: '1' } })).headers.get('cache-control')).to.equal('no-store');
  });

  it('F15b: no response of a write path or a read carries the write state', async () => {
    const d = await upload('leaky?');
    const texts = [
      (await info(d._id, false)).text,
      (await info(d._id, true)).text,
      (await putBuffer(d._id, 'a')).text,
      (await nextVersion(d._id, 'b')).text,
      (await rollBack(d._id, 0)).text,
      (await call('POST', `/${d._id}/directUpload`)).text,
    ];
    for (const t of texts) expect(t).to.not.match(LEAK);
    expect(JSON.stringify(await row(d._id))).to.not.match(LEAK);
  });

  it('F17: deleted and retired rows are not trusted', async () => {
    const dead = await upload('to delete');
    expect((await call('DELETE', `/${dead._id}/`)).status).to.be.within(200, 204);
    const gone = await info(dead._id);
    if (gone.status === 200) expect(gone.doc.storageFingerprint?.trusted).to.equal(false);
    const retired = await upload('to retire');
    expect((await call('POST', `/${retired._id}/retire`)).status).to.equal(200);
    expect((await fp(retired._id)).trusted).to.equal(false);
  });

  it('F21: bumping STORAGE_FINGERPRINT_EPOCH changes the value of a document with identical bytes and counters, and only for as long as it is set', async () => {
    const d = await upload('epoch');
    const a = await fp(d._id);
    process.env.STORAGE_FINGERPRINT_EPOCH = '5';
    const b = await fp(d._id);
    expect(b.value).to.not.equal(a.value);
    expect(b.object).to.deep.equal(a.object);
    delete process.env.STORAGE_FINGERPRINT_EPOCH;
    expect((await fp(d._id)).value).to.equal(a.value);
  });

  if (BACKEND === 'local') {
    it('F18 local: a download bound to the verified identity: 412 after an overwrite, 200 with the new identity, ETag equals object.etag', async () => {
      const d = await upload('bound bytes');
      const f0 = await fp(d._id);
      const ok = await call('GET', `/${d._id}/download`, { q: { ifMatch: f0.object?.etag as string } });
      expect(ok.status).to.equal(200);
      expect(ok.text).to.equal('bound bytes');
      expect(ok.headers.get('etag')).to.equal(`"${f0.object?.etag}"`);
      expect((await putBuffer(d._id, 'overwritten')).status).to.equal(200);
      expect((await call('GET', `/${d._id}/download`, { q: { ifMatch: f0.object?.etag as string } })).status).to.equal(412);
      const f1 = await fp(d._id);
      const again = await call('GET', `/${d._id}/download`, { q: { ifMatch: f1.object?.etag as string } });
      expect(again.status).to.equal(200);
      expect(again.text).to.equal('overwritten');
      expect(again.headers.get('etag')).to.equal(`"${f1.object?.etag}"`);
    });

    it('F20 local: a download open across a rollback reads the whole old or the whole new file, never a mix', async () => {
      const oldBytes = Buffer.alloc(3 * 1024 * 1024, 'o');
      const newBytes = Buffer.alloc(3 * 1024 * 1024, 'n');
      const d = await upload(oldBytes);
      expect((await nextVersion(d._id, newBytes)).status).to.equal(200);
      // every iteration flips the current file between the two contents, so every download overlaps a write that changes the bytes
      let current: 'o' | 'n' = 'n';
      for (let i = 0; i < 12; i++) {
        const target = current === 'n' ? 0 : 1; // version 0 holds the old bytes, version 1 the new ones
        const dl = fetch(url(`/${d._id}/download`), { headers: auth });
        const [res, rb] = await Promise.all([dl, rollBack(d._id, target)]);
        expect(rb.status, rb.text).to.equal(200);
        current = current === 'n' ? 'o' : 'n';
        const got = Buffer.from(await res.arrayBuffer());
        const kinds = new Set(got.toString('latin1').split('').filter((c, k) => k % 4099 === 0));
        expect(kinds.size, `iteration ${i}: a mix of old and new bytes`).to.equal(1);
        expect(got.length).to.equal(3 * 1024 * 1024);
        expect((await objectBytes(d._id))[0], `iteration ${i}: the rollback changed the bytes`).to.equal(current.charCodeAt(0));
      }
    });
  }


  // ------------------------------------------------------------------------------------------ moves, flags, keyed uploads
  it('F14: moving a tree keeps the object size; reads work afterwards (no assertion on value or lastModified)', async () => {
    const name = `mv-${++counter}-${randomUUID().slice(0, 6)}`;
    const r = await startUpload('moved bytes', name, { documentPath: `mv/${name}/inner` });
    let id: string;
    if (r.status === 200) id = r.doc._id;
    else {
      expect(await putTo(r.link as string, 'moved bytes')).to.be.within(200, 201);
      id = r.id as string;
    }
    const before = await fp(id);
    const moved = await call('POST', '/move-tree', { json: { oldPath: `mv/${name}`, newPath: `mv/${name}-moved` } });
    expect(moved.status, moved.text).to.be.within(200, 204);
    const after = await info(id);
    expect(after.status, after.text).to.equal(200);
    expect(after.doc.storageFingerprint?.object?.size).to.equal(before.object?.size);
    expect(after.doc.storageFingerprint?.objectMissing).to.equal(false);
    if (BACKEND === 's3') expect(after.doc.storageFingerprint?.object?.etag, 'a copy keeps the ETag of a single-PUT S3 object').to.equal(before.object?.etag);
    expect(sha(await objectBytes(id))).to.equal(sha('moved bytes'));
  });

  it('F17: an artifact set has no object fingerprint and is not trusted', async () => {
    const r = await call('POST', '/artifacts/presign', {
      json: { virtualRecordId: `vr-${randomUUID().slice(0, 8)}`, rev: randomUUID().replace(/-/g, '').slice(0, 16), files: [{ path: 'manifest.json', contentType: 'application/json' }] },
    });
    expect(r.status, r.text).to.equal(200);
    const id = json(r).documentId as string;
    const got = await info(id);
    expect(got.status, got.text).to.equal(200);
    expect(got.doc.storageFingerprint).to.include({ trusted: false });
    expect(got.doc.storageFingerprint?.object).to.equal(null);
  });

  // Only a proxied upload has a late loser writing bytes after a takeover; on S3 and Azure every upload is a link the caller PUTs
  // to (F4/F5 cover those bytes), so there is no server-side write to hold.
  if (BACKEND === 'local') {
    it('F6 keyed: the late loser of an Idempotency-Key takeover writes after the winner saved; the value moves, the window held the document unsettled, and it settles after', async () => {
      const key = `k-${randomUUID()}`;
      const name = `keyed-${++counter}-${randomUUID().slice(0, 6)}`;
      const gate = faults.gates.arm('L', 'W:beforeUpload');
      const loser = startUpload('same bytes', name, {}, { 'idempotency-key': key }, 'L');
      await gate.reached();
      // the loser's lease lapses; a retry with the same key takes the upload over and finishes it
      const id = (await row2(name)) as string;
      expect(id, 'the keyed upload created its row').to.be.a('string');
      await expireLease(id);
      const winner = await startUpload('same bytes', name, {}, { 'idempotency-key': key }, 'N');
      if (winner.status !== 200 && winner.status !== 301) throw new Error(`winner ${String(winner.status)} ${winner.text.slice(0, 300)}`);
      if (winner.status === 301) expect(await putTo(winner.link as string, 'same bytes')).to.be.within(200, 201);
      else expect(winner.status).to.equal(200);
      const mid = await fp(id);
      expect(mid.trusted, 'the loser still holds its window').to.equal(false);
      gate.release();
      await loser;
      const end = await fp(id);
      expect(end.settled).to.equal(true);
      expect(end.value).to.not.equal(mid.value);
    });
  }

  // -------------------------------------------------------------------------------------------- S3 and Azure only
  if (BACKEND !== 'local') {
    it('F5 S3/Azure: a finished direct upload is adopted once, whoever reads first; sizes follow the object', async () => {
      const name = `big-${++counter}-${randomUUID().slice(0, 6)}`;
      const start = await startUpload(Buffer.alloc(1024, 'x'), name);
      expect(start.status, start.text).to.equal(301);
      const id = start.id as string;
      const waiting = await info(id);
      expect(waiting.doc.awaitingDirectUpload).to.equal(true);
      expect(waiting.doc.storageFingerprint).to.include({ trusted: false });
      expect(waiting.doc.storageFingerprint?.object).to.equal(null);
      // declared 1 KiB, the client sends 5 bytes
      expect(await putTo(start.link as string, 'five!')).to.be.within(200, 201);
      const a = { row: faults.gates.arm('A', 'R:afterRow'), stat: faults.gates.arm('A', 'R:afterStat') };
      const b = { row: faults.gates.arm('B', 'R:afterRow'), stat: faults.gates.arm('B', 'R:afterStat') };
      const ra = info(id, true, 'A');
      const rb = info(id, true, 'B');
      await Promise.all([a.row.reached(), b.row.reached()]);
      a.row.release(); b.row.release();
      await Promise.all([a.stat.reached(), b.stat.reached()]);
      a.stat.release(); b.stat.release();
      const [x, y] = await Promise.all([ra, rb]);
      for (const r of [x, y]) {
        expect(r.doc.awaitingDirectUpload ?? false).to.equal(false);
        expect(r.doc.storageFingerprint?.object?.size).to.equal(5);
        expect(r.doc.sizeInBytes).to.equal(5);
        expect(r.doc.storageFingerprint?.trusted).to.equal(true);
      }
      expect(x.doc.storageFingerprint?.value).to.equal(y.doc.storageFingerprint?.value);
      expect(faults.adopted, 'exactly one adoption').to.equal(1);
      // a second PUT after the adoption moves the etag
      const before = (await fp(id)).object?.etag;
      const again = json(await call('POST', `/${id}/directUpload`)).signedUrl as string;
      expect(await putTo(again, 'second')).to.be.within(200, 201);
      expect((await fp(id)).object?.etag).to.not.equal(before);
    });

    it('F5 S3/Azure: a second PUT between the stat and the adopt: the final pass reports the new size, and the stored size converges', async () => {
      const start = await startUpload(Buffer.alloc(1024, 'x'), `big-${++counter}-${randomUUID().slice(0, 6)}`);
      const id = start.id as string;
      expect(await putTo(start.link as string, 'five!')).to.be.within(200, 201);
      const g = faults.gates.arm('W2', 'R:afterStat');
      const slow = info(id, true, 'W2');
      await g.reached();
      expect(await putTo(start.link as string, 'seven!!')).to.be.within(200, 201);
      g.release();
      const r = await slow;
      expect(r.doc.storageFingerprint?.object?.size).to.equal(7);
      expect(r.doc.sizeInBytes).to.equal(7);
      await sleep(100);
      expect((await row(id))?.sizeInBytes, 'the stored column converged').to.equal(7);
    });

    it('F5 S3/Azure: the row removed between the loser\'s adopt and its second pass: 404', async () => {
      const start = await startUpload(Buffer.alloc(1024, 'x'), `big-${++counter}-${randomUUID().slice(0, 6)}`);
      const id = start.id as string;
      expect(await putTo(start.link as string, 'five!')).to.be.within(200, 201);
      const g = faults.gates.arm('L', 'W:afterAdopt');
      const loser = info(id, true, 'L');
      await g.reached();
      await inOrg(() => real.remove(id, ORG));
      g.release();
      expect((await loser).status).to.equal(404);
    });

    it('F5 S3/Azure: abort on an object that arrived answers 409 and leaves the adopted state', async () => {
      const start = await startUpload(Buffer.alloc(1024, 'x'), `big-${++counter}-${randomUUID().slice(0, 6)}`);
      const id = start.id as string;
      expect(await putTo(start.link as string, 'five!')).to.be.within(200, 201);
      expect((await call('POST', `/${id}/abortDirectUpload`)).status).to.equal(409);
      const after = await info(id);
      expect(after.doc.awaitingDirectUpload ?? false).to.equal(false);
      expect(after.doc.storageFingerprint).to.include({ trusted: true });
      expect(after.doc.storageFingerprint?.object?.size).to.equal(5);
    });

    if (BACKEND === 's3') {
      it('F5b S3: a stored URL the provider cannot parse (path-style) answers 200 objectUnverifiable, not 503; other store failures still 503', async () => {
        const d = await upload('path style');
        await inOrg(() => real.update(ORG, d._id, { s3: { url: 'http://127.0.0.1:9000/some-bucket/some/key.txt' } }));
        const r = await info(d._id);
        expect(r.status, r.text).to.equal(200);
        expect(r.doc.storageFingerprint).to.include({ objectUnverifiable: true, trusted: false, reason: 'unparseable_storage_url' });
        expect(r.doc.storageFingerprint?.object).to.equal(null);
        const e = await upload('store down');
        faults.failAdapterBefore.set('statObject', new Error('head failed (test fault)'));
        expect((await info(e._id)).status).to.equal(503);
        faults.failAdapterBefore.clear();
      });
    }

    if (BACKEND === 'azureBlob') {
      it('F5b Azure: a stored URL outside the container answers 200 objectUnverifiable, not 503', async () => {
        const d = await upload('other container');
        await inOrg(() => real.update(ORG, d._id, { azureBlob: { url: 'http://127.0.0.1:10000/devstoreaccount1/some-other-container/some/key.txt' } }));
        const r = await info(d._id);
        expect(r.status, r.text).to.equal(200);
        expect(r.doc.storageFingerprint).to.include({ objectUnverifiable: true, trusted: false, reason: 'unparseable_storage_url' });
        expect(r.doc.storageFingerprint?.object).to.equal(null);
      });
    }

    it('F18 S3/Azure: a download bound by If-Match: 412 after an overwrite, and the response ETag equals object.etag', async () => {
      const d = await upload('bytes X');
      const f0 = await fp(d._id);
      const signed = async (): Promise<string> => json(await call('GET', `/${d._id}/download`)).signedUrl as string;
      const get = (u: string, etag: string) => fetch(u, { headers: { 'if-match': `"${etag}"` } });
      const ok = await get(await signed(), f0.object?.etag as string);
      expect(ok.status).to.equal(200);
      expect(ok.headers.get('etag')?.replace(/^W\//, '').replace(/"/g, '')).to.equal(f0.object?.etag);
      expect(Buffer.from(await ok.arrayBuffer()).toString()).to.equal('bytes X');
      expect((await putBuffer(d._id, 'bytes Y')).status).to.equal(200);
      const stale = await get(await signed(), f0.object?.etag as string);
      await stale.arrayBuffer();
      expect(stale.status, 'the object changed after it was verified').to.equal(412);
      // an overwrite through the old link that puts the same bytes back
      const link = json(await call('POST', `/${d._id}/directUpload`)).signedUrl as string;
      expect(await putTo(link, 'bytes X')).to.be.within(200, 201);
      const restored = await get(await signed(), f0.object?.etag as string);
      const restoredBytes = Buffer.from(await restored.arrayBuffer()).toString();
      if (BACKEND === 's3') {
        expect(restored.status, 'S3 ETags follow the content').to.equal(200);
        expect(restoredBytes).to.equal('bytes X');
      } else {
        expect(restored.status, 'Azure ETags change on every write').to.equal(412);
      }
    });
  }

  // --------------------------------------------------------------------------------------------------------- F12 replay
  describe('interleavings (F12)', () => {
    const emit = (set: string): Schedule[] =>
      JSON.parse(execFileSync('python3', [MODEL, 'emit', '--set', set], { maxBuffer: 256 * 1024 * 1024 }).toString()) as Schedule[];

    it('the model file prints the fixed counts: 1,042 schedules, 351 with ticks', () => {
      const out = execFileSync('python3', [MODEL, 'counts']).toString();
      expect(out).to.match(/total 1042/);
      const withTicks = [...out.matchAll(/with ticks: (\d+)/g)].reduce((n, m) => n + Number(m[1]), 0);
      expect(withTicks).to.equal(351);
    });

    for (const set of REPLAY_SETS) {
      it(`${set}: the real trusted of every read equals the model's, and every trusted value names one object content`, async function () {
        this.timeout(30 * 60 * 1000);
        const limit = Number(process.env.FP_REPLAY_LIMIT ?? 0);
        const all = emit(set);
        expect(all.length).to.equal(REPLAY_COUNTS[set]);
        const schedules = limit > 0 ? all.slice(0, limit) : all;
        const failures: string[] = [];
        for (const sch of schedules) {
          // the port of the model must agree with the model's own expectation, or the replay proves nothing
          for (const em of ['strong', 'coarse'] as const) {
            const mine = runModel({ writers: sch.writers, hb: sch.hb, order: finish(sch.order) }, em);
            const theirs = em === 'strong' ? sch.expected_strong : sch.expected_coarse;
            for (const [r, d] of Object.entries(theirs)) {
              if (mine.reads[r]?.trusted !== d.trusted) failures.push(`${sch.id}: the TypeScript port and the model disagree on ${r} (${em})`);
            }
          }
          const result = await replay(sch);
          failures.push(...result.failures);
        }
        expect(failures.slice(0, 10), `${failures.length} mismatches`).to.deep.equal([]);
      });
    }
  });

  /** Runs one schedule of the model against the real stack and reports every difference. */
  async function replay(sch: Schedule): Promise<{ failures: string[] }> {
    const failures: string[] = [];
    const fail = (m: string): void => { failures.push(`${sch.id}: ${m}`); };
    faults = newFaults();
    setDocumentRepository(faultRepository(real, faults));
    writeWindowHooks.heartbeat = 'manual';
    const tokens: Record<string, string> = {};
    writeWindowHooks.onBegin = (i) => { tokens[actorOf()] = i.token; };
    const d = await upload('c0 content');
    const id = d._id;
    // on S3 and Azure a finished upload is adopted by the first verified read; the schedules start from the adopted document
    await fp(id);
    for (const [w, steps] of Object.entries(sch.writers)) if (!steps.includes('E1')) faults.crashed.add(w);
    const wg: Record<string, Record<string, ReturnType<Faults['gates']['arm']>>> = {};
    for (const w of Object.keys(sch.writers))
      wg[w] = { prune: faults.gates.arm(w, 'W:afterPrune'), begin: faults.gates.arm(w, 'W:afterBegin'), bytes: faults.gates.arm(w, 'W:afterBytes') };
    const rg: Record<string, Record<string, ReturnType<Faults['gates']['arm']>>> = {};
    for (const r of sch.readers)
      rg[r] = { row: faults.gates.arm(r, 'R:afterRow'), stat: faults.gates.arm(r, 'R:afterStat'), reread: faults.gates.arm(r, 'R:afterReread') };
    const requests: Promise<unknown>[] = [];
    const pending: Record<string, Promise<{ doc: Doc }>> = {};
    const pend: Record<string, number> = {};
    const real_: Record<string, { trusted: boolean; value: string; label: string }> = {};
    const labelOf: Record<string, string> = {};
    const t0 = Date.now();
    let tickMs = 0;
    const readerDone = async (r: string): Promise<void> => {
      const res = await (pending[r] as Promise<{ doc: Doc }>);
      const f = res.doc.storageFingerprint as Fp;
      real_[r] = { trusted: f.trusted, value: f.value, label: labelOf[r] as string };
    };

    for (const [actor, idx] of finish(sch.order)) {
      if (actor === 'T') {
        const start = await clock();
        let now = start;
        while (now - start < 0.6 * WINDOW_MS) { await sleep(20); now = await clock(); }
        if (now - start > 0.9 * WINDOW_MS) fail(`HARNESS_TIMING: a tick took ${now - start} ms`);
        tickMs += now - start;
      } else if (actor in sch.writers) {
        const step = (sch.writers[actor] as string[])[idx];
        const g = wg[actor] as Record<string, ReturnType<Faults['gates']['arm']>>;
        if (step === 'B1') {
          const p = putBuffer(id, `writer ${actor}`, actor);
          requests.push(p);
          await (g.prune as any).reached();
        } else if (step === 'B2') {
          (g.prune as any).release();
          await (g.begin as any).reached();
        } else if (step === 'BY') {
          (g.begin as any).release();
          await (g.bytes as any).reached();
        } else if (step === 'E1') {
          const ended = faults.gates.expectSignal(actor, 'W:afterEnd');
          (g.bytes as any).release();
          await ended;
        } else if (step === 'E2') {
          await inOrg(() => real.endWrite(ORG, id, tokens[actor] as string));
        }
      } else if (actor.startsWith('H')) {
        const w = actor[1] as string;
        const kind = (sch.hb[actor] as string[])[idx];
        if (kind === 'H1') pend[w] = (await clock()) + WINDOW_MS;
        else if (tokens[w] !== undefined) {
          const now = await clock();
          await inOrg(() => real.renewWrite(ORG, id, tokens[w] as string, (pend[w] as number) - now));
        }
      } else if (actor === 'F') {
        if (idx === 2) {
          labelOf.F = sha(await objectBytes(id));
          pending.F = info(id, true);
          await readerDone('F');
        }
      } else {
        const g = rg[actor] as Record<string, ReturnType<Faults['gates']['arm']>>;
        if (idx === 0) {
          pending[actor] = info(id, true, actor);
          await (g.row as any).reached();
        } else if (idx === 1) {
          (g.row as any).release();
          await (g.stat as any).reached();
          // the labelled-read oracle: the content the store holds at the moment the reader has stat-ed
          labelOf[actor] = sha(await objectBytes(id));
        } else {
          (g.stat as any).release();
          await (g.reread as any).reached();
          (g.reread as any).release();
          await readerDone(actor);
        }
      }
    }
    faults.gates.releaseAll();
    await Promise.race([Promise.allSettled(requests), sleep(10_000)]);
    if (Date.now() - t0 - tickMs > 0.9 * WINDOW_MS * 3) fail(`HARNESS_TIMING: the schedule took ${Date.now() - t0 - tickMs} ms outside its ticks`);

    // (a) the real trusted of every read equals the model's, for the schedule in which each B1 is adjacent to its B2 (see pruneAdjacent)
    const adjusted = runModel({ writers: sch.writers, hb: sch.hb, order: finish(pruneAdjacent(sch.writers, sch.order)) }, 'strong');
    const expected: Schedule['expected_strong'] = {};
    for (const [r, d] of Object.entries(adjusted.reads)) expected[r] = { trusted: d.trusted as boolean, value: d.val as [number, string] };
    for (const [r, e] of Object.entries(expected)) {
      const got = real_[r];
      if (!got) { fail(`read ${r} never finished`); continue; }
      // a schedule in which a heartbeat was held back longer than the window is the documented residual: counted, not asserted
      if (sch.compliant && got.trusted !== e.trusted) fail(`read ${r}: real trusted ${got.trusted}, model ${e.trusted}`);
    }
    // (b) a value names one content: across every trusted read of the run, equal values have equal labels; and values the model
    //     calls equal are equal
    const byValue = new Map<string, Set<string>>();
    for (const g of Object.values(real_)) if (g.trusted) byValue.set(g.value, (byValue.get(g.value) ?? new Set()).add(g.label));
    for (const [v, labels] of byValue) if (labels.size > 1) fail(`value ${v} names ${labels.size} different contents`);
    const names = Object.keys(expected).filter((r) => real_[r]?.trusted && expected[r]?.trusted);
    for (let i = 0; i < names.length; i++)
      for (let j = i + 1; j < names.length; j++) {
        const a = names[i] as string, b = names[j] as string;
        const sameModel = JSON.stringify(expected[a]?.value) === JSON.stringify(expected[b]?.value);
        if (sameModel && real_[a]?.value !== real_[b]?.value) fail(`reads ${a} and ${b}: the model says the same value, the stack does not`);
      }
    return { failures };
  }
});

