import test from 'node:test';
import * as assert from 'node:assert/strict';
import { findDeepLinkInArgv, parseOAuthDeepLink } from '../deep-link';

// The OS hands this parser whatever any local caller passes, so the rejection
// cases matter as much as the happy path.

test('extracts a GitHub code from the query string', () => {
  const link = parseOAuthDeepLink('pipeshub://auth/github/callback?code=abc123&state=phd.deadbeef');
  assert.deepEqual(link, {
    provider: 'github',
    params: { code: 'abc123', state: 'phd.deadbeef' },
  });
});

test('extracts a Google id_token', () => {
  const link = parseOAuthDeepLink('pipeshub://auth/google/callback?id_token=jwt.body.sig&state=phd.a1');
  assert.equal(link?.provider, 'google');
  assert.equal(link?.params.id_token, 'jwt.body.sig');
});

test('carries provider errors through', () => {
  const link = parseOAuthDeepLink(
    'pipeshub://auth/microsoft/callback?error=access_denied&error_description=User+cancelled&state=phd.b2',
  );
  assert.equal(link?.params.error, 'access_denied');
  assert.equal(link?.params.error_description, 'User cancelled');
});

test('still reads a fragment, which is what a provider would send directly', () => {
  const link = parseOAuthDeepLink('pipeshub://auth/google/callback#id_token=jwt&state=phd.c3');
  assert.equal(link?.params.id_token, 'jwt');
  assert.equal(link?.params.state, 'phd.c3');
});

test('percent-encoded values are decoded once', () => {
  const link = parseOAuthDeepLink('pipeshub://auth/github/callback?code=a%2Fb%2Bc&state=phd.d4');
  assert.equal(link?.params.code, 'a/b+c');
});

for (const rejected of [
  'https://auth/google/callback?code=x',
  'pipeshub://auth/twitter/callback?code=x',
  'pipeshub://auth/google?code=x',
  'pipeshub://auth/google/callback/extra?code=x',
  'pipeshub://other/google/callback?code=x',
  'pipeshub://auth/callback?code=x',
  'not a url at all',
  '',
]) {
  test(`rejects ${rejected || '(empty string)'}`, () => {
    assert.equal(parseOAuthDeepLink(rejected), null);
  });
}

test('rejects a non-string input', () => {
  assert.equal(parseOAuthDeepLink(undefined as unknown as string), null);
});

test('finds the link at any argv position', () => {
  // Position varies: the Linux launcher prepends --no-sandbox
  // (scripts/electron/after-pack.cjs) and in dev argv[1] is the project path.
  assert.equal(
    findDeepLinkInArgv(['/opt/PipesHub/pipeshub', '--no-sandbox', 'pipeshub://auth/google/callback?code=x']),
    'pipeshub://auth/google/callback?code=x',
  );
  assert.equal(
    findDeepLinkInArgv(['electron.exe', 'C:\\project', 'pipeshub://auth/github/callback?code=y']),
    'pipeshub://auth/github/callback?code=y',
  );
});

test('returns null when argv holds no link', () => {
  assert.equal(findDeepLinkInArgv(['/opt/PipesHub/pipeshub', '--no-sandbox']), null);
  assert.equal(findDeepLinkInArgv([]), null);
});
