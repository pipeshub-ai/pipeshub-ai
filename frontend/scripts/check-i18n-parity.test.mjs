import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkLocaleParity } from './check-i18n-parity.mjs';

test('accepts nested translations, arrays, reordered and formatted interpolation variables', () => {
  const result = checkLocaleParity(
    { nested: { title: '{{name}}: {{count, number}}', rows: ['First', { text: '{{- html}}' }] } },
    { nested: { title: '{{count}}: {{name}}', rows: ['Erste', { text: '{{- html}}' }] } },
  );
  assert.equal(result.valid, true);
  assert.equal(result.sourceLeaves, 3);
});

test('reports every missing and orphaned leaf including nested array entries', () => {
  const result = checkLocaleParity(
    { nested: { title: 'Title', help: 'Help' }, rows: ['First', 'Second'] },
    { old: { title: 'Alt' }, rows: ['Erste'] },
  );
  assert.deepEqual(result.errors.missing, ['nested.title', 'nested.help', 'rows.1']);
  assert.deepEqual(result.errors.extra, ['old.title']);
  assert.equal(result.valid, false);
});

test('rejects string, object, array and null type mismatches', () => {
  const result = checkLocaleParity(
    { a: 'Text', b: { c: 'Text' }, d: ['Text'], e: null },
    { a: {}, b: 'Text', d: { 0: 'Text' }, e: 'Text' },
  );
  assert.equal(result.errors.types.length, 4);
  assert.equal(result.valid, false);
});

test('rejects missing, additional and renamed interpolation variables', () => {
  const result = checkLocaleParity(
    { a: '{{name}}', b: 'Text', c: '{{workspace}}' },
    { a: 'Name', b: '{{count}}', c: '{{organization}}' },
  );
  assert.equal(result.errors.placeholders.length, 3);
  assert.equal(result.valid, false);
});

test('requires German one and other plural variants', () => {
  const source = { items_one: '{{count}} item', items_other: '{{count}} items' };
  assert.equal(checkLocaleParity(source, source).valid, true);
  const result = checkLocaleParity(source, { items_one: '{{count}} Element' });
  assert.deepEqual(result.errors.missing, ['items_other']);
  assert.deepEqual(result.errors.plurals, ['items_other: missing cardinal string']);
  assert.equal(result.valid, false);
});

test('detects an incomplete plural group even when both files omit the same variant', () => {
  const result = checkLocaleParity({ items_one: 'Item' }, { items_one: 'Element' });
  assert.equal(result.valid, false);
  assert.deepEqual(result.errors.plurals, ['items_other: missing cardinal string']);
});

test('treats zero overrides as optional and checks ordinal groups separately', () => {
  const source = { items_one: 'One', items_other: 'Other', items_zero: 'None', rank_ordinal_other: '{{count}}th' };
  assert.equal(checkLocaleParity(source, source).valid, true);
});

test('detects orphaned empty containers', () => {
  assert.deepEqual(checkLocaleParity({}, { stale: {} }).errors.extra, ['stale']);
});
