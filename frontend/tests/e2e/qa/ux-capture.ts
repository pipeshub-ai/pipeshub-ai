/**
 * QA-CLASSIC: what the UX capture specs share (ux-shots.spec.ts on the harness stack, ux-shots-engine.spec.ts on
 * the durable one): one state at every viewport and scheme, compared with its baseline, written to the report's
 * gallery and held to axe (WCAG 2.1 A and AA). See ux-shots.spec.ts for the whole design.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { axeViolations } from '../harness/support/classic-jobs';
import { resultsDir } from './support';

export interface Manifest {
  playwright: string;
  viewports: Record<string, { width: number; height: number }>;
  schemes: Array<'light' | 'dark'>;
  /** What ux-shots.spec.ts captures on the harness stack. */
  captures: string[];
  /** What ux-shots-engine.spec.ts captures on the durable stack (engine runs). */
  engineCaptures: string[];
  /** Axe rules a capture still breaks, each tracked by a STATUS row: a new rule fails, and so does one fixed. */
  axeKnown?: Record<string, { rules: string[]; tracked: string }>;
}

export const MANIFEST = JSON.parse(readFileSync(path.join(__dirname, 'ux-captures.json'), 'utf8')) as Manifest;

/** The captures a spec took, for its last test to check against the manifest. */
export const taken: string[] = [];

/**
 * What changes with the clock or the run, never with the design, under `root`.
 */
export const dynamic = (root: Page | Locator): Locator[] => [
  root.getByTestId('job-card-elapsed'),
  root.getByTestId('job-card-meta'),
  root.getByTestId('job-panel-status'),
  root.getByText(/\b\d{1,2}:\d{2}(\s?[AP]M)?\b/),
  root.getByText(/\b\d+\s?[a-z]+\.?\s+ago\b/i),
  root.getByText(/\b\d{1,2} [A-Z][a-z]{2,8},? \d{4}\b|\b[A-Z][a-z]{2,8} \d{1,2},? \d{4}\b/),
  root.getByText(/\b\d{1,2}\/\d{1,2}\/\d{4}\b/),
  root.getByText(/^(now|just now|today|yesterday)$/i),
  // How long a job or turn took ("finished in 16s", "2m 05s").
  root.getByText(/\bin \d+(m \d{1,2})?s\b/),
];

/**
 * Opens one state at every viewport and scheme and compares it with its baseline. `open` brings the page to the
 * state from a navigation, so each variant is drawn the way a person loads it, not resized after the fact.
 */
export async function capture(page: Page, name: string, open: (page: Page) => Promise<void>, part?: (page: Page) => Locator) {
  const gallery = resultsDir(test.info(), 'ux');
  const known = new Set(MANIFEST.axeKnown?.[name]?.rules ?? []);
  const seen = new Set<string>();
  for (const [viewport, size] of Object.entries(MANIFEST.viewports)) {
    for (const scheme of MANIFEST.schemes) {
      await page.setViewportSize(size);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await open(page);
      const shot = `${name}-${viewport}-${scheme}`;
      // A part (an overlay) is captured alone: what shows behind it, and how far it is scrolled, is another capture's.
      // Its masks are its own: a mask is painted over its box whatever covers it, so a clock behind the overlay would
      // be painted over the overlay.
      const target = part ? part(page) : page;
      const masks = dynamic(target);
      await expect.soft(target, shot).toHaveScreenshot(`${shot}.png`, { mask: masks, timeout: 20_000 });
      await target.screenshot({ path: path.join(gallery, `${shot}.png`), mask: masks, animations: 'disabled', caret: 'hide' });
      const violations = await axeViolations(page, [part ? await selectorOf(part(page)) : 'body']);
      for (const v of violations) seen.add(v.id);
      // Each look's findings beside its picture, so a known rule's elements can be read without a rerun.
      if (violations.length) writeFileSync(path.join(gallery, `${shot}-axe.json`), `${JSON.stringify(violations, null, 1)}\n`);
      expect.soft(violations.filter((v) => !known.has(v.id)), `${shot} axe`).toEqual([]);
      taken.push(name);
    }
  }
  expect.soft([...known].filter((rule) => !seen.has(rule)), `${name}: fixed, so take these out of ux-captures.json axeKnown`).toEqual([]);
}

/** A part's own element as a selector axe can include: its test id, or for a dialog its id (Radix gives each one). */
async function selectorOf(part: Locator): Promise<string> {
  const id = await part.getAttribute('data-testid');
  if (id) return `[data-testid="${id}"]`;
  const role = await part.getAttribute('role');
  const elementId = await part.getAttribute('id');
  if ((role === 'dialog' || role === 'alertdialog') && elementId) return `[id="${elementId}"]`;
  throw new Error('a captured part needs a data-testid, or is a dialog with an id, for its axe pass');
}

/**
 * A page drawn: its ready element visible and its fonts (the icon font too) loaded. Animations need no wait:
 * `toHaveScreenshot` stops them and takes a picture only once two in a row agree.
 */
export async function goto(page: Page, url: string, ready: (page: Page) => Locator): Promise<void> {
  await page.goto(url);
  await expect(ready(page).first()).toBeVisible({ timeout: 60_000 });
  await settle(page);
}

export async function settle(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => document.fonts.check('24px "Material Symbols Outlined"') || document.fonts.check('24px "Material Icons Outlined"'), null, { timeout: 30_000 }).catch(() => undefined);
}

export const heading = (page: Page) => page.getByRole('heading', { level: 1 });

/** The person's stored theme would win over the emulated scheme. */
export function followEmulatedScheme(): void {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem('pipeshub-theme-preference'));
  });
}

/** The spec's last test: the manifest's list for it names exactly the captures it took. */
export function expectManifestLists(list: 'captures' | 'engineCaptures'): void {
  test(`UX the manifest lists exactly the captures taken (${list})`, async () => {
    const names = [...new Set(taken)].sort();
    expect(names, `ux-captures.json ${list} names every capture the spec takes, and no other`).toEqual([...MANIFEST[list]].sort());
  });
}
