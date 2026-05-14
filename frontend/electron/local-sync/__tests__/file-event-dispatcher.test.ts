import * as assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import test from 'node:test';
import { dispatchFileEventBatch, HttpStatusCode } from '../transport/file-event-dispatcher';

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
  } finally {
    global.fetch = originalFetch;
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('dispatchFileEventBatch refreshes the access token on 401 and retries', async () => {
  // Simulates the manager's refresh path: backend rejects with 401 once, the
  // refreshAccessToken callback hands back a new token, the second attempt
  // succeeds. Without the refresh wiring, the dispatcher would have retried
  // with the same stale token and burned the network retry budget.
  const originalFetch = global.fetch;
  const calls: { url: string; auth: string | undefined }[] = [];
  let attempt = 0;
  global.fetch = (async (url: string, init: RequestInit) => {
    const headers = init.headers as Record<string, string>;
    calls.push({ url, auth: headers.Authorization });
    attempt += 1;
    if (attempt === 1) {
      return {
        ok: false,
        status: 401,
        headers: new Headers(),
        json: async () => ({ error: 'expired' }),
      } as Response;
    }
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ success: true }),
    } as Response;
  }) as typeof fetch;

  let refreshCalls = 0;
  const refreshAccessToken = async () => {
    refreshCalls += 1;
    return 'fresh-token';
  };

  try {
    await dispatchFileEventBatch({
      apiBaseUrl: 'https://api.example.test/',
      accessToken: 'stale-token',
      connectorId: 'connector-1',
      batchId: 'batch-401',
      timestamp: 0,
      events: [{ type: 'DELETED', path: 'gone.txt', timestamp: 0, isDirectory: false }],
      rootPath: '/tmp/x',
      refreshAccessToken,
    });
  } finally {
    global.fetch = originalFetch;
  }

  assert.equal(calls.length, 2, `expected 2 fetch calls (401 then 200), got ${calls.length}`);
  assert.equal(calls[0].auth, 'Bearer stale-token');
  assert.equal(calls[1].auth, 'Bearer fresh-token');
  assert.equal(refreshCalls, 1, 'refreshAccessToken should be called exactly once');
});

test('dispatchFileEventBatch retries transient rate-limit responses', async () => {
  const originalFetch = global.fetch;
  const calls: number[] = [];
  global.fetch = (async () => {
    calls.push(calls.length + 1);
    if (calls.length === 1) {
      return {
        ok: false,
        status: HttpStatusCode.TooManyRequests,
        headers: new Headers({ 'retry-after': '0' }),
        json: async () => ({ error: 'rate_limited' }),
      } as Response;
    }
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
      batchId: 'batch-429',
      timestamp: 0,
      events: [{ type: 'DELETED', path: 'rate-limited.txt', timestamp: 0, isDirectory: false }],
    });
  } finally {
    global.fetch = originalFetch;
  }

  assert.equal(calls.length, 2, `expected one retry after 429, got ${calls.length} call(s)`);
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
