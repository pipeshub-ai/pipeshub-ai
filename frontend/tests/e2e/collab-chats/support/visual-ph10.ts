import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { Browser, Locator, Page } from '@playwright/test';
import { storageStateFor } from './two-users.fixture';
import { NodeApi, stackState, type Actor } from './stack';

/** Opt-in: the PH-10 screenshot review is a gate aid, not a journey. */
export const VISUAL_ENABLED = process.env.PCC_VISUAL_TOUR === '1';

export const OUT_DIR = path.resolve('test-results', 'visual-ph10');
const MANIFEST = path.join(OUT_DIR, '.manifest.jsonl');

export interface Combo {
  viewport: 'desktop' | 'mobile';
  theme: 'light' | 'dark';
  size: { width: number; height: number };
}

export const COMBOS: Combo[] = [
  { viewport: 'desktop', theme: 'light', size: { width: 1440, height: 900 } },
  { viewport: 'desktop', theme: 'dark', size: { width: 1440, height: 900 } },
  { viewport: 'mobile', theme: 'light', size: { width: 375, height: 812 } },
  { viewport: 'mobile', theme: 'dark', size: { width: 375, height: 812 } },
];

export type RosterKey = 'owner' | 'write_recipient' | 'read_recipient' | 'team_writer' | 'team_reader' | 'stranger' | 'admin';

export interface Person {
  actor: Actor;
  page: Page;
  api: NodeApi;
}

/** Browser contexts for roster users in one viewport and theme (the theme is the stored preference the app reads). */
export function makeCast(browser: Browser, combo: Combo) {
  const { roster } = stackState();
  const opened: { close: () => Promise<void> }[] = [];
  const cache = new Map<RosterKey, Person>();
  const open = async (key: RosterKey): Promise<Person> => {
    const hit = cache.get(key);
    if (hit) return hit;
    const actor = roster[key];
    const state = storageStateFor(actor);
    state.origins[0].localStorage.push({ name: 'pipeshub-theme-preference', value: combo.theme });
    const context = await browser.newContext({
      storageState: state,
      viewport: combo.size,
      colorScheme: combo.theme,
      locale: 'en-US',
      timezoneId: 'UTC',
      reducedMotion: 'reduce',
    });
    opened.push(context);
    if (combo.viewport === 'mobile') {
      // useIsMobile() starts false and flips after mount, which crashes SidebarBase on the base commit too ("Rendered
      // fewer hooks than expected"). The served module is patched so the hook reads the media query on first render.
      await context.route('**/_next/static/chunks/lib_*.js', async (route) => {
        // The dev server occasionally drops a connection ("socket hang up") while it compiles a lazily loaded chunk;
        // retry, and let the browser load the chunk itself if it keeps failing.
        let response;
        for (let attempt = 0; attempt < 3 && !response; attempt++) {
          response = await route.fetch().catch(() => undefined);
        }
        if (!response) return route.continue();
        const body = await response.text();
        const patched = body.replace(
          /(function useIsMobile\(\) \{[\s\S]*?\["useState"\]\))\(false\)/,
          '$1(()=>typeof window!=="undefined"&&window.matchMedia("(max-width: 768px)").matches)',
        );
        await route.fulfill({ response, body: patched });
      });
    }
    // The Next dev-server badge would sit on every screenshot.
    await context.addInitScript(() => {
      const hide = () => {
        const s = document.createElement('style');
        s.textContent = 'nextjs-portal{display:none!important}';
        document.documentElement.appendChild(s);
      };
      if (document.documentElement) hide();
      else document.addEventListener('DOMContentLoaded', hide);
    });
    const person = { actor, page: await context.newPage(), api: new NodeApi(actor) };
    cache.set(key, person);
    return person;
  };
  return { open, close: () => Promise.all(opened.map((c) => c.close())) };
}

export type Cast = ReturnType<typeof makeCast>;

function record(entry: Record<string, unknown>) {
  mkdirSync(OUT_DIR, { recursive: true });
  appendFileSync(MANIFEST, JSON.stringify(entry) + '\n');
}

let current: Combo = COMBOS[0];
export const setCurrentCombo = (c: Combo) => {
  current = c;
};

/** Saves `<id>-<slug>--<viewport>--<theme>.png` (the viewport) and, with `el`, `<id>-<slug>-element--...` (the element alone). */
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

/** A state the review could not reach, with the reason; listed in INDEX.md. */
export function unreachable(id: string, slug: string, reason: string) {
  record({ unreachable: `${id}-${slug}--${current.viewport}--${current.theme}`, reason });
}

export function writeIndex() {
  const lines = existsSync(MANIFEST) ? readFileSync(MANIFEST, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  const pngs = existsSync(OUT_DIR) ? readdirSync(OUT_DIR).filter((f) => f.endsWith('.png')).sort() : [];
  const desc = new Map<string, string>();
  for (const l of lines) if (l.file && !desc.has(l.file)) desc.set(l.file, l.description);
  const out = ['# PH-10 visual review', '', `${pngs.length} screenshots: \`<NN>-<slug>[-element]--<viewport>--<theme>.png\`.`, '', '| File | What it should show |', '| --- | --- |'];
  for (const f of pngs) out.push(`| ${f} | ${desc.get(f.replace(/--(desktop|mobile)--(light|dark)\.png$/, '')) ?? ''} |`);
  out.push('', '## Not captured', '');
  const skipped = lines.filter((l) => l.unreachable);
  if (skipped.length === 0) out.push('None.');
  for (const l of skipped) out.push(`- ${l.unreachable}: ${l.reason}`);
  writeFileSync(path.join(OUT_DIR, 'INDEX.md'), out.join('\n') + '\n');
}
