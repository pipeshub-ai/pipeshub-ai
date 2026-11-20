/**
 * The S3 and Azure Blob storage adapters against real storage servers.
 *
 * Unit tests stub the AWS and Azure SDKs, so they cannot tell whether a
 * stored URL can be turned back into the right object, whether signed links
 * really open, or whether a name with a space survives the round trip.
 * These tests run each adapter against a real server: MinIO (S3-compatible)
 * and Azurite (Microsoft's Azure Storage emulator).
 *
 * Run one backend per process (see .github/workflows/storage-backends.yml):
 *   STORAGE_IT_BACKEND=s3    STORAGE_IT_S3_ENDPOINT=http://localhost:9000
 *                            STORAGE_IT_S3_ACCESS_KEY=... STORAGE_IT_S3_SECRET_KEY=...
 *   STORAGE_IT_BACKEND=azure STORAGE_IT_AZURE_CONNECTION_STRING=...
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomBytes, randomUUID } from 'crypto';
import { CreateBucketCommand, S3Client } from '@aws-sdk/client-s3';
import mongoose from 'mongoose';
import AmazonS3Adapter from '../../src/modules/storage/providers/s3.provider';
import AzureBlobStorageAdapter from '../../src/modules/storage/providers/azure.provider';
import { StorageServiceInterface } from '../../src/modules/storage/services/storage.service';
import {
  Document,
  StorageInfo,
  StorageVendor,
} from '../../src/modules/storage/types/storage.service.types';
import { StorageError } from '../../src/libs/errors/storage.errors';

type Backend = 's3' | 'azure';
type UrlField = 's3' | 'azureBlob';

interface Target {
  backend: Backend;
  urlField: UrlField;
  adapter: StorageServiceInterface;
  /** Headers a plain HTTP PUT to a presigned upload URL needs on this backend. */
  directUploadHeaders: Record<string, string>;
}

const S3_REGION = 'us-east-1';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} must be set for the storage adapter tests`);
  }
  return value;
}

async function s3Target(bucket: string): Promise<Target> {
  // The adapter reads these instead of a global SDK config, so every client
  // in this process talks to MinIO rather than AWS.
  const endpoint = requireEnv('STORAGE_IT_S3_ENDPOINT');
  process.env.AWS_ENDPOINT_URL_S3 = endpoint;
  process.env.AWS_S3_FORCE_PATH_STYLE = 'true';
  const accessKeyId = requireEnv('STORAGE_IT_S3_ACCESS_KEY');
  const secretAccessKey = requireEnv('STORAGE_IT_S3_SECRET_KEY');
  const client = new S3Client({
    region: S3_REGION,
    endpoint,
    forcePathStyle: true,
    credentials: { accessKeyId, secretAccessKey },
  });
  await client.send(new CreateBucketCommand({ Bucket: bucket }));
  return {
    backend: 's3',
    urlField: 's3',
    adapter: new AmazonS3Adapter({
      accessKeyId,
      secretAccessKey,
      region: S3_REGION,
      bucket,
    }),
    directUploadHeaders: {},
  };
}

async function azureTarget(container: string): Promise<Target> {
  const adapter = new AzureBlobStorageAdapter({
    azureBlobConnectionString: requireEnv('STORAGE_IT_AZURE_CONNECTION_STRING'),
    containerName: container,
  });
  return {
    backend: 'azure',
    urlField: 'azureBlob',
    adapter,
    directUploadHeaders: { 'x-ms-blob-type': 'BlockBlob' },
  };
}

function documentFor(
  target: Target,
  url: string,
  extra: Partial<Document> = {},
): Document {
  const info: StorageInfo = { url };
  return {
    documentName: 'doc',
    isVersionedFile: false,
    orgId: new mongoose.Types.ObjectId(),
    initiatorUserId: null,
    extension: '.bin',
    currentVersion: 0,
    isDeleted: false,
    storageVendor:
      target.backend === 's3' ? StorageVendor.S3 : StorageVendor.AzureBlob,
    [target.urlField]: info,
    ...extra,
  };
}

async function fetchBytes(url: string): Promise<{ status: number; body: Buffer; headers: Headers }> {
  const res = await fetch(url);
  return {
    status: res.status,
    body: Buffer.from(await res.arrayBuffer()),
    headers: res.headers,
  };
}

describe('Storage adapters against a real storage server', function () {
  this.timeout(60000);

  const backend = requireEnv('STORAGE_IT_BACKEND') as Backend;
  const runId = randomUUID().slice(0, 8);
  let target: Target;

  // Where uploads land, the way the upload service lays out a stored file.
  const orgPrefix = `${new mongoose.Types.ObjectId().toString()}/PipesHub`;
  const pathFor = (name: string): string =>
    `${orgPrefix}/${runId}/${randomUUID().slice(0, 8)}/${name}`;

  async function upload(name: string, body: Buffer, mimeType = 'application/octet-stream'): Promise<string> {
    const res = await target.adapter.uploadDocumentToStorageService({
      documentPath: pathFor(name),
      buffer: body,
      mimeType,
      isVersioned: false,
    });
    expect(res.statusCode).to.equal(200);
    expect(res.data).to.be.a('string');
    return res.data as string;
  }

  async function download(doc: Document, version?: number): Promise<Buffer> {
    const res = await target.adapter.getBufferFromStorageService(doc, version);
    expect(res.statusCode).to.equal(200);
    return res.data as Buffer;
  }

  before(async () => {
    if (backend === 's3') {
      target = await s3Target(`pipeshub-it-${runId}`);
    } else if (backend === 'azure') {
      target = await azureTarget(`pipeshub-it-${runId}`);
    } else {
      throw new Error(`STORAGE_IT_BACKEND must be s3 or azure, got '${backend}'`);
    }
  });

  it('stores a file and reads back the exact bytes', async () => {
    const body = randomBytes(256 * 1024);
    const url = await upload('report.bin', body);
    expect(await download(documentFor(target, url))).to.deep.equal(body);
  });

  it('round-trips a file much larger than one request chunk', async () => {
    const body = randomBytes(12 * 1024 * 1024);
    const url = await upload('large.bin', body);
    const got = await download(documentFor(target, url));
    expect(got.length).to.equal(body.length);
    expect(got.equals(body)).to.equal(true);
  });

  for (const name of [
    'Quarterly report.pdf',
    'Rapport financier – été 2026.docx',
    '売上 レポート.xlsx',
    'notes (final) #2 + 50%.txt',
  ]) {
    it(`finds a file named "${name}" again after storing it`, async () => {
      const body = Buffer.from(`content of ${name}`, 'utf8');
      const url = await upload(name, body);
      expect((await download(documentFor(target, url))).toString('utf8')).to.equal(
        `content of ${name}`,
      );
    });
  }

  it('replaces the content in place when a file is updated', async () => {
    const url = await upload('edited draft.txt', Buffer.from('first draft'));
    const doc = documentFor(target, url, { mimeType: 'text/plain' });
    const res = await target.adapter.updateBuffer(Buffer.from('second draft'), doc);
    expect(res.statusCode).to.equal(200);
    expect((await download(doc)).toString()).to.equal('second draft');
  });

  it('reads an older version from the version history', async () => {
    const v0 = await upload('v0.txt', Buffer.from('version zero'));
    const v1 = await upload('v1.txt', Buffer.from('version one'));
    const doc = documentFor(target, v1, {
      isVersionedFile: true,
      currentVersion: 1,
      versionHistory: [
        { version: 0, [target.urlField]: { url: v0 } },
        { version: 1, [target.urlField]: { url: v1 } },
      ],
    });
    expect((await download(doc, 0)).toString()).to.equal('version zero');
    expect((await download(doc)).toString()).to.equal('version one');
  });

  it('gives a signed link that opens the file without credentials', async () => {
    const body = randomBytes(4096);
    const url = await upload('shared.bin', body);
    const res = await target.adapter.getSignedUrl(documentFor(target, url));
    expect(res.statusCode).to.equal(200);
    const fetched = await fetchBytes(res.data as string);
    expect(fetched.status).to.equal(200);
    expect(fetched.body.equals(body)).to.equal(true);
  });

  it('names the download after the file, including non-English names', async () => {
    const url = await upload('any.bin', Buffer.from('named download'));
    const res = await target.adapter.getSignedUrl(
      documentFor(target, url),
      undefined,
      'Résumé 2026',
    );
    const fetched = await fetchBytes(res.data as string);
    expect(fetched.status).to.equal(200);
    const disposition = fetched.headers.get('content-disposition') ?? '';
    expect(disposition).to.include('attachment');
    expect(disposition).to.include(`filename*=UTF-8''R%C3%A9sum%C3%A9%202026.bin`);
  });

  it('accepts a browser upload to a presigned link, then serves it', async () => {
    const documentPath = pathFor('direct.bin');
    const res = await target.adapter.generatePresignedUrlForDirectUpload!(documentPath);
    expect(res.statusCode).to.equal(200);
    const body = randomBytes(8192);
    const put = await fetch(res.data!.url, {
      method: 'PUT',
      body,
      headers: target.directUploadHeaders,
    });
    expect(put.status, await put.text()).to.be.oneOf([200, 201]);

    // Stored URL for the object, as the adapter itself would have produced it.
    const stored = await target.adapter.uploadDocumentToStorageService({
      documentPath: `${documentPath}.probe`,
      buffer: Buffer.from('x'),
      mimeType: 'application/octet-stream',
      isVersioned: false,
    });
    const directUrl = (stored.data as string).replace(/\.probe$/, '');
    expect((await download(documentFor(target, directUrl))).equals(body)).to.equal(true);
  });

  it('tells a direct upload that never arrived from one that did', async () => {
    const documentPath = pathFor('maybe.bin');
    const res = await target.adapter.generatePresignedUrlForDirectUpload!(documentPath);
    const stored = await target.adapter.uploadDocumentToStorageService({
      documentPath: `${documentPath}.probe`,
      buffer: Buffer.from('x'),
      mimeType: 'application/octet-stream',
      isVersioned: false,
    });
    const doc = documentFor(target, (stored.data as string).replace(/\.probe$/, ''));

    // The placeholder records this URL before the browser sends anything.
    expect(await target.adapter.objectExists!(doc)).to.equal(false);

    const put = await fetch(res.data!.url, {
      method: 'PUT',
      body: randomBytes(1024),
      headers: target.directUploadHeaders,
    });
    expect(put.status, await put.text()).to.be.oneOf([200, 201]);
    expect(await target.adapter.objectExists!(doc)).to.equal(true);
  });

  it('uploads straight away to a container that does not exist yet', async function () {
    if (backend !== 'azure') {
      this.skip();
    }
    // A fresh adapter creates its container at start-up; the first upload must wait for it.
    const fresh = await azureTarget(`pipeshub-it-fresh-${randomUUID().slice(0, 8)}`);
    const body = Buffer.from('first upload');
    const res = await fresh.adapter.uploadDocumentToStorageService({
      documentPath: pathFor('first.txt'),
      buffer: body,
      mimeType: 'text/plain',
      isVersioned: false,
    });
    expect(res.statusCode).to.equal(200);
    const got = await fresh.adapter.getBufferFromStorageService(documentFor(fresh, res.data as string));
    expect((got.data as Buffer).equals(body)).to.equal(true);
  });

  it('reports a missing file as an error, not as empty content', async () => {
    const url = await upload('gone.txt', Buffer.from('soon gone'));
    const missing = url.replace(/gone\.txt$/, 'never-stored.txt');
    let caught: unknown;
    try {
      await target.adapter.getBufferFromStorageService(documentFor(target, missing));
    } catch (error) {
      caught = error;
    }
    expect(caught).to.be.instanceOf(StorageError);
  });

  it('assembles a multipart upload from parts sent to presigned links', async function () {
    if (backend !== 's3') {
      // Azure has no multipart API here; the adapter says so explicitly.
      let caught: unknown;
      try {
        await target.adapter.getMultipartUploadId!('x', 'application/octet-stream');
      } catch (error) {
        caught = error;
      }
      expect(caught).to.be.instanceOf(StorageError);
      return;
    }
    const documentPath = pathFor('multipart.bin');
    const started = await target.adapter.getMultipartUploadId!(
      documentPath,
      'application/octet-stream',
    );
    const uploadId = started.data!.uploadId;
    // S3 requires every part except the last to be at least 5 MB.
    const partBodies = [randomBytes(5 * 1024 * 1024), randomBytes(1024 * 1024)];
    const parts: Array<{ ETag: string; PartNumber: number }> = [];
    for (const [index, partBody] of partBodies.entries()) {
      const partNumber = index + 1;
      const signed = await target.adapter.generatePresignedUrlForPart!(
        documentPath,
        partNumber,
        uploadId,
      );
      const put = await fetch(signed.data!.url, { method: 'PUT', body: partBody });
      expect(put.status, await put.text()).to.equal(200);
      parts.push({ ETag: put.headers.get('etag') as string, PartNumber: partNumber });
    }
    const done = await target.adapter.completeMultipartUpload!(documentPath, uploadId, parts);
    expect(done.statusCode).to.equal(200);
    const got = await download(documentFor(target, done.data!.url));
    expect(got.equals(Buffer.concat(partBodies))).to.equal(true);
  });

  describe('bound range reads (what the record preview issues)', () => {
    async function signed(body: Buffer, name = 'ranged.bin'): Promise<{ doc: Document; url: string; etag: string }> {
      const stored = await upload(name, body);
      const doc = documentFor(target, stored);
      const res = await target.adapter.getSignedUrl(doc);
      expect(res.statusCode).to.equal(200);
      const url = res.data as string;
      const whole = await fetch(url, { headers: { Range: 'bytes=0-0' } });
      const etag = whole.headers.get('etag');
      expect(etag, 'the object has an ETag').to.be.a('string');
      await whole.arrayBuffer();
      return { doc, url, etag: etag as string };
    }

    it('answers one closed range with exactly those bytes, as 206 with the length', async () => {
      const body = randomBytes(300 * 1024);
      const { url, etag } = await signed(body);
      const res = await fetch(url, { headers: { Range: 'bytes=1000-70999', 'If-Match': etag } });
      expect(res.status).to.equal(206);
      const got = Buffer.from(await res.arrayBuffer());
      expect(got.equals(body.subarray(1000, 71000))).to.equal(true);
      expect(res.headers.get('content-range')).to.equal(`bytes 1000-70999/${body.length}`);
    });

    it('honours an open-ended range', async () => {
      const body = randomBytes(64 * 1024);
      const { url, etag } = await signed(body);
      const res = await fetch(url, { headers: { Range: `bytes=${body.length - 100}-`, 'If-Match': etag } });
      expect(res.status).to.equal(206);
      expect(Buffer.from(await res.arrayBuffer()).equals(body.subarray(body.length - 100))).to.equal(true);
    });

    it('answers a range past the end with 416 (the length comes from the verified object size, not from this answer)', async () => {
      const body = randomBytes(8 * 1024);
      const { url } = await signed(body);
      const res = await fetch(url, { headers: { Range: 'bytes=999999999-' } });
      expect(res.status).to.equal(416);
      await res.arrayBuffer();
    });

    it('refuses a read bound to an object that was overwritten (412), even for a range', async () => {
      const body = randomBytes(32 * 1024);
      const { doc, url, etag } = await signed(body, 'overwritten.bin');
      const res = await target.adapter.updateBuffer(randomBytes(32 * 1024), doc);
      expect(res.statusCode).to.equal(200);
      const fresh = await target.adapter.getSignedUrl(doc);
      const stale = await fetch(fresh.data as string, { headers: { Range: 'bytes=0-99', 'If-Match': etag } });
      expect(stale.status).to.equal(412);
      await stale.arrayBuffer();
      // the old link is not a way around the binding either
      const old = await fetch(url, { headers: { Range: 'bytes=0-99', 'If-Match': etag } });
      expect([200, 206, 403, 404, 412]).to.include(old.status);
      if (old.status !== 412) {
        const now = await fetch(fresh.data as string, { headers: { Range: 'bytes=0-0' } });
        await now.arrayBuffer();
        expect(now.headers.get('etag')).to.not.equal(etag);
      }
    });

    it('streams a whole object bound to its tag without holding it in memory', async () => {
      const body = randomBytes(24 * 1024 * 1024);
      const { url, etag } = await signed(body, 'stream.bin');
      const res = await fetch(url, { headers: { 'If-Match': etag } });
      expect(res.status).to.equal(200);
      let received = 0;
      const reader = (res.body as ReadableStream<Uint8Array>).getReader();
      let peak = process.memoryUsage().rss;
      const start = peak;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        received += value.length;
        peak = Math.max(peak, process.memoryUsage().rss);
      }
      expect(received).to.equal(body.length);
      expect(peak - start).to.be.lessThan(4 * body.length);
    });
  });

  describe('artifact sets', () => {
    // One content revision's files, as the presign route lays them out.
    const revisionRoot = (rev: string): string => `${orgPrefix}/record-revisions/${runId}-vr/${rev}`
    const files: Array<{ path: string; contentType: string; body: Buffer }> = [
      { path: 'manifest.json', contentType: 'application/json', body: Buffer.from('{"rev":"r"}') },
      { path: 'blocks/part-0000.jsonl', contentType: 'application/x-ndjson', body: Buffer.from('{"i":0}\n{"i":1}\n') },
      { path: `images/${'d'.repeat(64)}.png`, contentType: 'image/png', body: randomBytes(70 * 1024) },
      { path: 'enrich/summary@1.json', contentType: 'application/json', body: Buffer.from('{}') },
    ]

    async function putRevision(root: string): Promise<void> {
      for (const f of files) {
        const signed = await target.adapter.signObjectUrl!(`${root}/${f.path}`, {
          method: 'PUT',
          contentType: f.contentType,
          expiresInSeconds: 900,
        })
        const put = await fetch(signed.url, { method: 'PUT', body: f.body, headers: signed.headers })
        expect(put.status, `${f.path}: ${await put.text()}`).to.be.oneOf([200, 201])
      }
    }

    async function readUrl(root: string, filePath: string): Promise<{ url: string; headers: Record<string, string> }> {
      return target.adapter.signObjectUrl!(`${root}/${filePath}`, { method: 'GET', expiresInSeconds: 900 })
    }

    it('accepts uploads to signed PUT URLs sent with their headers, and serves them back through signed GET URLs', async () => {
      const root = revisionRoot('0123456789abcdef')
      await putRevision(root)

      for (const f of files) {
        const signed = await readUrl(root, f.path)
        const res = await fetch(signed.url, { headers: signed.headers })
        expect(res.status, f.path).to.equal(200)
        expect(res.headers.get('content-type'), f.path).to.equal(f.contentType)
        expect(Buffer.from(await res.arrayBuffer()).equals(f.body), f.path).to.equal(true)
      }
    })

    it('serves a byte range through a signed GET URL', async () => {
      const root = revisionRoot('1111111111111111')
      await putRevision(root)
      const image = files[2]!

      const signed = await readUrl(root, image.path)
      const res = await fetch(signed.url, { headers: { ...signed.headers, range: 'bytes=100-199' } })

      expect(res.status).to.equal(206)
      expect(Buffer.from(await res.arrayBuffer()).equals(image.body.subarray(100, 200))).to.equal(true)
    })

    it('removes every file of a revision with deleteTree, and only that revision', async () => {
      const gone = revisionRoot('2222222222222222')
      const kept = revisionRoot('22222222222222220000')
      await putRevision(gone)
      await putRevision(kept)

      await target.adapter.deleteTree!(gone)

      for (const f of files) {
        expect(await target.adapter.objectExistsAtPath!(`${gone}/${f.path}`), `${gone}/${f.path}`).to.equal(false)
        expect(await target.adapter.objectExistsAtPath!(`${kept}/${f.path}`), `${kept}/${f.path}`).to.equal(true)
      }
      const signed = await readUrl(gone, 'manifest.json')
      expect((await fetch(signed.url)).status).to.equal(404)
    })

    it('refuses a signed PUT URL once it has expired', async () => {
      const signed = await target.adapter.signObjectUrl!(`${revisionRoot('3333333333333333')}/record.json`, {
        method: 'PUT',
        contentType: 'application/json',
        expiresInSeconds: 1,
      })
      await new Promise((resolve) => setTimeout(resolve, 2500))

      const put = await fetch(signed.url, { method: 'PUT', body: Buffer.from('{}'), headers: signed.headers })

      expect(put.status).to.equal(403)
    })
  });

  describe('paid-result cache objects', () => {
    const org = new mongoose.Types.ObjectId().toString();
    const recordRoot = (record: string): string => `${org}/PipesHub/paid-results/${record}`;
    const key = (record: string, n: number): string =>
      `${recordRoot(record)}/ocr_page/${n.toString(16).padStart(64, '0')}.json`;

    async function signedPut(storageKey: string, body: string, ifAbsent: boolean): Promise<number> {
      const signed = await target.adapter.signObjectUrl!(storageKey, {
        method: 'PUT',
        contentType: 'application/json',
        expiresInSeconds: 60,
        ifAbsent,
      });
      const res = await fetch(signed.url, { method: 'PUT', headers: signed.headers, body });
      await res.arrayBuffer();
      return res.status;
    }

    async function signedGet(storageKey: string): Promise<{ status: number; text: string }> {
      const signed = await target.adapter.signObjectUrl!(storageKey, { method: 'GET', expiresInSeconds: 60 });
      const res = await fetch(signed.url, { headers: signed.headers });
      return { status: res.status, text: await res.text() };
    }

    it('a signed PUT with the condition keeps the first writer, and one without it replaces', async () => {
      const k = key('cond', 1);
      expect(await signedPut(k, '{"first":true}', true)).to.be.oneOf([200, 201]);
      expect(await signedPut(k, '{"second":true}', true)).to.be.oneOf([409, 412]);
      expect(await signedGet(k)).to.deep.equal({ status: 200, text: '{"first":true}' });

      expect(await signedPut(k, '{"replaced":true}', false)).to.be.oneOf([200, 201]);
      expect((await signedGet(k)).text).to.equal('{"replaced":true}');
    });

    it('concurrent conditional writers of one key leave exactly one winner', async () => {
      const k = key('race', 1);
      const codes = await Promise.all(Array.from({ length: 6 }, (_, i) => signedPut(k, `{"writer":${i}}`, true)));
      expect(codes.filter((c) => c === 200 || c === 201)).to.have.length(1);
      expect(codes.filter((c) => c === 409 || c === 412)).to.have.length(5);
      expect((await signedGet(k)).text).to.match(/^\{"writer":\d\}$/);
    });

    it('lists one record in key order, page by page, with sizes and write times', async () => {
      for (const n of [3, 1, 5, 2, 4]) await signedPut(key('list', n), 'x'.repeat(n), true);
      await signedPut(key('list-longer', 1), 'other record', true);

      const seen: string[] = [];
      let after: string | undefined;
      let pages = 0;
      do {
        const page = await target.adapter.listObjects!(`${recordRoot('list')}/`, { limit: 2, after });
        pages += 1;
        for (const o of page.objects) {
          seen.push(o.key);
          expect(o.size).to.equal(parseInt(o.key.slice(-69, -5), 16));
          expect(o.lastModifiedMs).to.be.greaterThan(Date.now() - 5 * 60 * 1000);
        }
        after = page.next;
      } while (after !== undefined);

      expect(seen).to.deep.equal([1, 2, 3, 4, 5].map((n) => key('list', n)));
      expect(pages).to.equal(3);
    });

    it('lists nothing under a prefix with no objects', async () => {
      expect(await target.adapter.listObjects!(`${recordRoot('nothing-here')}/`, { limit: 10 })).to.deep.equal({
        objects: [],
      });
    });

    it('deletes the named objects, counts a missing one as done, and leaves the rest', async () => {
      for (const n of [1, 2, 3]) await signedPut(key('batch', n), 'x', true);
      await target.adapter.deleteObjects!([key('batch', 1), key('batch', 3), key('batch', 99)]);

      const left = await target.adapter.listObjects!(`${recordRoot('batch')}/`, { limit: 10 });
      expect(left.objects.map((o) => o.key)).to.deep.equal([key('batch', 2)]);
    });

    it('a record purge removes that record only, and an org purge removes the org only', async () => {
      const otherOrg = new mongoose.Types.ObjectId().toString();
      const otherOrgKey = `${otherOrg}/PipesHub/paid-results/purge-a/ocr_page/${'0'.repeat(63)}1.json`;
      await signedPut(key('purge-a', 1), 'a', true);
      await signedPut(key('purge-a', 2), 'a', true);
      await signedPut(key('purge-b', 1), 'b', true);
      await signedPut(otherOrgKey, 'other org', true);

      await target.adapter.deleteTree!(recordRoot('purge-a'));
      await target.adapter.deleteTree!(recordRoot('purge-a')); // a repeat is not an error
      expect((await target.adapter.listObjects!(`${recordRoot('purge-a')}/`, { limit: 10 })).objects).to.deep.equal([]);
      expect((await signedGet(key('purge-b', 1))).status).to.equal(200);
      expect((await signedGet(otherOrgKey)).status).to.equal(200);

      await target.adapter.deleteTree!(`${org}/PipesHub/paid-results`);
      expect((await signedGet(key('purge-b', 1))).status).to.equal(404);
      expect((await signedGet(otherOrgKey)).status).to.equal(200);
    });
  });
});

