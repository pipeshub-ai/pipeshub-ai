/**
 * R25FIX-FU-1: nothing inside a card may run past the card's sides, and no text field in it may be squeezed too
 * narrow to read. A card is measured at every width a person uses (desktop, then phones down to 320 px), because a
 * card in a narrow container (the agent builder's 340 px node) breaks at desktop widths, where viewport breakpoints
 * lay its rows out side by side, while a page card breaks on a phone.
 */
import type { Locator, Page } from '@playwright/test';
import { expect } from '@playwright/test';

export type FitProblem = { kind: 'overflow' | 'squeezed'; what: string; by: number };

/** Narrower than this (layout px), a text field shows a letter or two of its value or placeholder. */
export const MIN_FIELD_PX = 64;

export const FIT_WIDTHS = [
  { width: 1280, height: 900 },
  { width: 428, height: 926 },
  { width: 375, height: 812 },
  { width: 320, height: 700 },
] as const;

/**
 * What in `card` runs past its sides or is squeezed, measured in the page. Self-contained (no imports, no closures)
 * because Playwright serialises it into the browser; vitest runs the same function on jsdom with stubbed boxes.
 * What a scroller or a clipped box inside the card holds cannot be seen past it, so only the box itself is measured
 * for overflow (its fields still for squeezing). Only the outermost element that overflows is named.
 */
export function cardFitProblems(card: Element, minFieldPx: number): FitProblem[] {
  const win = card.ownerDocument.defaultView;
  if (!win) return [];
  const problems: FitProblem[] = [];
  const shown = (el: Element): boolean => {
    const style = win.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  // Radix's form inputs behind a checkbox, radio or select (aria-hidden, moved off its control) and React Flow's
  // connection handles, which sit on the card's edge by design.
  const ignored = (el: Element): boolean =>
    (el.tagName === 'INPUT' && el.getAttribute('aria-hidden') === 'true') ||
    (el.tagName === 'SELECT' && el.getAttribute('aria-hidden') === 'true') ||
    el.classList.contains('react-flow__handle');
  const clips = (el: Element): boolean => {
    const style = win.getComputedStyle(el);
    return ['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX);
  };
  const describe = (el: Element): string => {
    const name = el.getAttribute('aria-label') ?? el.getAttribute('placeholder') ?? el.getAttribute('data-testid');
    const text = (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40);
    return `<${el.tagName.toLowerCase()}>${name ? ` "${name}"` : ''}${text ? ` ${text}` : ''}`;
  };
  const bound = card.getBoundingClientRect();
  // `settled`: an ancestor already overflowed (it is the one named) or clips what it holds.
  const walk = (el: Element, settled: boolean): void => {
    for (const child of Array.from(el.children)) {
      if (ignored(child) || !shown(child)) continue;
      const box = child.getBoundingClientRect();
      const by = Math.max(box.right - bound.right, bound.left - box.left);
      const over = !settled && by > 1;
      if (over) problems.push({ kind: 'overflow', what: describe(child), by: Math.round(by) });
      const field =
        (child.tagName === 'INPUT' && !['checkbox', 'radio', 'hidden', 'range'].includes((child as HTMLInputElement).type)) ||
        child.tagName === 'TEXTAREA';
      const layoutWidth = (child as HTMLElement).offsetWidth;
      if (field && layoutWidth > 0 && layoutWidth < minFieldPx) {
        problems.push({ kind: 'squeezed', what: describe(child), by: Math.round(minFieldPx - layoutWidth) });
      }
      walk(child, settled || over || clips(child));
    }
  };
  walk(card, false);
  return problems;
}

/** Every card `cards` matches fits at the current viewport. */
export async function expectCardsFit(cards: Locator, label: string): Promise<void> {
  const count = await cards.count();
  expect(count, `${label}: a card to measure`).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const problems = await cards.nth(index).evaluate(cardFitProblems, MIN_FIELD_PX);
    expect(problems, `${label} (card ${index + 1} of ${count}) fits`).toEqual([]);
  }
}

/** `cards` fit at desktop and at phone widths down to 320 px; the viewport is put back to the desktop one after. */
export async function expectCardsFitAtEveryWidth(page: Page, cards: () => Locator, label: string): Promise<void> {
  for (const size of FIT_WIDTHS) {
    await page.setViewportSize(size);
    // One frame for the layout of the new width (no transition is involved: sizes, not colours, change).
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expectCardsFit(cards(), `${label} at ${size.width} px`);
  }
  await page.setViewportSize(FIT_WIDTHS[0]);
}

/** The agent builder's cards on the canvas (the agent card and the blocks beside it). */
export const flowCards = (page: Page): Locator => page.locator('.flow-node-surface');

/** The workspace settings pages' cards (`SettingsSection`). */
export const settingsCards = (page: Page): Locator => page.getByTestId('settings-section');
