import { test, expect, EXPECTS_CSP_VIOLATION } from '../fixtures/base.fixture';

/**
 * SEC-D-07 (ADR-WEB-01): the real UI works under its CSP, which is always on. The base fixture already fails any spec
 * that meets a CSP violation; this one walks the main pages on purpose, keeps a screenshot of each, and proves that
 * another site cannot frame the app.
 */

const PAGES = [
  '/chat/',
  '/knowledge-base/',
  '/artifacts/',
  '/agents/',
  '/workspace/general/',
  '/workspace/profile/',
  '/workspace/users/',
  '/workspace/connectors/',
  '/workspace/services/',
];

test.describe('browser security headers @smoke', () => {
  for (const route of PAGES) {
    test(`${route} loads under the CSP`, async ({ page }, testInfo) => {
      const response = await page.goto(route);
      expect(response, route).not.toBeNull();
      const headers = response!.headers();
      const csp = headers['content-security-policy'] ?? '';
      expect(csp).toContain("frame-ancestors 'none'");
      expect(csp).toMatch(/script-src 'self'[^;]*'sha256-/);
      expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/);
      expect(headers['x-frame-options']).toBe('DENY');
      expect(headers['x-content-type-options']).toBe('nosniff');
      expect(headers['x-powered-by']).toBeUndefined();
      await page.waitForLoadState('networkidle');
      await expect(page).not.toHaveURL(/\/login/);
      await page.screenshot({ path: testInfo.outputPath(`${route.replace(/\//g, '_')}.png`), fullPage: true });
    });
  }

  // frame-ancestors 'none' refuses every embedder, the app's own origin included, so a frame the app opens of itself
  // proves what a sibling site would meet, with no second origin to stand up. The iframe's load event also fires for
  // the browser's refusal page, so the test waits on the outcome, not on a clock.
  test('the chat cannot be framed, even by its own origin', async ({ page }) => {
    test.info().annotations.push(EXPECTS_CSP_VIOLATION);
    await page.goto('/chat/');
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          const frame = document.createElement('iframe');
          frame.id = 'pxh-framing-probe';
          frame.src = '/chat/';
          frame.onload = () => resolve();
          document.body.appendChild(frame);
        }),
    );
    const probe = await (await page.$('#pxh-framing-probe'))?.contentFrame();
    expect(probe, 'the probe frame exists').toBeTruthy();
    expect(probe!.url()).not.toContain('/chat/');
    expect(await probe!.locator('body *').count()).toBe(0);
  });

  test('an injected inline script does not run', async ({ page }) => {
    test.info().annotations.push(EXPECTS_CSP_VIOLATION);
    await page.goto('/chat/');
    const ran = await page.evaluate(() => {
      (window as unknown as { __pxhInjected?: number }).__pxhInjected = 0;
      const s = document.createElement('script');
      s.textContent = 'window.__pxhInjected = 1';
      document.body.appendChild(s);
      return (window as unknown as { __pxhInjected?: number }).__pxhInjected;
    });
    expect(ran).toBe(0);
  });
});
