import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { normalizeUrlInputOnBlur, getUrlValidationError } from '../url-field.ts';

describe('normalizeUrlInputOnBlur', () => {
  it('returns empty for whitespace-only input', () => {
    assert.equal(normalizeUrlInputOnBlur('   '), '');
  });

  it('prepends https for bare host', () => {
    assert.equal(normalizeUrlInputOnBlur('example.com'), 'https://example.com');
    assert.equal(normalizeUrlInputOnBlur('example.com/foo'), 'https://example.com/foo');
  });

  it('leaves http and https URLs unchanged', () => {
    assert.equal(normalizeUrlInputOnBlur('http://a.com'), 'http://a.com');
    assert.equal(normalizeUrlInputOnBlur('HTTPS://a.com/path'), 'HTTPS://a.com/path');
  });

  it('does not prepend https to other URI schemes', () => {
    assert.equal(normalizeUrlInputOnBlur('ftp://files.example/'), 'ftp://files.example/');
  });
});

describe('getUrlValidationError', () => {
  it('returns null for empty values', () => {
    assert.equal(getUrlValidationError('Website URL', ''), null);
    assert.equal(getUrlValidationError('Website URL', '  '), null);
  });

  it('accepts bare hosts and paths after implicit https', () => {
    assert.equal(getUrlValidationError('Website URL', 'example.com'), null);
    assert.equal(getUrlValidationError('Website URL', 'https://example.com'), null);
    assert.equal(getUrlValidationError('Website URL', 'http://example.com'), null);
  });

  it('returns message for unparseable garbage', () => {
    const msg = getUrlValidationError('Website URL', ':::');
    assert.ok(msg && msg.includes('Website URL'));
  });

  it('rejects non-http(s) schemes', () => {
    assert.ok(getUrlValidationError('Website URL', 'javascript:alert(1)'));
    assert.ok(getUrlValidationError('Website URL', 'data:text/html,hi'));
    assert.ok(getUrlValidationError('Website URL', 'ftp://example.com/'));
  });
});
