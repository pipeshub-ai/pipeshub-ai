import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { compare } from './evidence-compare.mjs';

const clean = (id, viewport) => ({
  id,
  viewport,
  docOverflowPx: 0,
  mainOverflowPx: 0,
  offscreenInteractive: [],
  clippedRegions: [],
  squeezed: [],
  pageErrors: [],
});

function run(rows) {
  const dir = mkdtempSync(join(tmpdir(), 'evidence-'));
  writeFileSync(join(dir, 'metrics.jsonl'), rows.map((r) => JSON.stringify(r)).join('\n'));
  return dir;
}

const before = run([
  { ...clean('chat', 'mobile'), offscreenInteractive: ['Send'] },
  clean('chat', 'desktop'),
]);

test('a clean after run passes', () => {
  const { failures } = compare(before, run([clean('chat', 'mobile'), clean('chat', 'desktop')]), tmpdir());
  assert.deepEqual(failures, []);
});

test('a screen missing from the after run fails', () => {
  const { failures } = compare(before, run([clean('chat', 'mobile')]), tmpdir());
  assert.equal(failures.length, 1);
  assert.match(failures[0], /chat @ desktop: measured before, not run or skipped after/);
});

test('an empty after run fails', () => {
  const { failures } = compare(before, run([]), tmpdir());
  assert.equal(failures.length, 1);
  assert.match(failures[0], /no rows/);
});

test('a regression fails', () => {
  const after = run([clean('chat', 'mobile'), { ...clean('chat', 'desktop'), squeezed: [{ label: 'canvas', width: 40 }] }]);
  const { failures } = compare(before, after, tmpdir());
  assert.deepEqual(failures, ['chat @ desktop: squeezed 1']);
});

test('a row measured before the icon fonts loaded is not failed for clipped or squeezed content', async () => {
  const { problems } = await import('./evidence-compare.mjs');
  const row = { ...clean('a', 'mobile'), clippedRegions: [{ label: 'x', px: 20 }], squeezed: [{ label: 'y', width: 10 }] };
  assert.deepEqual(problems({ ...row, fontNotLoaded: true }), []);
  assert.equal(problems(row).length, 2);
});

test('font-not-loaded rows are marked and counted, and more than 20% of them fails the run', () => {
  const some = run([{ ...clean('chat', 'mobile'), fontNotLoaded: true }, clean('chat', 'desktop'), clean('a', 'mobile'), clean('a', 'desktop'), clean('b', 'mobile'), clean('b', 'desktop')]);
  const ok = compare(some, some, tmpdir());
  assert.match(ok.markdown, /font not loaded: clipped\/squeezed not checked/);
  assert.match(ok.markdown, /1 row skipped the clipped\/squeezed checks/);
  assert.deepEqual(ok.failures, []);

  const many = run([{ ...clean('chat', 'mobile'), fontNotLoaded: true }, { ...clean('chat', 'desktop'), fontNotLoaded: true }, clean('a', 'mobile')]);
  const bad = compare(many, many, tmpdir());
  assert.equal(bad.failures.length, 1);
  assert.match(bad.failures[0], /2 of 3 after rows did not load the icon fonts/);
});

test('more small touch targets than before fails; the same or fewer passes', () => {
  const small = (count) => ({ count, first: [] });
  const was = run([{ ...clean('chat', 'mobile'), smallTouchTargets: small(2) }]);
  assert.deepEqual(compare(was, run([{ ...clean('chat', 'mobile'), smallTouchTargets: small(2) }]), tmpdir()).failures, []);
  assert.deepEqual(compare(was, run([{ ...clean('chat', 'mobile'), smallTouchTargets: small(1) }]), tmpdir()).failures, []);
  assert.deepEqual(compare(was, run([{ ...clean('chat', 'mobile'), smallTouchTargets: small(3) }]), tmpdir()).failures, [
    'chat @ mobile: small touch targets 2 → 3',
  ]);
});
