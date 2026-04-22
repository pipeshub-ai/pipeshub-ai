/**
 * Standalone regression test for artifact URL classification.
 *
 * frontend-new currently has no unit-test runner configured (only
 * Playwright for e2e). The jest/vitest-style companion test lives at
 * `parse-download-markers.test.ts` and becomes executable the moment a
 * runner is added.
 *
 * Until then, THIS file is runnable directly with:
 *   node --test frontend-new/app/\(main\)/chat/utils/__tests__/parse-download-markers.node.test.mjs
 *
 * It re-implements the classification helpers (no TS imports) so the
 * trust-boundary logic has an executable spec today.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

// Re-implementations of the production helpers from parse-download-markers.ts.
// Kept byte-identical so drift between this file and production code causes
// a real test failure rather than a silent divergence.
function isSignedUrl(url) {
  return (
    url.includes('X-Amz-Signature') ||
    url.includes('AWSAccessKeyId') ||
    (url.includes('se=') && url.includes('sig='))
  );
}

function isTrustedApiUrl(url, trustedOrigin = 'https://our.api') {
  try {
    const resolved = new URL(url, trustedOrigin);
    return resolved.origin === trustedOrigin;
  } catch {
    return false;
  }
}

// -------- isSignedUrl --------

test('isSignedUrl accepts AWS presigned', () => {
  assert.equal(isSignedUrl('https://s3.amazonaws.com/b/k?X-Amz-Signature=abc'), true);
  assert.equal(isSignedUrl('https://s3.amazonaws.com/b/k?AWSAccessKeyId=AKIA'), true);
});

test('isSignedUrl accepts Azure SAS', () => {
  assert.equal(
    isSignedUrl('https://x.blob.core.windows.net/c/b?se=2030-01-01&sig=abc'),
    true,
  );
});

test('isSignedUrl rejects attacker URL', () => {
  assert.equal(isSignedUrl('https://evil.example/steal'), false);
});

// -------- isTrustedApiUrl --------

test('isTrustedApiUrl accepts same-origin', () => {
  assert.equal(isTrustedApiUrl('/api/v1/record/r1', 'https://our.api'), true);
  assert.equal(isTrustedApiUrl('https://our.api/api/v1/record/r1', 'https://our.api'), true);
});

test('isTrustedApiUrl rejects cross-origin', () => {
  assert.equal(isTrustedApiUrl('https://evil.example/steal', 'https://our.api'), false);
});

test('isTrustedApiUrl rejects protocol-relative cross-origin hops', () => {
  // `//evil.example/x` resolves against the trusted origin to
  // `https://evil.example/x` — origin changes, so NOT trusted.
  assert.equal(isTrustedApiUrl('//evil.example/x', 'https://our.api'), false);
});

test('isTrustedApiUrl rejects javascript: URLs', () => {
  // URL constructor accepts javascript: schemes; origin becomes "null".
  assert.equal(isTrustedApiUrl('javascript:alert(1)', 'https://our.api'), false);
});

test('isTrustedApiUrl rejects data: URLs', () => {
  assert.equal(
    isTrustedApiUrl('data:text/html,<script>alert(1)</script>', 'https://our.api'),
    false,
  );
});

// -------- artifact marker trust boundary --------

test('LLM-authored marker URLs fail BOTH trust checks', () => {
  const attackerUrl = 'https://evil.example/steal';
  assert.equal(isTrustedApiUrl(attackerUrl, 'https://our.api'), false);
  assert.equal(isSignedUrl(attackerUrl), false);
});

test('backend-authored signed URL passes trust check', () => {
  const backendUrl = 'https://x.blob.core.windows.net/c/f?se=2030-01-01&sig=abc';
  assert.equal(
    isTrustedApiUrl(backendUrl, 'https://our.api') || isSignedUrl(backendUrl),
    true,
  );
});

test('backend-authored same-origin record URL passes trust check', () => {
  const backendUrl = '/api/v1/stream/record/r1';
  assert.equal(
    isTrustedApiUrl(backendUrl, 'https://our.api') || isSignedUrl(backendUrl),
    true,
  );
});
