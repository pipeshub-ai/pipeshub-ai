// Compares two viewport-audit runs (tests/e2e/responsive) and writes a
// markdown table of each screen before and after.
// usage: node scripts/evidence-compare.mjs <before-dir> <after-dir> <out.md>
// Exits 1, printing why, when the after run is empty, misses a screen the
// before run measured (not run or skipped), or still has a screen with
// overflow, clipped or squeezed content, page errors or a failed scenario.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Rows keyed by `id|viewport`; a later line for the same screen wins (re-runs append). */
export function readMetrics(dir) {
  const file = join(dir, 'metrics.jsonl');
  const rows = new Map();
  if (!existsSync(file)) return rows;
  for (const line of readFileSync(file, 'utf-8').split('\n')) {
    if (!line.trim()) continue;
    const row = JSON.parse(line);
    rows.set(`${row.id}|${row.viewport}`, row);
  }
  return rows;
}

/** What is still wrong with one measured screen; empty when it is clean. */
export function problems(row) {
  const found = [];
  if (row.docOverflowPx > 0) found.push(`page overflow ${row.docOverflowPx}px`);
  if ((row.mainOverflowPx ?? 0) > 0) found.push(`main overflow ${row.mainOverflowPx}px`);
  if (row.offscreenInteractive?.length) found.push(`clipped controls ${row.offscreenInteractive.length}`);
  // Rows measured before the icon fonts loaded lay ligature text out as words; the audit skips these two checks for them.
  if (!row.fontNotLoaded && row.clippedRegions?.length) found.push(`clipped regions ${row.clippedRegions.length}`);
  if (!row.fontNotLoaded && row.squeezed?.length) found.push(`squeezed ${row.squeezed.length}`);
  if (row.pageErrors?.length) found.push(`errors ${row.pageErrors.length}`);
  if (row.actionError) found.push('scenario failed');
  return found;
}

function cell(row, dir, outDir) {
  if (!row) return 'not run or skipped';
  const found = problems(row);
  const image = relative(outDir, join(dir, `${row.id}--${row.viewport}.png`));
  const note = row.fontNotLoaded ? ' (font not loaded: clipped/squeezed not checked)' : '';
  return `${found.length ? `❌ ${found.join(', ')}` : '✅ clean'}${note} ([image](${image}))`;
}

const FONT_SKIP_LIMIT = 0.2;
const VIEWPORT_ORDER = ['mobile', 'tablet', 'desktop'];

/** The markdown table and the reasons the after run does not pass. */
export function compare(beforeDir, afterDir, outDir) {
  const before = readMetrics(beforeDir);
  const after = readMetrics(afterDir);
  const keys = [...new Set([...before.keys(), ...after.keys()])].sort((a, b) => {
    const [ia, va] = a.split('|');
    const [ib, vb] = b.split('|');
    return ia.localeCompare(ib) || VIEWPORT_ORDER.indexOf(va) - VIEWPORT_ORDER.indexOf(vb);
  });

  const failures = [];
  if (after.size === 0) failures.push(`the after run has no rows (${join(afterDir, 'metrics.jsonl')})`);
  const lines = ['| Screen | Viewport | Before | After |', '| --- | --- | --- | --- |'];
  for (const key of keys) {
    const [id, viewport] = key.split('|');
    const a = after.get(key);
    if (!a && after.size > 0) failures.push(`${id} @ ${viewport}: measured before, not run or skipped after`);
    if (a && problems(a).length) failures.push(`${id} @ ${viewport}: ${problems(a).join(', ')}`);
    // Existing small targets are fixed screen by screen; a screen must not gain new ones.
    const wasSmall = before.get(key)?.smallTouchTargets?.count;
    const nowSmall = a?.smallTouchTargets?.count;
    if (wasSmall !== undefined && nowSmall !== undefined && nowSmall > wasSmall) {
      failures.push(`${id} @ ${viewport}: small touch targets ${wasSmall} → ${nowSmall}`);
    }
    lines.push(`| ${id} | ${viewport} | ${cell(before.get(key), beforeDir, outDir)} | ${cell(a, afterDir, outDir)} |`);
  }
  const fontSkipped = [...after.values()].filter((r) => r.fontNotLoaded).length;
  // Past this share the font check cannot be trusted for the run, so the run fails.
  if (after.size > 0 && fontSkipped / after.size > FONT_SKIP_LIMIT) {
    failures.push(`${fontSkipped} of ${after.size} after rows did not load the icon fonts (limit ${FONT_SKIP_LIMIT * 100}%)`);
  }
  lines.push('', `${fontSkipped} ${fontSkipped === 1 ? 'row' : 'rows'} skipped the clipped/squeezed checks (font not loaded).`);
  lines.push('', `${keys.length} screens; ${failures.length} problems after.`);
  return { markdown: `${lines.join('\n')}\n`, failures };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [beforeDir, afterDir, outFile] = process.argv.slice(2);
  if (!beforeDir || !afterDir || !outFile) {
    console.error('usage: node scripts/evidence-compare.mjs <before-dir> <after-dir> <out.md>');
    process.exit(2);
  }
  const { markdown, failures } = compare(beforeDir, afterDir, dirname(outFile));
  writeFileSync(outFile, markdown);
  console.log(`wrote ${outFile}`);
  for (const failure of failures) console.error(`FAIL ${failure}`);
  process.exit(failures.length ? 1 : 0);
}
