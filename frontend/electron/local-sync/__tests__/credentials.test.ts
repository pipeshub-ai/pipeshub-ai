import test from 'node:test';
import * as assert from 'node:assert/strict';
import { DesktopCredentialsStore } from '../persistence/credentials';

const API_BASE_URL = 'http://localhost:3000';
const IDENTITY = { deviceId: 'a'.repeat(64), deviceName: 'test-host' };

/** A JWT-shaped token whose `exp` is `offsetSeconds` from now. Signature is never checked. */
function tokenExpiringIn(offsetSeconds: number, marker = 'tok'): string {
  const exp = Math.floor(Date.now() / 1000) + offsetSeconds;
  const payload = Buffer.from(JSON.stringify({ exp, marker })).toString('base64url');
  return `header.${payload}.signature`;
}

test('the resolved identity is exposed as given', () => {
  const store = new DesktopCredentialsStore(IDENTITY);
  assert.equal(store.deviceId, IDENTITY.deviceId);
  assert.equal(store.deviceName, IDENTITY.deviceName);
  assert.equal(store.hasCredential(), false);
  assert.equal(store.getAccessToken(), null);
});

test('the pushed token round-trips and reports whether it changed', () => {
  const store = new DesktopCredentialsStore(IDENTITY);
  const accessToken = tokenExpiringIn(600);

  const first = store.setAccessToken({ accessToken, apiBaseUrl: API_BASE_URL });
  assert.equal(first.changed, true);
  assert.equal(first.deviceId, IDENTITY.deviceId);
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

test('an expired token is not presented to the handshake', () => {
  const store = new DesktopCredentialsStore(IDENTITY);
  // Inside the skew window, so already unusable even though `exp` is ahead.
  store.setAccessToken({ accessToken: tokenExpiringIn(5), apiBaseUrl: API_BASE_URL });

  assert.equal(store.getAccessToken(), null);
  assert.equal(store.hasCredential(), false);
});

test('a non-http(s) apiBaseUrl is rejected', () => {
  const store = new DesktopCredentialsStore(IDENTITY);
  assert.throws(
    () => store.setAccessToken({ accessToken: tokenExpiringIn(600), apiBaseUrl: 'file:///etc/passwd' }),
    /apiBaseUrl must be an http\(s\) URL/,
  );
});

test('clear drops the token but keeps the identity', () => {
  const store = new DesktopCredentialsStore(IDENTITY);
  store.setAccessToken({ accessToken: tokenExpiringIn(600), apiBaseUrl: API_BASE_URL });

  store.clear();
  assert.equal(store.getAccessToken(), null);
  assert.equal(store.apiBaseUrl, null);
  assert.equal(store.hasCredential(), false);
  assert.equal(store.deviceId, IDENTITY.deviceId);
  assert.equal(store.deviceName, IDENTITY.deviceName);
});
