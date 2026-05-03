import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import test from 'node:test';
import { dispatchFileEventBatch } from '../transport/file-event-dispatcher';

interface CapturedFetchCall {
  url: string;
  init: RequestInit;
}

test('dispatchFileEventBatch uploads file bytes for content-backed events', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'local-sync-dispatch-'));
  await fs.mkdir(path.join(root, 'docs'));
  await fs.writeFile(path.join(root, 'docs', 'note.txt'), 'hello desktop');

  const originalFetch = global.fetch;
  const calls: CapturedFetchCall[] = [];
  global.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ success: true }),
    } as Response;
  }) as typeof fetch;

  try {
    await dispatchFileEventBatch({
      apiBaseUrl: 'https://api.example.test/',
      accessToken: 'token-1',
      connectorId: 'connector-1',
      batchId: 'batch-1',
      timestamp: 123,
      rootPath: root,
      events: [
        {
          type: 'CREATED',
          path: 'docs/note.txt',
          timestamp: 123,
          size: 13,
          isDirectory: false,
        },
      ],
    });
  } finally {
    global.fetch = originalFetch;
    await fs.rm(root, { recursive: true, force: true });
  }

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.example.test/api/v1/connectors/connector-1/file-events/upload');
  const headers = calls[0].init.headers as Record<string, string>;
  assert.equal(headers.Authorization, 'Bearer token-1');
  assert.ok(!('Content-Type' in headers));

  const form = calls[0].init.body as FormData;
  assert.equal(typeof form.get, 'function');
  const manifest = JSON.parse(form.get('manifest') as string);
  assert.equal(manifest.events[0].contentField, 'file_0');
  assert.equal(manifest.events[0].sha256.length, 64);
  assert.equal(manifest.events[0].mimeType, 'text/plain');

  const blob = form.get('file_0') as Blob;
  assert.equal(await blob.text(), 'hello desktop');
});

test('dispatchFileEventBatch uses upload endpoint for delete-only desktop batches', async () => {
  const originalFetch = global.fetch;
  const calls: CapturedFetchCall[] = [];
  global.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ success: true }),
    } as Response;
  }) as typeof fetch;

  try {
    await dispatchFileEventBatch({
      apiBaseUrl: 'https://api.example.test/',
      accessToken: 'token-1',
      connectorId: 'connector-1',
      batchId: 'batch-del',
      timestamp: 456,
      rootPath: '/Users/me/Desktop',
      events: [
        {
          type: 'DELETED',
          path: 'docs/old.txt',
          timestamp: 456,
          isDirectory: false,
        },
      ],
    });
  } finally {
    global.fetch = originalFetch;
  }

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://api.example.test/api/v1/connectors/connector-1/file-events/upload');
  const body = calls[0].init.body as FormData;
  const manifest = JSON.parse(body.get('manifest') as string);
  assert.equal(manifest.events[0].type, 'DELETED');
  assert.equal(manifest.events[0].contentField, undefined);
});
