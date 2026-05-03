const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { dispatchFileEventBatch } = require('../transport/file-event-dispatcher');

test('dispatchFileEventBatch uploads file bytes for content-backed events', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'local-sync-dispatch-'));
  await fs.mkdir(path.join(root, 'docs'));
  await fs.writeFile(path.join(root, 'docs', 'note.txt'), 'hello desktop');

  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ success: true }),
    };
  };

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
  assert.equal(calls[0].init.headers.Authorization, 'Bearer token-1');
  assert.ok(!('Content-Type' in calls[0].init.headers));

  const form = calls[0].init.body;
  assert.equal(typeof form.get, 'function');
  const manifest = JSON.parse(form.get('manifest'));
  assert.equal(manifest.events[0].contentField, 'file_0');
  assert.equal(manifest.events[0].sha256.length, 64);
  assert.equal(manifest.events[0].mimeType, 'text/plain');

  const blob = form.get('file_0');
  assert.equal(await blob.text(), 'hello desktop');
});

test('dispatchFileEventBatch uses upload endpoint for delete-only desktop batches', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ success: true }),
    };
  };

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
  const manifest = JSON.parse(calls[0].init.body.get('manifest'));
  assert.equal(manifest.events[0].type, 'DELETED');
  assert.equal(manifest.events[0].contentField, undefined);
});
