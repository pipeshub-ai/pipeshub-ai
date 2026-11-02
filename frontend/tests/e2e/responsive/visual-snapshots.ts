import * as fs from 'fs';
import * as path from 'path';
import { test, expect } from '../fixtures/base.fixture';
import { SETTLE_MS, iconFontsLoaded, seedOnce, type AuditRoute, type SeedIds } from './viewport-audit';

export const VISUAL_VIEWPORTS = [
  { name: 'phone', width: 375, height: 812 },
  { name: 'laptop', width: 1440, height: 900 },
] as const;

export const THEMES = ['light', 'dark'] as const;

/**
 * One screenshot per route, viewport and theme. With VISUAL_BASELINE_DIR set
 * the shot is compared with the baseline; otherwise it is saved to EVIDENCE_DIR.
 */
export function runVisualSnapshots(routes: AuditRoute[]): void {
  let seed: SeedIds;

  test.beforeAll(async ({ browser }, testInfo) => {
    seed = await seedOnce(browser, testInfo);
  });

  for (const vp of VISUAL_VIEWPORTS) {
    for (const theme of THEMES) {
      test.describe(`${vp.name} ${theme}`, () => {
        test.use({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 1 });

        for (const route of routes) {
          test(`${route.id} ${vp.name} ${theme}`, async ({ page }, testInfo) => {
            const skipReason = route.skip?.(seed);
            test.skip(!!skipReason, skipReason);

            await page.addInitScript((t) => localStorage.setItem('pipeshub-theme-preference', t), theme);
            await route.setup?.(page, seed);
            await page.goto(typeof route.path === 'function' ? route.path(seed) : route.path);
            await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => undefined);
            await page.waitForTimeout(SETTLE_MS);

            const notFound = await page.getByText('This page could not be found.', { exact: true }).isVisible();
            test.skip(notFound && !route.notFound, 'this stack answers 404 for the page (the feature is not enabled here)');

            if (!(await iconFontsLoaded(page))) {
              await page.reload();
              await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => undefined);
              await iconFontsLoaded(page);
            }
            // Dialog routes open their dialog first, so the shot shows it.
            await route.action?.(page);
            await page.waitForTimeout(SETTLE_MS);

            const name = `${route.id}--${vp.name}--${theme}.png`;
            if (process.env.VISUAL_BASELINE_DIR) {
              await expect(page).toHaveScreenshot(name, {
                fullPage: true,
                animations: 'disabled',
                maxDiffPixelRatio: 0.01,
                mask: [page.locator('time, [data-relative-time]')],
              });
            } else {
              const dir = process.env.EVIDENCE_DIR ?? testInfo.outputDir;
              fs.mkdirSync(dir, { recursive: true });
              await page.screenshot({ path: path.join(dir, name), fullPage: true });
            }
          });
        }
      });
    }
  }
}
