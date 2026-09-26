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
  // A plural gap is reported once, as a plural error rather than a missing key.
  assert.deepEqual(result.errors.missing, []);
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

test('asks each language only for the plural categories it uses', () => {
  const source = { items_one: '{{count}} item', items_other: '{{count}} items' };

  // Chinese and Korean have a single category, so `_one` is neither required nor orphaned.
  for (const language of ['zh-CN', 'zh-TW', 'ko-KR']) {
    const result = checkLocaleParity(source, { items_other: '{{count}} 個項目' }, language);
    assert.equal(result.valid, true, `${language} should accept _other alone`);
  }

  // Spanish additionally needs `_many`, which the English source does not carry.
  const spanish = checkLocaleParity(source, { items_one: '{{count}} elemento', items_other: '{{count}} elementos' }, 'es-ES');
  assert.deepEqual(spanish.errors.plurals, ['items_many: missing cardinal string']);
  assert.equal(spanish.valid, false);

  const complete = checkLocaleParity(
    source,
    { items_one: '{{count}} elemento', items_many: '{{count}} de elementos', items_other: '{{count}} elementos' },
    'es-ES',
  );
  assert.equal(complete.valid, true);
  assert.deepEqual(complete.errors.extra, [], '_many is a real Spanish category, not an orphan');
});

test('checks placeholders inside a plural category the source does not define', () => {
  const source = { items_one: '{{count}} item', items_other: '{{count}} items' };
  const result = checkLocaleParity(
    source,
    { items_one: '{{count}} elemento', items_many: '{{total}} de elementos', items_other: '{{count}} elementos' },
    'es-ES',
  );
  assert.deepEqual(result.errors.placeholders, ['items_many: count / total']);
  assert.equal(result.valid, false);
});

test('judges plural groups inside an absent subtree by the target language, not the source', () => {
  const source = { panel: { title: 'Title', items_one: '{{count}} item', items_other: '{{count}} items' } };

  const chinese = checkLocaleParity(source, {}, 'zh-CN');
  assert.deepEqual(chinese.errors.missing, ['panel.title'], 'only the plain leaf is missing');
  assert.deepEqual(chinese.errors.plurals, ['panel.items_other: missing cardinal string'],
    'Chinese must not be asked for _one just because the subtree is absent');

  const spanish = checkLocaleParity(source, {}, 'es-ES');
  assert.deepEqual(spanish.errors.plurals, [
    'panel.items_many: missing cardinal string',
    'panel.items_one: missing cardinal string',
    'panel.items_other: missing cardinal string',
  ], 'Spanish needs _many even though the English source has no such category');
});

test('still reports an absent subtree that holds nothing', () => {
  assert.deepEqual(checkLocaleParity({ empty: {} }, {}).errors.missing, ['empty']);
});

test('an optional plural override stays optional, but is checked once it is there', () => {
  const source = { items_one: '{{count}} item', items_other: '{{count}} items', items_zero: 'None for {{count}}' };
  const base = { items_one: '{{count}} Element', items_other: '{{count}} Elemente' };

  assert.equal(checkLocaleParity(source, base).valid, true, '_zero may be left out');
  assert.equal(checkLocaleParity(source, { ...base, items_zero: 'Keine für {{count}}' }).valid, true);

  const renamed = checkLocaleParity(source, { ...base, items_zero: 'Keine für {{total}}' });
  assert.deepEqual(renamed.errors.placeholders, ['items_zero: count / total']);

  const wrongType = checkLocaleParity(source, { ...base, items_zero: { nested: 'x' } });
  assert.deepEqual(wrongType.errors.types, ['items_zero: string / object']);
});

test('a target-only plural override may drop a variable but not invent one', () => {
  const source = { items_one: '{{count}} item', items_other: '{{count}} items' };
  const base = { items_one: '{{count}} Element', items_other: '{{count}} Elemente' };

  // A zero form reads better without the number, and the source has no _zero to match.
  assert.equal(checkLocaleParity(source, { ...base, items_zero: 'Keine' }).valid, true);

  const invented = checkLocaleParity(source, { ...base, items_zero: 'Keine für {{total}}' });
  assert.deepEqual(invented.errors.placeholders, ['items_zero: count / total unknown']);
});
