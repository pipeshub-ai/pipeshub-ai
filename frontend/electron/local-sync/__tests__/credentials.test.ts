import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { DesktopCredentialsStore } from '../persistence/credentials';

const CREDENTIALS_FILE = 'desktop-credentials.json';
const API_BASE_URL = 'http://localhost:3000';

async function withTempDir(run: (dir: string) => Promise<void>): Promise<void> {
  const dir = await fsp.mkdtemp(path.join(os.tmpdir(), 'desktop-credentials-'));
  try {
    await run(dir);
  } finally {
    await fsp.rm(dir, { recursive: true, force: true });
  }
}

function readStoredFile(dir: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(dir, CREDENTIALS_FILE), 'utf8'));
}

/** A JWT-shaped token whose `exp` is `offsetSeconds` from now. Signature is never checked. */
function tokenExpiringIn(offsetSeconds: number, marker = 'tok'): string {
  const exp = Math.floor(Date.now() / 1000) + offsetSeconds;
  const payload = Buffer.from(JSON.stringify({ exp, marker })).toString('base64url');
  return `header.${payload}.signature`;
}

test('a file left by a pre-release build keeps its deviceId and loses the stored token', async () => {
  await withTempDir(async (dir) => {
    const deviceId = '8f1d6a2e-0000-4000-8000-abcdefabcdef';
    fs.writeFileSync(
      path.join(dir, CREDENTIALS_FILE),
      JSON.stringify({
        version: 1,
        deviceId,
        apiBaseUrl: API_BASE_URL,
        refreshTokenEnc: Buffer.from('a-refresh-token').toString('base64'),
        updatedAt: Date.now(),
      }),
      'utf8',
    );

    const store = new DesktopCredentialsStore(dir);

    // The sync point on the server is pinned to this id; a fresh one would
    // force a full re-walk and a manual sync-point reset.
    assert.equal(store.deviceId, deviceId);
    assert.equal(store.hasCredential(), false);
    assert.equal(store.getAccessToken(), null);

    const onDisk = readStoredFile(dir);
    assert.deepEqual(Object.keys(onDisk).sort(), ['deviceId', 'updatedAt', 'version']);
  });
});

test('an identity-only file is left alone across reopens', async () => {
  await withTempDir(async (dir) => {
    const first = new DesktopCredentialsStore(dir);
    const written = readStoredFile(dir);

    assert.equal(new DesktopCredentialsStore(dir).deviceId, first.deviceId);
    assert.deepEqual(readStoredFile(dir), written);
  });
});

test('no token material is ever written to disk', async () => {
  await withTempDir(async (dir) => {
    const store = new DesktopCredentialsStore(dir);
    const accessToken = tokenExpiringIn(600, 'secret-marker');
    store.setAccessToken({ accessToken, apiBaseUrl: API_BASE_URL });

    const raw = fs.readFileSync(path.join(dir, CREDENTIALS_FILE), 'utf8');
    assert.equal(raw.includes(accessToken), false);
    assert.equal(raw.includes('secret-marker'), false);
    assert.deepEqual(Object.keys(JSON.parse(raw)).sort(), ['deviceId', 'updatedAt', 'version']);
  });
});

test('the pushed token round-trips and reports whether it changed', async () => {
  await withTempDir(async (dir) => {
    const store = new DesktopCredentialsStore(dir);
    const accessToken = tokenExpiringIn(600);

    const first = store.setAccessToken({ accessToken, apiBaseUrl: API_BASE_URL });
    assert.equal(first.changed, true);
    assert.equal(first.deviceId, store.deviceId);
    assert.equal(store.getAccessToken(), accessToken);
    assert.equal(store.apiBaseUrl, API_BASE_URL);
    assert.equal(store.hasCredential(), true);

    // The renderer pushes on every auth-store change, not only on refresh —
    // re-pushing the same token must not look like a new credential.
    const second = store.setAccessToken({ accessToken, apiBaseUrl: API_BASE_URL });
    assert.equal(second.changed, false);

    const third = store.setAccessToken({ accessToken: tokenExpiringIn(600, 'other'), apiBaseUrl: API_BASE_URL });
    assert.equal(third.changed, true);
  });
});

test('an expired token is not presented to the handshake', async () => {
  await withTempDir(async (dir) => {
    const store = new DesktopCredentialsStore(dir);
    // Inside the skew window, so already unusable even though `exp` is ahead.
    store.setAccessToken({ accessToken: tokenExpiringIn(5), apiBaseUrl: API_BASE_URL });

    assert.equal(store.getAccessToken(), null);
    assert.equal(store.hasCredential(), false);
  });
});

test('a non-http(s) apiBaseUrl is rejected', async () => {
  await withTempDir(async (dir) => {
    const store = new DesktopCredentialsStore(dir);
    assert.throws(
      () => store.setAccessToken({ accessToken: tokenExpiringIn(600), apiBaseUrl: 'file:///etc/passwd' }),
      /apiBaseUrl must be an http\(s\) URL/,
    );
  });
});

test('clear drops the token but the deviceId survives a reopen', async () => {
  await withTempDir(async (dir) => {
    const store = new DesktopCredentialsStore(dir);
    const { deviceId } = store.setAccessToken({
      accessToken: tokenExpiringIn(600),
      apiBaseUrl: API_BASE_URL,
    });

    store.clear();
    assert.equal(store.getAccessToken(), null);
    assert.equal(store.apiBaseUrl, null);
    assert.equal(store.hasCredential(), false);
    assert.equal(store.deviceId, deviceId);

    assert.equal(new DesktopCredentialsStore(dir).deviceId, deviceId);
  });
});
