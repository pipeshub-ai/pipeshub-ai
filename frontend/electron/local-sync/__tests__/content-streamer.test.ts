import test from 'node:test';
import * as assert from 'node:assert';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { ContentStreamer, resolveInsideRoot } from '../transport/content-streamer';
import type { ContentFetchRequest, ContentStreamAck } from '../transport/content-streamer';

const CHUNK_BYTES = 4 * 1024;

interface Capture {
  chunks: Array<{ seq: number; data: Buffer; final: boolean }>;
  aborts: Array<{ code: string; message: string; retryable: boolean }>;
}

function setup(rootPath: string | null) {
  const streamer = new ContentStreamer({
    getRootPath: () => rootPath,
    log: () => { /* quiet */ },
  });
  const capture: Capture = { chunks: [], aborts: [] };
  const serve = (request: Partial<ContentFetchRequest>): Promise<ContentStreamAck> =>
    streamer.serve(
      {
        requestId: 'req-1',
        connectorId: 'c-1',
        relPath: 'a.txt',
        chunkBytes: CHUNK_BYTES,
        ...request,
      } as ContentFetchRequest,
      (seq, data, final) => capture.chunks.push({ seq, data, final }),
      (error) => capture.aborts.push(error),
    );
  return { serve, capture };
}

/** The ack resolves before streaming starts, so wait for the final frame. */
async function waitForCompletion(capture: Capture): Promise<void> {
  for (let i = 0; i < 200; i += 1) {
    if (capture.chunks.some((c) => c.final) || capture.aborts.length > 0) return;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error('transfer never completed');
}

test('a file larger than one frame round-trips byte-identically', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-content-'));
  const body = Buffer.alloc(CHUNK_BYTES * 2 + 137);
  for (let i = 0; i < body.length; i += 1) body[i] = i % 251;
  await fsp.writeFile(path.join(root, 'a.txt'), body);

  const { serve, capture } = setup(root);
  const ack = await serve({});
  assert.equal(ack.ok, true);
  assert.equal((ack as { size: number }).size, body.length);
  assert.equal((ack as { mimeType: string }).mimeType, 'text/plain');

  await waitForCompletion(capture);
  assert.equal(capture.chunks.length, 3, 'expected three frames');
  assert.deepEqual(capture.chunks.map((c) => c.seq), [0, 1, 2], 'frames must be sequenced');
  assert.equal(capture.chunks[2].final, true, 'only the last frame is final');
  assert.ok(
    Buffer.concat(capture.chunks.map((c) => c.data)).equals(body),
    'reassembled bytes must match the file exactly',
  );

  fs.rmSync(root, { recursive: true, force: true });
});

test('a path escaping the sync root is refused before any read', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-content-'));
  const secret = path.join(root, '..', `pipeshub-secret-${process.pid}.txt`);
  await fsp.writeFile(secret, 'do not leak me');

  const { serve, capture } = setup(root);
  const ack = await serve({ relPath: `../${path.basename(secret)}` });

  assert.equal(ack.ok, false);
  assert.equal((ack as { error: { code: string } }).error.code, 'PATH_OUTSIDE_ROOT');
  assert.equal(capture.chunks.length, 0, 'nothing may be streamed for a rejected path');

  fs.rmSync(secret, { force: true });
  fs.rmSync(root, { recursive: true, force: true });
});

test('an absolute relPath cannot reach outside the root either', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-content-'));
  const { serve } = setup(root);
  const ack = await serve({ relPath: path.resolve(os.tmpdir()) });
  assert.equal(ack.ok, false);
  assert.equal((ack as { error: { code: string } }).error.code, 'PATH_OUTSIDE_ROOT');
  fs.rmSync(root, { recursive: true, force: true });
});

test('an oversize file is refused cleanly instead of streamed', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-content-'));
  await fsp.writeFile(path.join(root, 'a.txt'), Buffer.alloc(2048));

  const { serve, capture } = setup(root);
  const ack = await serve({ maxBytes: 1024 });

  assert.equal(ack.ok, false);
  const error = (ack as { error: { code: string; retryable: boolean } }).error;
  assert.equal(error.code, 'CONTENT_TOO_LARGE');
  // Terminal, not transient: retrying three times before discovering the size
  // burns the indexing consumer's whole retry budget.
  assert.equal(error.retryable, false);
  assert.equal(capture.chunks.length, 0);

  fs.rmSync(root, { recursive: true, force: true });
});

test('a missing file is terminal so the indexing consumer stops retrying', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-content-'));
  const { serve } = setup(root);
  const ack = await serve({ relPath: 'gone.txt' });

  assert.equal(ack.ok, false);
  assert.equal((ack as { error: { code: string } }).error.code, 'FILE_UNREADABLE');
  assert.equal((ack as { error: { retryable: boolean } }).error.retryable, false);

  fs.rmSync(root, { recursive: true, force: true });
});

test('an unconfigured connector answers CONFIG_MISMATCH', async () => {
  const { serve } = setup(null);
  const ack = await serve({});
  assert.equal(ack.ok, false);
  assert.equal((ack as { error: { code: string } }).error.code, 'CONFIG_MISMATCH');
});

test('resolveInsideRoot accepts nested paths and rejects traversal', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeshub-content-'));
  fs.mkdirSync(path.join(root, 'nested'));
  fs.writeFileSync(path.join(root, 'nested', 'b.txt'), 'b');

  assert.ok(resolveInsideRoot(root, 'nested/b.txt'));
  assert.equal(resolveInsideRoot(root, '../../etc/passwd'), null);
  assert.equal(resolveInsideRoot(root, ''), null, 'the root itself is not a file target');

  fs.rmSync(root, { recursive: true, force: true });
});
