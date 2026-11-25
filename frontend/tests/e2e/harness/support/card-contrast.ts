/**
 * R25UI-FU-1: every card stays readable in both themes. The agent card's run settings read light on light in dark
 * mode because React Flow wrote `light` on the canvas root, and Radix Themes redeclares every colour scale on
 * `.light`: a theme class that disagrees with the page's resets every token under it. Two checks, in light then dark:
 * no element from the card up to the page carries the other theme's class, and axe's `color-contrast` (WCAG 2 AA)
 * finds nothing in the cards.
 */
import fs from 'fs';
import { createRequire } from 'module';
import type { Locator, Page } from '@playwright/test';
import { expect } from '@playwright/test';

export type Appearance = 'light' | 'dark';
export const THEMES: readonly Appearance[] = ['light', 'dark'];

/** The theme classes Radix Themes (and globals.css) redeclare the colour scales on. */
const THEME_CLASSES: Record<Appearance, readonly string[]> = {
  light: ['light', 'light-theme'],
  dark: ['dark', 'dark-theme'],
};

/**
 * Elements from `card` up to the page, and inside it, that carry the theme class `pageTheme` is not. Self-contained
 * (Playwright serialises it into the page; vitest runs it on jsdom). A nested Radix `Theme` with a fixed appearance
 * counts too, unless it is marked `data-fixed-theme` (a terminal that is dark in both themes on purpose): what it
 * holds is then left to axe.
 */
export function themeClassLeaks(card: Element, pageTheme: Appearance): string[] {
  const other = pageTheme === 'dark' ? ['light', 'light-theme'] : ['dark', 'dark-theme'];
  const leaks: string[] = [];
  const name = (el: Element): string => {
    const cls = Array.from(el.classList).slice(0, 3).join('.');
    return `<${el.tagName.toLowerCase()}${cls ? `.${cls}` : ''}>`;
  };
  const check = (el: Element) => {
    if (el.tagName === 'HTML' || el.closest('[data-fixed-theme]')) return;
    if (other.some((c) => el.classList.contains(c))) leaks.push(name(el));
  };
  for (let el: Element | null = card; el; el = el.parentElement) check(el);
  for (const el of Array.from(card.querySelectorAll('*'))) check(el);
  return leaks;
}

const AXE_SOURCE = fs.readFileSync(createRequire(__filename).resolve('axe-core/axe.min.js'), 'utf8');
const MARK = 'data-r26-contrast';

export type ContrastProblem = { what: string; why: string };

/**
 * Waits until the theme switch's CSS transitions have run: axe reads the painted colours. Only transitions: a
 * spinner's endless animation never finishes and has nothing to do with the switch.
 */
async function settled(page: Page): Promise<void> {
  await page.waitForFunction(
    () => document.getAnimations().every((a) => !(a instanceof CSSTransition) || a.playState !== 'running'),
    null,
    { timeout: 10_000 },
  );
}

/** axe's `color-contrast` over the marked cards, each failing node with its markup and axe's reason. */
async function contrastProblems(page: Page): Promise<ContrastProblem[]> {
  if (!(await page.evaluate(() => 'axe' in window))) await page.evaluate(AXE_SOURCE);
  return page.evaluate(async (mark) => {
    type AxeNode = { html: string; failureSummary?: string };
    type AxeResult = { violations: Array<{ nodes: AxeNode[] }> };
    const axe = (window as unknown as { axe: { run: (ctx: unknown, opts: unknown) => Promise<AxeResult> } }).axe;
    const result = await axe.run(
      { include: [[`[${mark}]`]] },
      { runOnly: { type: 'rule', values: ['color-contrast'] }, resultTypes: ['violations'] },
    );
    return result.violations.flatMap((v) =>
      v.nodes.slice(0, 20).map((n) => ({ what: n.html.slice(0, 160), why: (n.failureSummary ?? '').replace(/\s+/g, ' ').slice(0, 240) })),
    );
  }, MARK);
}

/**
 * Every card `cards` matches is readable in light and in dark at the current viewport; the page is put back to light.
 * The app follows the system theme in these specs (`followSystemTheme`), so emulating the scheme switches it. Both
 * themes are checked before anything is asserted, so one run names every fault.
 */
export async function expectCardsReadableInBothThemes(page: Page, cards: () => Locator, label: string): Promise<void> {
  const found: Record<string, unknown[]> = {};
  for (const theme of THEMES) {
    await page.emulateMedia({ colorScheme: theme });
    await expect(page.locator('html'), `${label}: the page is ${theme}`).toHaveClass(new RegExp(`\\b${THEME_CLASSES[theme][0]}\\b`));
    await settled(page);
    const all = cards();
    const count = await all.count();
    expect(count, `${label}: a card to check`).toBeGreaterThan(0);
    const leaks: string[] = [];
    for (let index = 0; index < count; index += 1) {
      leaks.push(...(await all.nth(index).evaluate(themeClassLeaks, theme)));
      await all.nth(index).evaluate((el, mark) => el.setAttribute(mark, ''), MARK);
    }
    try {
      const contrast = await contrastProblems(page);
      if (leaks.length > 0) found[`${theme}: a ${theme === 'dark' ? 'light' : 'dark'} theme class`] = leaks;
      if (contrast.length > 0) found[`${theme}: WCAG AA contrast`] = contrast;
    } finally {
      await page.evaluate((mark) => document.querySelectorAll(`[${mark}]`).forEach((el) => el.removeAttribute(mark)), MARK);
    }
  }
  await page.emulateMedia({ colorScheme: 'light' });
  expect(found, `${label}: every card readable in light and dark`).toEqual({});
}
