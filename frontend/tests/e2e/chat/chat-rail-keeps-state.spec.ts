/**
 * The chat rail never blanks what it already shows. Measured per action, in the page: whether the rail was rebuilt,
 * the list requests it made, skeleton and empty frames, which rows stayed the same elements, its scroll, and how much
 * it shifted. Every list answer is also held 800 ms in a second pass, so a skeleton frame cannot hide behind a fast
 * network.
 *
 * The rail's chat lists are answered in the browser (FakeChatLists), so the spec needs only a signed-in user and
 * writes nothing to the server. Tests marked fixme describe where the rail is going and are switched on as each part
 * lands.
 */
import { test, expect } from '../fixtures/base.fixture';
import {
  FAKE_ID_PREFIX,
  FakeChatLists,
  clickRailNav,
  installRailProbe,
  measure,
  railRow,
  scrollRail,
  waitForRailToSettle,
} from '../helpers/rail-probe.helper';

test.use({ viewport: { width: 1440, height: 800 } });

const OWN_ROW = `${FAKE_ID_PREFIX}o01`;
const OWN_TITLE = 'Rail check o 1';

/** Opens a rail row's menu and picks `item`. */
async function rowAction(page: import('@playwright/test').Page, id: string, title: string, item: string) {
  await railRow(page, id).hover();
  await page.locator('[data-ph-sidebar-slot]').getByRole('button', { name: `Actions for ${title}` }).click();
  await page.getByRole('menuitem', { name: item, exact: true }).click();
}

/** Client-side navigation, as a link in the app does it. */
async function push(page: import('@playwright/test').Page, url: string) {
  await page.evaluate((u) => (window as unknown as { next: { router: { push(u: string): void } } }).next.router.push(u), url);
  await page.waitForURL((u) => `${u.pathname}${u.search}`.startsWith(url.replace(/\/$/, '')));
}

for (const delayMs of [0, 800]) {
  test.describe(`chat rail, lists ${delayMs ? `held ${delayMs} ms` : 'as fast as they come'}`, () => {
    let lists: FakeChatLists;

    test.beforeEach(async ({ page }) => {
      // More shared chats than the rail shows, as a busy account has: Shared chats is a full section, five and More.
      lists = new FakeChatLists({ owned: 8, pinned: 5, shared: 8 });
      lists.delayMs = delayMs;
      await installRailProbe(page);
      await lists.install(page);
      await page.goto('/chat/');
      await expect(railRow(page, OWN_ROW)).toBeVisible({ timeout: 60_000 });
      await waitForRailToSettle(page, lists);
    });

    test('a first load shows the skeleton once, while the rail has nothing to show', async ({ page }) => {
      test.skip(delayMs === 0, 'a fast first load may finish before the skeleton is due');
      const m = await measure(page, lists, () => page.reload());
      expect(m.skeletonMs).toBeGreaterThan(0);
    });

    test('a pin shows no skeleton and keeps every row but the pinned one', async ({ page }) => {
      const m = await measure(page, lists, () => rowAction(page, OWN_ROW, OWN_TITLE, 'Pin'));
      expect(m.railRebuilt).toBe(false);
      expect(m.skeletonMs).toBe(0);
      expect(m.emptyMs).toBe(0);
      expect(m.rowsComparable).toBeGreaterThan(5);
      expect(m.rowsKept).toBeGreaterThanOrEqual(m.rowsComparable - 1);
    });

    test('to Agents and back: the rows and the footer’s org never blank', async ({ page }) => {
      for (const go of [() => clickRailNav(page, 'Agents'), () => push(page, '/chat/')]) {
        const m = await measure(page, lists, go);
        expect(m.skeletonMs).toBe(0);
        expect(m.emptyMs).toBe(0);
        expect(m.footerBlankMs).toBe(0);
      }
    });

    test('the More panel open across Agents shows its rows with no skeleton', async ({ page }) => {
      await page.locator('[data-ph-sidebar-slot]').getByText('More', { exact: true }).last().click();
      await waitForRailToSettle(page, lists);
      const m = await measure(page, lists, () => clickRailNav(page, 'Agents'));
      expect(m.panelOpen).toBe(true);
      expect(m.skeletonMs).toBe(0);
      expect(m.panelSkeletonMs).toBe(0);
    });

    test('Chat, Agents and All artifacts keep one rail: no rebuild, no request, scroll and More kept', async ({ page }) => {
      await scrollRail(page, 120);
      for (const go of [() => clickRailNav(page, 'Agents'), () => clickRailNav(page, 'All artifacts'), () => push(page, '/chat/')]) {
        const m = await measure(page, lists, go);
        expect(m.railRebuilt).toBe(false);
        expect(m.requests).toEqual({ owned: 0, shared: 0, more: 0, org: 0, logo: 0, agentChats: 0 });
        expect(Math.abs(m.scrollAfter - m.scrollBefore)).toBeLessThanOrEqual(1);
      }
      await page.locator('[data-ph-sidebar-slot]').getByText('More', { exact: true }).last().click();
      await waitForRailToSettle(page, lists);
      const m = await measure(page, lists, () => clickRailNav(page, 'Agents'));
      expect(m.panelOpen).toBe(true);
      expect(m.requests.more).toBe(0);
    });

    test('back from Collections, the rail scrolls to where it was before the first paint', async ({ page }) => {
      await scrollRail(page, 120);
      await clickRailNav(page, 'Collections');
      await waitForRailToSettle(page, lists);
      const m = await measure(page, lists, () => push(page, '/chat/'));
      expect(m.scrollAfter).toBe(120);
      expect(m.scrollLowest).toBe(120);
    });

    test('a pin, a rename, an archive and its Undo change the row and ask for no list', async ({ page }) => {
      const pin = await measure(page, lists, () => rowAction(page, OWN_ROW, OWN_TITLE, 'Pin'));
      expect(pin.requests.owned + pin.requests.shared).toBe(0);

      const renamed = 'Rail check renamed';
      const rename = await measure(page, lists, async () => {
        await rowAction(page, `${FAKE_ID_PREFIX}o02`, 'Rail check o 2', 'Rename');
        await page.keyboard.press('ControlOrMeta+A');
        await page.keyboard.type(renamed);
        await page.keyboard.press('Enter');
      });
      expect(rename.requests.owned + rename.requests.shared).toBe(0);
      await expect(page.locator('[data-ph-sidebar-slot]').getByText(renamed)).toBeVisible();

      const archive = await measure(page, lists, () => rowAction(page, `${FAKE_ID_PREFIX}o03`, 'Rail check o 3', 'Archive'));
      expect(archive.requests.owned + archive.requests.shared).toBe(0);
      await expect(railRow(page, `${FAKE_ID_PREFIX}o03`)).toHaveCount(0);

      const undo = await measure(page, lists, () => page.getByRole('button', { name: 'Undo' }).first().click());
      expect(undo.requests.owned + undo.requests.shared).toBe(0);
      await expect(railRow(page, `${FAKE_ID_PREFIX}o03`)).toHaveCount(1);
      expect(undo.rowsKept).toBe(undo.rowsComparable);
    });

    test('a reload asks for each list once, and the rail does not shift', async ({ page }) => {
      const m = await measure(page, lists, () => page.reload(), { quietMs: 2500 });
      expect(m.requests.owned).toBe(1);
      expect(m.requests.shared).toBe(1);
      expect(m.requests.org).toBeLessThanOrEqual(1);
      expect(m.railShift).toBeLessThan(0.001);
    });
  });
}
