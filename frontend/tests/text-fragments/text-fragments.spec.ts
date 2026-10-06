import fs from 'node:fs';
import path from 'node:path';
import { test, expect, type Page } from '@playwright/test';
import { PNG } from 'pngjs';
import { FAKE_ORIGIN, fixtureHtml, hasDirective, loadCases, squash, type GoldenCase } from './corpus';

const POLYFILL_UTILS = fs.readFileSync(
  path.resolve(__dirname, '../../node_modules/text-fragments-polyfill/src/text-fragment-utils.js'),
  'utf-8',
);
const UTILS_PATH = '/__polyfill/text-fragment-utils.js';

const cases = loadCases();

async function serveFixtures(page: Page): Promise<void> {
  await page.route(`${FAKE_ORIGIN}/**`, async (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === UTILS_PATH) {
      return route.fulfill({ contentType: 'text/javascript', body: POLYFILL_UTILS });
    }
    const match = /^\/([\w-]+)\.html$/.exec(pathname);
    if (!match) return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({ contentType: 'text/html; charset=utf-8', body: fixtureHtml(match[1]) });
  });
}

// The polyfill is a second, independent implementation of the spec's matching
// algorithm; the browser's own is checked separately below.
async function polyfillHighlight(page: Page, url: string): Promise<string | null> {
  return page.evaluate(
    async ({ href, utils }) => {
      const mod = await import(utils);
      const hash = new URL(href).hash;
      const directives = mod.parseFragmentDirectives(mod.getFragmentDirectives(hash));
      const first = directives.text?.[0];
      if (!first) return null;
      const ranges = mod.processTextFragmentDirective(first, document, document.body);
      return ranges.length ? ranges[0].toString() : null;
    },
    { href: url, utils: UTILS_PATH },
  );
}

async function nativeMarkedPixels(page: Page): Promise<number> {
  await page.waitForTimeout(250);
  const png = PNG.sync.read(await page.screenshot());
  let marked = 0;
  for (let i = 0; i < png.data.length; i += 4) {
    if (png.data[i] > 240 && png.data[i + 1] < 20 && png.data[i + 2] > 240) marked += 1;
  }
  return marked;
}

function urlOf(testCase: GoldenCase): string {
  return testCase.expected_url;
}

// Cases the polyfill cannot judge. Chromium itself matches them (see the native
// suite below), so these say something about the polyfill, not about our URLs.
const POLYFILL_GAPS: Record<string, string> = {
  'nbsp-and-zero-width': 'the polyfill does not fold NBSP or zero-width characters the way Chromium does',
};

test.describe('text fragments: matcher agreement (polyfill)', () => {
  for (const testCase of cases) {
    test(testCase.id, async ({ page }) => {
      test.skip(testCase.id in POLYFILL_GAPS, POLYFILL_GAPS[testCase.id]);
      await serveFixtures(page);
      await page.goto(`${FAKE_ORIGIN}/${testCase.fixture}.html`);

      const highlight = await polyfillHighlight(page, urlOf(testCase));

      if (testCase.expected_highlight === null) {
        expect(highlight).toBeNull();
        return;
      }
      expect(highlight).not.toBeNull();
      expect(squash(highlight as string)).toBe(squash(testCase.expected_highlight));
    });
  }
});

test.describe('text fragments: native Chromium highlight', () => {
  for (const testCase of cases) {
    test(testCase.id, async ({ page }) => {
      await serveFixtures(page);
      await page.goto(urlOf(testCase));
      const marked = await nativeMarkedPixels(page);

      if (hasDirective(urlOf(testCase))) {
        expect(marked, 'native ::target-text should paint the matched text').toBeGreaterThan(50);
      } else {
        expect(marked).toBe(0);
      }
    });
  }

  test('control: a directive that matches nothing paints nothing', async ({ page }) => {
    await serveFixtures(page);
    await page.goto(`${FAKE_ORIGIN}/report.html#:~:text=this%20phrase%20is%20not%20on%20the%20page`);
    expect(await nativeMarkedPixels(page)).toBe(0);
  });

  test('control: an unencoded hyphen invalidates the whole directive', async ({ page }) => {
    await serveFixtures(page);
    const encoded = `${FAKE_ORIGIN}/report.html#:~:text=Revenue%20grew%2012%25%20year%2Dover%2Dyear`;
    await page.goto(encoded);
    expect(await nativeMarkedPixels(page), 'the encoded form is the positive control').toBeGreaterThan(50);

    await page.goto(`${FAKE_ORIGIN}/report.html?unencoded#:~:text=Revenue%20grew%2012%25%20year-over-year`);
    expect(await nativeMarkedPixels(page)).toBe(0);
  });
});
