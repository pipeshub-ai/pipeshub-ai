/**
 * SS3 WS-3: Node writes and reads workspace objects through the storage
 * service's internal routes the way the worker's PipesHubBinaryObjectStore
 * does. The stand-in server refuses what the real routes refuse: a missing or
 * wrongly scoped storage token, and bodies the routes' own validators reject.
 */
import { expect } from 'chai';
import express, { NextFunction, Request, Response } from 'express';
import multer from 'multer';
import jwt from 'jsonwebtoken';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { createHash } from 'node:crypto';
import {
  CreateDocumentSchema,
  DirectUploadSchema,
  UploadNewSchema,
} from '../../../src/modules/storage/validators/validators';
import {
  StorageWorkspaceObjects,
  WorkspaceObjectStorageError,
} from '../../../src/modules/agent-harness/adapters/storage/storage-workspace-objects';

const SECRET = 'scoped-secret';
// Storage's document id validator takes only Mongo ObjectIds (#3668).
const documentIdFor = (n: number): string => n.toString(16).padStart(24, '0');
const DOC_1 = documentIdFor(1);
const token = (orgId: string, scopes = ['storage:token']): string => jwt.sign({ orgId, scopes }, SECRET);

interface Stored {
  orgId: string;
  bytes: Buffer;
  fields: Record<string, unknown>;
}

function storageServer() {
  const documents = new Map<string, Stored>();
  const pending = new Map<string, Stored>();
  const app = express();
  let next = 0;
  const requests: string[] = [];
  // The storage controller sends raw bytes (main #4398); `legacyJson` serves the
  // JSON-serialized Buffer a Node from before that change sent.
  const mode = { legacyJson: false };
  const auth = (req: Request, res: Response, proceed: NextFunction): void => {
    try {
      const claims = jwt.verify(String(req.headers.authorization).replace('Bearer ', ''), SECRET) as { orgId: string; scopes: string[] };
      if (!claims.scopes.includes('storage:token')) throw new Error('scope');
      (req as Request & { orgId: string }).orgId = claims.orgId;
      proceed();
    } catch {
      res.status(401).json({ error: 'unauthorized' });
    }
  };
  const valid = (schema: { safeParse(v: unknown): { success: boolean } }, req: Request, extra: Record<string, unknown> = {}) =>
    schema.safeParse({ body: { ...req.body, ...extra }, query: req.query, params: req.params, headers: req.headers }).success;
  const orgOf = (req: Request): string => (req as Request & { orgId: string }).orgId;
  app.use((req, _res, proceed) => {
    requests.push(`${req.method} ${req.path}`);
    proceed();
  });
  app.post('/api/v1/document/internal/upload', auth, multer({ storage: multer.memoryStorage() }).single('file'), (req, res) => {
    if (!req.file || !valid(UploadNewSchema, req, { fileBuffer: req.file })) {
      res.status(400).json({ error: 'invalid' });
      return;
    }
    const id = documentIdFor((next += 1));
    documents.set(id, { orgId: orgOf(req), bytes: req.file.buffer, fields: { ...req.body, filename: req.file.originalname, mimetype: req.file.mimetype } });
    res.json({ _id: id, documentName: req.body.documentName });
  });
  app.post('/api/v1/document/internal/placeholder', auth, express.json(), (req, res) => {
    if (!valid(CreateDocumentSchema, req)) {
      res.status(400).json({ error: 'invalid' });
      return;
    }
    const id = documentIdFor((next += 1));
    pending.set(id, { orgId: orgOf(req), bytes: Buffer.alloc(0), fields: req.body as Record<string, unknown> });
    res.json({ _id: id });
  });
  app.post('/api/v1/document/internal/:documentId/directUpload', auth, express.json(), (req, res) => {
    if (!valid(DirectUploadSchema, req) || pending.get(req.params.documentId!)?.orgId !== orgOf(req)) {
      res.status(404).json({ error: 'not found' });
      return;
    }
    res.json({ signedUrl: `http://${req.headers.host}/signed/${req.params.documentId!}?sig=1` });
  });
  app.put('/signed/:documentId', express.raw({ type: () => true, limit: '10mb' }), (req, res) => {
    const doc = pending.get(req.params.documentId!);
    if (doc === undefined || req.headers.authorization !== undefined) {
      res.status(403).end();
      return;
    }
    documents.set(req.params.documentId!, { ...doc, bytes: req.body as Buffer });
    pending.delete(req.params.documentId!);
    res.status(200).end();
  });
  app.get('/api/v1/document/internal/:documentId/buffer', auth, (req, res) => {
    const doc = documents.get(req.params.documentId!);
    if (doc === undefined || doc.orgId !== orgOf(req)) {
      res.status(404).json({ error: 'not found' });
      return;
    }
    if (mode.legacyJson) {
      res.json(doc.bytes);
      return;
    }
    res.status(200).type('application/octet-stream').send(doc.bytes);
  });
  return { app, documents, requests, mode };
}

describe('SS3 storage workspace objects (WS-3)', () => {
  let server: Server;
  let world: ReturnType<typeof storageServer>;
  let baseUrl: string;

  beforeEach(async () => {
    world = storageServer();
    server = world.app.listen(0, '127.0.0.1');
    await new Promise((resolve) => server.once('listening', resolve));
    baseUrl = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterEach(async () => {
    await new Promise((resolve) => server.close(resolve));
  });

  const objects = (storageType: string, orgToken = (orgId: string) => token(orgId)) =>
    new StorageWorkspaceObjects(() => ({ baseUrl, storageType }), orgToken);

  const BYTES = Buffer.from([0x48, 0x57, 0x53, 0x31, 0, 1, 2, 255]);
  const DIGEST = createHash('sha256').update(BYTES).digest('hex');

  it('local: one multipart upload with the worker\'s fields, read back and checked', async () => {
    const store = objects('local');
    const ref = await store.put('org-1', BYTES);
    expect(ref).to.deep.equal({ objectId: DOC_1, contentHash: `sha256:${DIGEST}`, sizeBytes: BYTES.length });
    expect(world.requests).to.deep.equal(['POST /api/v1/document/internal/upload']);
    expect(world.documents.get(DOC_1)!.fields).to.deep.include({
      documentName: DIGEST,
      documentPath: `attachments/agent-harness-object-${DIGEST}`,
      isVersionedFile: 'false',
      extension: 'bin',
      recordId: `agent-harness-object-${DIGEST}`,
      filename: `${DIGEST}.bin`,
      mimetype: 'application/octet-stream',
    });
    expect((await store.get('org-1', ref)).equals(BYTES)).to.equal(true);
  });

  for (const storageType of ['s3', 'azureBlob']) {
    it(`${storageType}: placeholder, direct-upload URL, then the bytes to the signed URL without the storage token`, async () => {
      const store = objects(storageType);
      const ref = await store.put('org-1', BYTES);
      expect(world.requests).to.deep.equal([
        'POST /api/v1/document/internal/placeholder',
        `POST /api/v1/document/internal/${DOC_1}/directUpload`,
        `PUT /signed/${DOC_1}`,
      ]);
      expect((await store.get('org-1', ref)).equals(BYTES)).to.equal(true);
    });
  }

  it('reads the raw bytes the storage service sends, and the JSON Buffer an older one sent', async () => {
    const store = objects('local');
    const ref = await store.put('org-1', BYTES);
    expect((await store.get('org-1', ref)).equals(BYTES)).to.equal(true);
    world.mode.legacyJson = true;
    expect((await store.get('org-1', ref)).equals(BYTES)).to.equal(true);
  });

  it('a read checks size and hash; another org\'s object is not served; a wrong token is refused', async () => {
    const store = objects('local');
    const ref = await store.put('org-1', BYTES);
    const failure = async (work: Promise<unknown>) => work.then(() => expect.fail('expected a refusal'), (error: unknown) => error);
    expect(await failure(store.get('org-1', { ...ref, sizeBytes: 1 }))).to.be.instanceOf(WorkspaceObjectStorageError);
    expect(await failure(store.get('org-1', { ...ref, contentHash: 'sha256:00' }))).to.be.instanceOf(WorkspaceObjectStorageError);
    expect(((await failure(store.get('org-2', ref))) as { response?: { status?: number } }).response?.status).to.equal(404);
    const wrongScope = objects('local', (orgId) => token(orgId, ['conversation:erase']));
    expect(((await failure(wrongScope.put('org-1', BYTES))) as { response?: { status?: number } }).response?.status).to.equal(401);
  });
});
