import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

/**
 * Recorded exceptions: a violation of `rule` on a node whose HTML contains `htmlIncludes`. Each one is outside the
 * collaborative-chats surface and says why it is left alone. The violation is still attached to the test as
 * `axe-<label>.json`; only the failure is waived.
 */
export const KNOWN_EXCEPTIONS: { rule: string; htmlIncludes: string; reason: string }[] = [
  {
    rule: 'color-contrast',
    htmlIncludes: 'No agents found',
    reason: 'App sidebar "My agents" empty state (slate-10 on near-white, 3.7:1). Not a collaborative-chats element; seen on every chat page.',
  },
  {
    rule: 'color-contrast',
    htmlIncludes: 'data-accent-color="amber" class="rt-reset rt-Badge rt-r-size-1 rt-variant-soft"',
    reason: 'Sidebar "Beta" chip on Projects and, since main #3427, My artifacts (amber-11 on amber-3 at 12 px, 4.19:1). App sidebar, not a collaborative-chats element; seen on every chat page. Fix on main (highContrast or a larger size).',
  },
];

const BLOCKING = new Set(['serious', 'critical']);

/** Axe scan of the page (or of `include`); fails on serious or critical violations that are not in `KNOWN_EXCEPTIONS`. */
export async function expectNoBlockingViolations(page: Page, label: string, include?: string): Promise<void> {
  // A dialog scanned mid fade-in reports blended colours (e.g. 2.2:1 on "Make owner"): let finite animations end first.
  await page.evaluate(() =>
    Promise.race([
      Promise.all(
        document
          .getAnimations()
          .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
          .map((a) => a.finished.catch(() => undefined)),
      ),
      new Promise((resolve) => setTimeout(resolve, 2_000)),
    ]),
  );
  let builder = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']);
  if (include) builder = builder.include(include);
  const { violations } = await builder.analyze();
  await test.info().attach(`axe-${label}.json`, {
    body: JSON.stringify(
      violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        help: v.help,
        nodes: v.nodes.map((n) => ({ target: n.target.join(' '), html: n.html.slice(0, 200), why: n.any[0]?.message })),
      })),
      null,
      2,
    ),
    contentType: 'application/json',
  });
  const blocking: string[] = [];
  for (const v of violations) {
    if (!BLOCKING.has(v.impact ?? '')) continue;
    for (const n of v.nodes) {
      if (KNOWN_EXCEPTIONS.some((e) => e.rule === v.id && n.html.includes(e.htmlIncludes))) continue;
      blocking.push(`${v.id} (${v.impact}): ${v.help} -> ${n.target.join(' ')} ${n.html.slice(0, 160)} ${n.any[0]?.message ?? ''}`);
    }
  }
  expect(blocking, `serious or critical accessibility violations on "${label}"`).toEqual([]);
}
