import { expect, type Page } from '@playwright/test';

type Fake = {
  persona: 'owner' | 'bob';
  revoked: Set<number>;
  outage: boolean;
  latencyMs: number;
  status: string;
  incomplete: boolean;
  hubs: string[];
  fileOf: (id: string) => number | undefined;
  visibleNeighbours: (id: string, direction?: string, includeTests?: boolean) => string[];
  canSeeId: (id: string, includeTests?: boolean) => boolean;
  idsInFile: (file: number) => number[] | string[];
};

/** Open the explorer at `path` with the fake backend fast, and any errors from the page collected. */
export async function openExplorer(
  page: Page,
  path = '/',
  init: { latencyMs?: number; persona?: 'owner' | 'bob'; outage?: boolean; dense?: boolean; status?: string } = {},
) {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    // The font and icon CDN are not reachable in CI; nothing else may be an error.
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  // The fake starts in this state, before the explorer's first request, so a link that resolves on load sees it.
  await page.addInitScript((state) => ((window as unknown as { __fakeInit: unknown }).__fakeInit = state), init);
  await page.goto(path);
  return { errors };
}

/** Run `fn` in the page with the fake backend (and one JSON-able argument). */
export const fake = <T, A = undefined>(page: Page, fn: (f: Fake, arg: A) => T, arg?: A): Promise<T> =>
  page.evaluate(`(${fn.toString()})(window.__fake, ${JSON.stringify(arg ?? null)})`) as Promise<T>;

/** The file (0 to 79) a block id belongs to: 25 blocks a file. */
export const fileOfId = (id: string) => Math.floor(Number(id.replace('block:b', '')) / 25);

/** The Cytoscape instance the engine keeps on its container (read only). */
const CY = `(() => { const d=[...document.querySelectorAll('div')].find(x=>x._cyreg); return d && d._cyreg.cy; })()`;

export const cyNodeIds = (page: Page): Promise<string[]> =>
  page.evaluate(`(() => { const cy=${CY}; return cy ? cy.nodes().map(n=>n.id()) : []; })()`) as Promise<string[]>;

export const cyEdgeCount = (page: Page): Promise<number> =>
  page.evaluate(`(() => { const cy=${CY}; return cy ? cy.edges().length : 0; })()`) as Promise<number>;

export const cySelected = (page: Page): Promise<string[]> =>
  page.evaluate(`(() => { const cy=${CY}; return cy ? cy.nodes(':selected').map(n=>n.id()) : []; })()`) as Promise<string[]>;

/** Where a node is drawn, in page pixels, for clicking exactly on it. */
export const cyNodePoint = (page: Page, id: string): Promise<{ x: number; y: number } | null> =>
  page.evaluate(
    `(() => { const cy=${CY}; if(!cy) return null; const n=cy.getElementById(${JSON.stringify(id)}); if(!n.length) return null;
      const r=cy.container().getBoundingClientRect(); const p=n.renderedPosition(); return { x: r.left+p.x, y: r.top+p.y }; })()`,
  ) as Promise<{ x: number; y: number } | null>;

export const urlParams = (page: Page) => new URL(page.url()).searchParams;

/** Wait until the picture holds at least `n` nodes. */
export const waitForNodes = (page: Page, n = 1) => expect.poll(async () => (await cyNodeIds(page)).length, { timeout: 15_000 }).toBeGreaterThanOrEqual(n);

/** The labels listed in the table view (switch to it first). */
export async function tableLabels(page: Page): Promise<string[]> {
  return page.getByRole('rowheader').evaluateAll((els) => els.map((e) => (e.textContent ?? '').replace(/Centre$/, '').trim()));
}

export const notices = (page: Page) => page.locator('[role=status], [role=alert]').allInnerTexts();

/** The direction control is a segmented control whose items read "Callers and callees", "Callers", "Callees". */
export const direction = (page: Page, which: 'any' | 'inbound' | 'outbound') =>
  page.locator('button[role=radio]').nth(['any', 'inbound', 'outbound'].indexOf(which));

/** No call is in flight: the picture's container is not marked busy. */
export const idle = (page: Page) => expect(page.locator('[aria-busy=true]')).toHaveCount(0);
