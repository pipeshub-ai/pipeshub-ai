/**
 * A UI case's pictures for the evidence (`docs/agent-harness-v2/ui-cases/*.json`): light and dark, desktop
 * (1280 x 900) and phone (390 x 844), saved as `<id>-<desktop|phone>-<light|dark>.png` when the lane names a folder
 * (`E2E_UI_CASE_SHOTS=/work/pw/<dir>` on run.sh, `UI_CASE_SHOTS` in the runner); otherwise nothing is taken.
 */
import { mkdirSync } from 'fs';
import type { Locator, Page } from '@playwright/test';
import { expect } from '@playwright/test';

export const SHOTS = process.env.UI_CASE_SHOTS ?? '';
const PANNING_HEIGHT = 2400;

const VIEWS = [
  { view: 'desktop', theme: 'light', size: { width: 1280, height: 900 } },
  { view: 'desktop', theme: 'dark', size: { width: 1280, height: 900 } },
  { view: 'phone', theme: 'light', size: { width: 390, height: 844 } },
  { view: 'phone', theme: 'dark', size: { width: 390, height: 844 } },
] as const;

/** The app follows the system theme, so emulating the colour scheme switches it without a reload. */
export async function followSystemTheme(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('pipeshub-theme-preference', 'system');
    } catch {
      /* storage blocked: the default is the system theme too */
    }
  });
}

/**
 * The icon fonts come from Google Fonts, which a browser in a container sometimes fails to reach; the runner fetches
 * them instead, with retries, so a picture shows icons rather than their names. Only when pictures are taken.
 */
export async function iconFonts(page: Page): Promise<void> {
  if (!SHOTS) return;
  await page.route(/fonts\.(googleapis|gstatic)\.com/, async (route) => {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        return await route.fulfill({ response: await route.fetch() });
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    return route.abort();
  });
}

/**
 * The case's four pictures with `target` in view. At phone width nothing may scroll sideways, except on a page whose
 * canvas pans by design (the agent builder's flow: `pans: true`), whose pictures are taken in a window 2400 px tall.
 */
export async function shoot(
  page: Page,
  id: string,
  target?: () => Locator,
  options: { pans?: boolean; prepare?: (view: 'desktop' | 'phone') => Promise<void> } = {},
): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page
    .waitForFunction(() => document.fonts.check('24px "Material Icons Outlined"'), null, { timeout: 10_000 })
    .catch(() => undefined);
  for (const { view, theme, size } of VIEWS) {
    // A canvas does not scroll its card into view, so the window is made tall enough to hold the card instead.
    await page.setViewportSize(options.pans === true ? { width: size.width, height: PANNING_HEIGHT } : size);
    await page.emulateMedia({ colorScheme: theme });
    // A surface a phone shows apart from the desktop one (the activity drawer is a bottom sheet there) is opened anew.
    if (options.prepare) await options.prepare(view);
    if (target) await target().scrollIntoViewIfNeeded();
    // The theme switch repaints through a CSS transition.
    await page.waitForTimeout(400);
    if (view === 'phone' && options.pans !== true) {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${id} fits 390 px`).toBe(true);
    }
    await page.screenshot({ path: `${SHOTS}/${id}-${view}-${theme}.png` });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ colorScheme: 'light' });
}
