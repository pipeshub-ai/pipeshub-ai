import { request, type Browser, type Page, type TestInfo } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { test, expect } from '../fixtures/base.fixture';
import { apiBaseURL } from '../fixtures/api-context.fixture';
import { seedResponsiveData, type SeedIds } from './seed-responsive';

export type { SeedIds };

export const VIEWPORTS = [
  { name: 'mobile', width: 390, height: 844, isMobile: true, hasTouch: true },
  { name: 'tablet', width: 820, height: 1180, isMobile: true, hasTouch: true },
  { name: 'desktop', width: 1440, height: 900, isMobile: false, hasTouch: false },
] as const;

export interface AuditRoute {
  id: string;
  path: string | ((seed: SeedIds) => string);
  action?: (page: Page) => Promise<void>;
  /** Route mocks to install before the page loads. */
  setup?: (page: Page, seed: SeedIds) => Promise<void>;
  /** A reason to skip, when the audit stack cannot show this route. */
  skip?: (seed: SeedIds) => string | undefined;
  /** The route is meant to show the not-found page, so it is measured instead of skipped. */
  notFound?: boolean;
}

const SETTLE_MS = 800;
// Actions look for one control; a missing one is a finding, not a 15 s wait.
export const ACTION_TIMEOUT = 5_000;

/**
 * Seed once per run, not once per worker: the first worker takes the lock and
 * the others wait for its result. The output dir is emptied at the start of
 * every run, so a lock from an earlier run never blocks this one.
 */
/**
 * The bearer token the signed-in app sends. Read from its own requests, because
 * where the app keeps its tokens in the browser storage differs between builds.
 */
async function appAccessToken(browser: Browser, testInfo: TestInfo): Promise<string> {
  const { baseURL, storageState } = testInfo.project.use;
  const context = await browser.newContext({ baseURL, storageState });
  try {
    const page = await context.newPage();
    const signed = page.waitForRequest((r) => r.url().includes('/api/v1/') && !!r.headers().authorization, {
      timeout: 30_000,
    });
    await page.goto('/chat/');
    return (await signed).headers().authorization.replace(/^Bearer /, '');
  } finally {
    await context.close();
  }
}

async function seedOnce(browser: Browser, testInfo: TestInfo): Promise<SeedIds> {
  const outputDir = path.dirname(testInfo.outputDir);
  const file = path.join(outputDir, 'responsive-seed.json');
  const lock = `${file}.lock`;
  fs.mkdirSync(outputDir, { recursive: true });
  try {
    fs.mkdirSync(lock);
  } catch {
    for (let i = 0; i < 240; i++) {
      if (fs.existsSync(file)) {
        const saved = JSON.parse(fs.readFileSync(file, 'utf-8')) as SeedIds | { error: string };
        if ('error' in saved) throw new Error(`seeding failed in another worker: ${saved.error}`);
        return saved;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error('timed out waiting for another worker to seed the audit data');
  }
  const api = await request.newContext({
    baseURL: apiBaseURL(),
    extraHTTPHeaders: { Authorization: `Bearer ${await appAccessToken(browser, testInfo)}` },
  });
  try {
    const seed = await seedResponsiveData(api);
    fs.writeFileSync(file, JSON.stringify(seed));
    return seed;
  } catch (error) {
    fs.writeFileSync(file, JSON.stringify({ error: String(error) }));
    throw error;
  } finally {
    await api.dispose();
  }
}

export interface Measurement {
  docOverflowPx: number;
  /** Sideways overflow of the app's main scroll box, which hides it from the page. */
  mainOverflowPx: number;
  offscreenInteractive: string[];
  /** Boxes that hide sideways overflow and cut more than 8px of their content off. */
  clippedRegions: { label: string; px: number }[];
  /** Inputs, canvases and content areas squeezed under 160px wide. */
  squeezed: { label: string; width: number }[];
  /** Informational: tap targets under 44x44 on touch viewports. */
  smallTouchTargets: { count: number; first: string[] };
}

/** Everything the audit measures, in the page. Self-contained: it runs in the browser. */
function measure(touch: boolean): Measurement {
  const width = window.innerWidth;
  const scroller = document.scrollingElement ?? document.documentElement;
  const main = document.querySelector<HTMLElement>('[data-app-main-scroll]');

  const visible = (el: Element): el is HTMLElement => {
    if (el.closest('[aria-hidden="true"]') || (el as HTMLInputElement).type === 'hidden') return false;
    if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const name = (el: HTMLElement) =>
    el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 40) || el.tagName;
  const describe = (el: HTMLElement) => {
    const cls = typeof el.className === 'string' && el.className.trim() ? `.${el.className.trim().split(/\s+/)[0]}` : '';
    const text = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.textContent?.trim().slice(0, 30) || '';
    return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${cls}${text ? ` "${text}"` : ''}`;
  };
  const inOverlay = (el: HTMLElement) => {
    for (let p: HTMLElement | null = el; p; p = p.parentElement) {
      const pos = getComputedStyle(p).position;
      if (pos === 'fixed' || (pos === 'absolute' && p.parentElement === document.body)) return true;
    }
    return false;
  };

  const offscreen = new Set<string>();
  const CONTROLS = 'button, a[href], input, select, textarea, [role=button], [role=tab]';
  const controls = document.querySelectorAll(CONTROLS);
  for (const el of controls) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right <= width + 1 && r.left >= -1) continue;
    let scrollable = false;
    for (let p = el.parentElement; p && !scrollable; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      scrollable = ox === 'auto' || ox === 'scroll';
    }
    if (!scrollable) offscreen.add(name(el));
  }

  const clipped = new Map<string, number>();
  for (const el of document.body.querySelectorAll('*')) {
    if (el === main || !visible(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.overflowX !== 'hidden' && cs.overflowX !== 'clip') continue;
    const px = el.scrollWidth - el.clientWidth;
    if (px <= 8 || cs.textOverflow === 'ellipsis') continue;
    // A pannable canvas: nodes past its edge are reached by panning or the fit button.
    if (el.classList.contains('react-flow')) continue;
    // One line of text cut at the edge is a truncation choice; report the box only
    // when it cuts a control or media, or content that runs over several lines.
    const box = el.getBoundingClientRect();
    const cutsControl = [...el.querySelectorAll(`${CONTROLS}, img, svg, canvas, video`)].some((c) => {
      const r = c.getBoundingClientRect();
      return r.width > 0 && (r.right > box.right + 1 || r.left < box.left - 1);
    });
    const lineHeight = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2;
    if (!cutsControl && el.scrollHeight <= lineHeight * 1.6) continue;
    if (!(main?.contains(el) || inOverlay(el))) continue;
    const label = describe(el);
    clipped.set(label, Math.max(px, clipped.get(label) ?? 0));
  }

  const squeezed = new Map<string, number>();
  if (width >= 375) {
    const content = document.querySelectorAll(
      // Number fields are narrow by design.
      'input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]):not([type=file]):not([type=number]), textarea, [role=textbox], canvas, .react-flow, [data-app-main-scroll] > *, .file-preview-scroll-area, .file-preview-pdf-root',
    );
    for (const el of content) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      // Small canvases are icons and animations, not content.
      if (el.tagName === 'CANVAS' && r.height < 160) continue;
      if (r.width < 160) squeezed.set(describe(el), Math.round(r.width));
    }
  }

  const small: string[] = [];
  if (touch) {
    for (const el of document.querySelectorAll('button, [role=button], a[href]')) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 44 || r.height < 44) small.push(name(el));
    }
  }

  return {
    docOverflowPx: scroller.scrollWidth - width,
    mainOverflowPx: main ? main.scrollWidth - main.clientWidth : 0,
    offscreenInteractive: [...offscreen].sort(),
    clippedRegions: [...clipped].map(([label, px]) => ({ label, px })).sort((a, b) => a.label.localeCompare(b.label)),
    squeezed: [...squeezed].map(([label, w]) => ({ label, width: w })).sort((a, b) => a.label.localeCompare(b.label)),
    smallTouchTargets: { count: small.length, first: small.slice(0, 10) },
  };
}

/**
 * Waits for web fonts, then reports whether the icon fonts the page uses are
 * loaded. While they are not, ligature text ("tune", "expand_mo") is laid out
 * as plain words and falsely trips the clipped and squeezed checks.
 */
async function iconFontsLoaded(page: Page): Promise<boolean> {
  return page.evaluate(async () => {
    await document.fonts.ready;
    const usesSymbols = [...document.querySelectorAll('span, i')].some((el) =>
      getComputedStyle(el).fontFamily.includes('Material Symbols'),
    );
    const families = ['Material Icons Outlined', ...(usesSymbols ? ['Material Symbols Outlined'] : [])];
    // check() alone answers true when the stylesheet never arrived and no face is registered, so also require a loaded face.
    return Promise.all(
      families.map(async (family) => {
        await document.fonts.load(`24px "${family}"`).catch(() => undefined);
        const face = [...document.fonts].some((f) => f.family.replace(/["']/g, '') === family && f.status === 'loaded');
        return face && document.fonts.check(`24px "${family}"`);
      }),
    ).then((all) => all.every(Boolean));
  });
}

/** Opens the mobile navigation drawer when the page shows its hamburger. */
export async function openNavIfMobile(page: Page): Promise<void> {
  const hamburger = page.getByRole('button', { name: 'Open sidebar', exact: true });
  if (await hamburger.isVisible()) {
    await hamburger.click({ timeout: ACTION_TIMEOUT });
    await page.waitForTimeout(400);
  }
}

/**
 * Registers one test per route and viewport. Layout findings are soft, so one
 * run reports every screen; uncaught page errors fail the test.
 * With EVIDENCE_DIR set, each test saves `<id>--<viewport>.png` there and
 * appends its numbers to `metrics.jsonl`.
 */
export function runViewportAudit(routes: AuditRoute[]): void {
  let seed: SeedIds;

  test.beforeAll(async ({ browser }, testInfo) => {
    seed = await seedOnce(browser, testInfo);
  });

  for (const vp of VIEWPORTS) {
    test.describe(vp.name, () => {
      test.use({
        viewport: { width: vp.width, height: vp.height },
        isMobile: vp.isMobile,
        hasTouch: vp.hasTouch,
        deviceScaleFactor: 1,
      });

      for (const route of routes) {
        test(route.id, async ({ page }) => {
          const skipReason = route.skip?.(seed);
          test.skip(!!skipReason, skipReason);

          const pageErrors: string[] = [];
          page.on('pageerror', (error) => pageErrors.push(error.message));

          await route.setup?.(page, seed);
          await page.goto(typeof route.path === 'function' ? route.path(seed) : route.path);
          // Long-lived requests (notification sockets, polling) can keep the
          // network busy, so idle is a best effort and the settle delay follows.
          await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => undefined);
          await page.waitForTimeout(SETTLE_MS);

          // Pages this stack does not serve (a feature turned off) say so instead of measuring Next's 404.
          const notFound = await page.getByText('This page could not be found.', { exact: true }).isVisible();
          test.skip(notFound && !route.notFound, 'this stack answers 404 for the page (the feature is not enabled here)');

          // Reload once if the icon fonts are missing; if they still are, the row skips the layout checks that depend on them.
          let fontNotLoaded = !(await iconFontsLoaded(page));
          if (fontNotLoaded) {
            await page.reload();
            await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => undefined);
            await page.waitForTimeout(SETTLE_MS);
            fontNotLoaded = !(await iconFontsLoaded(page));
          }

          let actionError: string | undefined;
          if (route.action) {
            try {
              await route.action(page);
            } catch (error) {
              actionError = (error instanceof Error ? error.message : String(error)).split('\n')[0];
            }
            await page.waitForTimeout(SETTLE_MS);
          }

          // Measure until two samples in a row agree (up to ~3 s), so data still
          // loading or a transition still running does not change the result.
          let m = await page.evaluate(measure, vp.hasTouch);
          for (let i = 0; i < 8; i++) {
            await page.waitForTimeout(400);
            const next = await page.evaluate(measure, vp.hasTouch);
            const same = JSON.stringify({ ...next, smallTouchTargets: 0 }) === JSON.stringify({ ...m, smallTouchTargets: 0 });
            m = next;
            if (same) break;
          }

          const evidenceDir = process.env.EVIDENCE_DIR;
          if (evidenceDir) {
            fs.mkdirSync(evidenceDir, { recursive: true });
            await page.screenshot({ path: path.join(evidenceDir, `${route.id}--${vp.name}.png`), fullPage: true });
            const row = { id: route.id, viewport: vp.name, ...m, pageErrors, actionError, ...(fontNotLoaded ? { fontNotLoaded } : null) };
            fs.appendFileSync(path.join(evidenceDir, 'metrics.jsonl'), `${JSON.stringify(row)}\n`);
          }

          expect.soft(actionError, 'the scenario could not be completed').toBeUndefined();
          expect.soft(m.docOverflowPx, 'the page scrolls sideways').toBeLessThanOrEqual(0);
          expect.soft(m.mainOverflowPx, 'the main content is wider than its box').toBeLessThanOrEqual(0);
          expect.soft(m.offscreenInteractive, 'controls outside the viewport').toEqual([]);
          if (!fontNotLoaded) {
            expect.soft(m.clippedRegions, 'boxes that cut off their content').toEqual([]);
            expect.soft(m.squeezed, 'content squeezed under 160px').toEqual([]);
          }
          // smallTouchTargets is not asserted here; evidence-compare fails a screen whose count grows.
          expect(pageErrors, 'uncaught errors in the page').toEqual([]);
        });
      }
    });
  }
}
