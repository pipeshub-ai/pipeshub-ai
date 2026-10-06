import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { Locator, Page } from '@playwright/test';
import { COMBOS, makeCast, VISUAL_ENABLED, type Cast, type Combo } from './visual-ph10';

export { COMBOS, makeCast, VISUAL_ENABLED };
export type { Cast, Combo };

export const OUT_DIR = path.resolve('test-results', 'visual-ph12');
const MANIFEST = path.join(OUT_DIR, '.manifest.jsonl');

let current: Combo = COMBOS[0];
export const setCurrentCombo = (c: Combo) => {
  current = c;
};

function record(entry: Record<string, unknown>) {
  mkdirSync(OUT_DIR, { recursive: true });
  appendFileSync(MANIFEST, JSON.stringify(entry) + '\n');
}

/** Saves `<id>-<slug>--<viewport>--<theme>.png` and, with `el`, `<id>-<slug>-element--...`. */
export async function shot(page: Page, id: string, slug: string, description: string, el?: Locator): Promise<void> {
  mkdirSync(OUT_DIR, { recursive: true });
  const suffix = `--${current.viewport}--${current.theme}.png`;
  await page.waitForTimeout(400);
  const base = `${id}-${slug}`;
  await page.screenshot({ path: path.join(OUT_DIR, base + suffix), fullPage: false, animations: 'disabled', caret: 'hide' });
  record({ file: base, description });
  if (el) {
    await el.first().screenshot({ path: path.join(OUT_DIR, `${base}-element${suffix}`), animations: 'disabled', caret: 'hide' });
    record({ file: `${base}-element`, description: `${description} (the element alone)` });
  }
}

export function unreachable(id: string, slug: string, reason: string) {
  record({ unreachable: `${id}-${slug}--${current.viewport}--${current.theme}`, reason });
}

export function writeIndex() {
  const lines = existsSync(MANIFEST) ? readFileSync(MANIFEST, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  const pngs = existsSync(OUT_DIR) ? readdirSync(OUT_DIR).filter((f) => f.endsWith('.png')).sort() : [];
  const desc = new Map<string, string>();
  for (const l of lines) if (l.file && !desc.has(l.file)) desc.set(l.file, l.description);
  const out = ['# PH-12 visual review', '', `${pngs.length} screenshots: \`<NN>-<slug>[-element]--<viewport>--<theme>.png\`.`, '', '| File | What it should show |', '| --- | --- |'];
  for (const f of pngs) out.push(`| ${f} | ${desc.get(f.replace(/--(desktop|mobile)--(light|dark)\.png$/, '')) ?? ''} |`);
  out.push('', '## Not captured', '');
  const skipped = lines.filter((l) => l.unreachable);
  if (skipped.length === 0) out.push('None.');
  for (const l of skipped) out.push(`- ${l.unreachable}: ${l.reason}`);
  writeFileSync(path.join(OUT_DIR, 'INDEX.md'), out.join('\n') + '\n');
}
