import test from 'node:test';
import * as assert from 'node:assert/strict';
import * as path from 'path';
import { resolveStaticExportPath } from '../static-export';

const OUT = path.join(path.sep, 'app', 'out');
const has = (...files: string[]) => (p: string) => files.map((f) => path.join(OUT, f)).includes(p);

for (const pathname of ['/record/abc-123', '/record/abc-123/', '/record/abc-123/preview', '/record/abc-123/preview/']) {
  test(`serves the record shell for ${pathname}`, () => {
    assert.equal(resolveStaticExportPath(OUT, pathname, has()), path.join(OUT, 'record', 'index.html'));
  });
}

test('serves the record shell route payload for client-side navigation', () => {
  assert.equal(
    resolveStaticExportPath(OUT, '/record/abc-123/index.txt', has()),
    path.join(OUT, 'record', 'index.txt'),
  );
});

test('serves /record/ itself from its own directory', () => {
  assert.equal(
    resolveStaticExportPath(OUT, '/record/', has('record/index.html')),
    path.join(OUT, 'record', 'index.html'),
  );
});

test('resolves an extensionless route to its index.html', () => {
  assert.equal(resolveStaticExportPath(OUT, '/login', has('login/index.html')), path.join(OUT, 'login', 'index.html'));
  assert.equal(resolveStaticExportPath(OUT, '/login/', has('login/index.html')), path.join(OUT, 'login', 'index.html'));
});

test('passes asset paths through unchanged', () => {
  assert.equal(
    resolveStaticExportPath(OUT, '/_next/static/chunks/main.js', has()),
    path.join(OUT, '_next', 'static', 'chunks', 'main.js'),
  );
});

test('does not treat a route that only starts with "record" as a record URL', () => {
  assert.equal(
    resolveStaticExportPath(OUT, '/records/abc', has('records/abc/index.html')),
    path.join(OUT, 'records', 'abc', 'index.html'),
  );
});

// The handler decodes the pathname, so an encoded separator can smuggle `..` past URL parsing.
for (const pathname of ['/..%2f..%2fetc/passwd', '/../../etc/passwd', '/%2e%2e/secret']) {
  test(`refuses a path that escapes the export: ${pathname}`, () => {
    assert.equal(resolveStaticExportPath(OUT, decodeURIComponent(pathname), has()), null);
  });
}

test('a record URL with traversal still only reaches the record shell', () => {
  assert.equal(
    resolveStaticExportPath(OUT, decodeURIComponent('/record/x/..%2f..%2f..%2fetc%2fpasswd'), has()),
    path.join(OUT, 'record', 'index.html'),
  );
});

