import assert from 'node:assert/strict';
import { describe, it } from 'vitest';
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
    assert.equal(getUrlValidationError(''), null);
    assert.equal(getUrlValidationError('  '), null);
  });

  it('accepts bare hosts and paths after implicit https', () => {
    assert.equal(getUrlValidationError('example.com'), null);
    assert.equal(getUrlValidationError('https://example.com'), null);
    assert.equal(getUrlValidationError('http://example.com'), null);
  });

  it('returns a code for unparseable garbage', () => {
    const msg = getUrlValidationError(':::');
    assert.equal(msg, 'url');
  });

  it('rejects non-http(s) schemes', () => {
    assert.equal(getUrlValidationError('javascript:alert(1)'), 'urlProtocol');
    assert.equal(getUrlValidationError('data:text/html,hi'), 'urlProtocol');
    assert.equal(getUrlValidationError('ftp://example.com/'), 'urlProtocol');
  });
});
