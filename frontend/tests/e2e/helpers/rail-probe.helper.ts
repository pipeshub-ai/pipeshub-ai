/**
 * Measures what the chat rail (the app's sidebar slot) does during one action: whether it was rebuilt, which list
 * requests it made, how long a skeleton or an empty list showed, which rows survived as the same DOM nodes, where its
 * scroll ended up and how much it shifted.
 *
 * The rail's chat lists are answered here from a small in-memory list (FakeChatLists), so a run is the same on any
 * stack, can be slowed down to make every frame visible, and changes nothing on the server: a write the spec makes to
 * one of these chats is answered here too, and any other write to the API is refused.
 */
import type { Page, Route } from '@playwright/test';

/** What `measure` reports for one action. */
export interface RailMeasure {
  /** The rail's root element was replaced: the whole rail was built again. */
  railRebuilt: boolean;
  /** Frames with a list skeleton in the rail, in ms. */
  skeletonMs: number;
  /** Frames with a list skeleton in the More chats panel, in ms. */
  panelSkeletonMs: number;
  /** Frames with no chat row (or no rail) in ms. */
  emptyMs: number;
  /** Frames where the footer's org name was blank, in ms. */
  footerBlankMs: number;
  /** Rows shown before and after the action that stayed the very same element. */
  rowsKept: number;
  /** Rows shown both before and after the action. */
  rowsComparable: number;
  scrollBefore: number;
  scrollAfter: number;
  /**
   * Lowest scroll position of the rail on screen at the end, over the action: a jump to the top and back, or a rail
   * that painted at the top before scrolling back, shows here. Another page's rail does not count.
   */
  scrollLowest: number;
  /** Layout shift of rail elements, user input excluded. */
  railShift: number;
  /** Whether the More chats panel was open after the action. */
  panelOpen: boolean;
  /** List requests made during the action: rail lists, the More panel, and the org and its logo. */
  requests: { owned: number; shared: number; more: number; org: number; logo: number; agentChats: number };
}

type ProbeWindow = Window & {
  __railProbe?: {
    mark(): { rows: number; scrollTop: number; kid: number; panel: number };
    collect(t0: number): Omit<RailMeasure, 'requests' | 'scrollBefore'> & { lastChange: number };
    lastChange(): number;
    scrollRail(y: number): number;
  };
};

/** Runs in the page before any app code (addInitScript): it must not reference anything outside itself. */
function railProbe() {
  const w = window as ProbeWindow;
  if (w.__railProbe) return;
  type Frame = [
    number,
    { kid: number; rows: number; skel: number; pskel: number; panel: number; st: number; foot: string; empty: boolean },
  ];
  const frames: Frame[] = [];
  const shifts: Array<{ t: number; v: number; input: boolean; inRail: boolean }> = [];
  let lastChange = 0;
  let kidSeq = 0;
  let marks: { kid: Element | null; rows: Map<string, Element> } = { kid: null, rows: new Map() };
  const ids = new WeakMap<Element, number>();
  const slot = () => document.querySelector('[data-ph-sidebar-slot]');
  const primary = () => {
    const kid = slot()?.firstElementChild ?? null;
    if (!kid) return { kid: null, primary: null, secondary: null };
    const divs = [...kid.children].filter((e) => e.tagName === 'DIV') as HTMLElement[];
    const first = divs[0] ?? null;
    const secondary = divs.find((e) => e !== first && e.style.position === 'absolute') ?? null;
    return { kid, primary: first, secondary };
  };
  const rowsIn = (el: Element | null) => (el ? [...el.querySelectorAll('a[href*="conversationId="]')] : []);
  const rowId = (a: Element) => /conversationId=([^&]+)/.exec(a.getAttribute('href') ?? '')?.[1] ?? '';
  const scroller = (el: Element | null) => {
    if (!el) return null;
    const list = [...el.querySelectorAll('.no-scrollbar')].filter((e) => {
      const y = getComputedStyle(e).overflowY;
      return y === 'auto' || y === 'scroll';
    });
    // The rail's own list scroller is the tallest one.
    list.sort((a, b) => b.scrollHeight - a.scrollHeight);
    return (list[0] as HTMLElement | undefined) ?? null;
  };
  const state = () => {
    const { kid, primary: p, secondary } = primary();
    if (!kid) return { kid: 0, rows: 0, skel: 0, pskel: 0, panel: 0, st: -1, foot: '', empty: true };
    if (!ids.has(kid)) ids.set(kid, ++kidSeq);
    const rows = rowsIn(p);
    const buttons = p ? p.querySelectorAll('button') : [];
    const sc = scroller(p);
    return {
      kid: ids.get(kid) ?? 0,
      rows: rows.length,
      skel: p ? p.querySelectorAll('[data-list-skeleton]').length : 0,
      pskel: secondary ? secondary.querySelectorAll('[data-list-skeleton]').length : 0,
      panel: secondary ? 1 : 0,
      st: sc ? Math.round(sc.scrollTop) : -1,
      foot: buttons.length ? (buttons[buttons.length - 1].textContent ?? '').trim().slice(0, 30) : '',
      empty: rows.length === 0,
    };
  };
  let last = '';
  const sample = () => {
    try {
      const st = state();
      const key = JSON.stringify(st);
      if (key !== last) {
        const prev = last ? JSON.parse(last) : null;
        frames.push([Date.now(), st]);
        if (frames.length > 20000) frames.splice(0, 5000);
        // A scroll alone is not the rail changing.
        const onlyScroll = prev && Object.keys(st).every((k) => k === 'st' || (st as Record<string, unknown>)[k] === prev[k]);
        if (!onlyScroll) lastChange = Date.now();
        last = key;
      }
    } catch {
      // The page is between documents.
    }
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
  const watchMutations = () =>
    new MutationObserver((list) => {
      const s = slot();
      if (s && list.some((m) => s === m.target || s.contains(m.target))) lastChange = Date.now();
    }).observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  if (document.documentElement) watchMutations();
  else document.addEventListener('DOMContentLoaded', watchMutations);
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as Array<PerformanceEntry & { value: number; hadRecentInput: boolean; sources?: Array<{ node?: Node }> }>) {
        const s = slot();
        const inRail = (entry.sources ?? []).some((x) => !!(s && x.node && (s === x.node || s.contains(x.node))));
        shifts.push({ t: Math.round(performance.timeOrigin + entry.startTime), v: entry.value, input: entry.hadRecentInput, inRail });
      }
    }).observe({ type: 'layout-shift', buffered: true });
  } catch {
    // No layout-shift entries in this browser.
  }
  w.__railProbe = {
    mark() {
      const { kid, primary: p } = primary();
      marks = { kid, rows: new Map(rowsIn(p).map((a) => [rowId(a), a])) };
      const st = state();
      return { rows: st.rows, scrollTop: st.st, kid: st.kid, panel: st.panel };
    },
    lastChange: () => lastChange,
    scrollRail(y: number) {
      const sc = scroller(primary().primary);
      if (sc) sc.scrollTop = y;
      return sc ? sc.scrollTop : -1;
    },
    collect(t0: number) {
      const end = Date.now();
      const seq = frames.filter((f) => f[0] < t0).slice(-1).concat(frames.filter((f) => f[0] >= t0));
      const time = (hit: (s: Frame[1]) => boolean) => {
        let total = 0;
        for (let i = 0; i < seq.length; i++) {
          const a = Math.max(seq[i][0], t0);
          const b = i + 1 < seq.length ? seq[i + 1][0] : end;
          if (hit(seq[i][1]) && b > a) total += b - a;
        }
        return Math.round(total);
      };
      const { primary: p } = primary();
      const now = new Map(rowsIn(p).map((a) => [rowId(a), a]));
      let rowsKept = 0;
      let rowsComparable = 0;
      for (const [id, el] of marks.rows) {
        if (!now.has(id)) continue;
        rowsComparable++;
        if (el.isConnected && now.get(id) === el) rowsKept++;
      }
      const cur = state();
      const scrolls = seq.filter((f) => f[0] >= t0 && f[1].kid === cur.kid && f[1].st >= 0).map((f) => f[1].st);
      return {
        railRebuilt: marks.kid ? !marks.kid.isConnected : false,
        skeletonMs: time((s) => s.skel > 0),
        panelSkeletonMs: time((s) => s.pskel > 0),
        emptyMs: time((s) => s.empty),
        footerBlankMs: time((s) => s.kid > 0 && !/\w{3,}/.test(s.foot)),
        rowsKept,
        rowsComparable,
        scrollAfter: cur.st,
        scrollLowest: scrolls.length ? Math.min(...scrolls) : cur.st,
        railShift: Math.round(shifts.filter((x) => x.t >= t0 && x.inRail && !x.input).reduce((a, x) => a + x.v, 0) * 10000) / 10000,
        panelOpen: cur.panel === 1,
        lastChange,
      };
    },
  };
}

export interface FakeRow {
  _id: string;
  title: string;
  isPinned?: boolean;
  lastActivityAt: number;
}

/** Ids of the chats FakeChatLists makes up; nothing else may be written to. */
export const FAKE_ID_PREFIX = 'e2erail';

const PAGE_RX = /\/api\/v1\/conversations\?/;
const WRITE_RX = /\/api\/v1\/conversations\/([^/?]+)(?:\/(pin|unpin|title|archive|unarchive))?(?:\?|$)/;
/**
 * A live update from the server: a Socket.IO event frame (Engine.IO message 4, then event 2 or binary event 5).
 * Pings, pongs, connects and acks are not updates.
 */
const PUSH_RX = /^4[25]/;
/** A list read this soon after a push may be the app answering it (at once, after a burst, or after a load). */
const PUSH_READ_MS = 1500;

/**
 * The rail's chat lists, answered in the browser: owned and shared chats, paged and searched as the server does,
 * pinned first, and writable by the actions the spec takes (pin, rename, archive, restore, delete).
 */
export class FakeChatLists {
  owned: FakeRow[];
  shared: FakeRow[];
  private archived = new Map<string, FakeRow>();
  /** Added to every list answer, as a slow network would. */
  delayMs = 0;
  readonly requests: Array<{ t: number; kind: 'owned' | 'shared' | 'more' | 'write'; done: boolean }> = [];
  /** When the page started loading a document, and when the server pushed a live update. */
  private readonly loads: number[] = [];
  private readonly pushes: number[] = [];

  constructor({ owned, pinned, shared }: { owned: number; pinned: number; shared: number }) {
    const now = Date.now();
    const make = (prefix: string, n: number, extra: Partial<FakeRow> = {}) =>
      Array.from({ length: n }, (_, i) => ({
        _id: `${FAKE_ID_PREFIX}${prefix}${String(i + 1).padStart(2, '0')}`,
        title: `Rail check ${prefix} ${i + 1}`,
        lastActivityAt: now - i * 60_000,
        ...extra,
      }));
    this.owned = [...make('p', pinned, { isPinned: true }), ...make('o', owned)];
    this.shared = make('s', shared);
  }

  async install(page: Page): Promise<void> {
    // Routes run last-registered first: the guard is the fallback for every other API write. Looking people up by
    // id is a read sent as a POST.
    await page.route(/\/api\//, (route) => {
      const request = route.request();
      const read = request.method() === 'GET' || /\/api\/v1\/users\/by-ids/.test(request.url());
      return read ? route.fallback() : route.abort();
    });
    await page.route(PAGE_RX, (route) => this.answerList(route));
    await page.route(WRITE_RX, (route) => this.answerWrite(route));
    // The signed-in user is real and the stack is shared, so the server can push live updates mid-run, and the app may
    // read a list again to answer one. That read answers the push, not the action measured (count leaves it out).
    page.on('request', (r) => {
      if (r.isNavigationRequest() && r.frame() === page.mainFrame()) this.loads.push(Date.now());
    });
    page.on('websocket', (ws) => {
      if (!ws.url().includes('/socket.io/')) return;
      ws.on('framereceived', ({ payload }) => {
        if (typeof payload === 'string' && PUSH_RX.test(payload)) this.pushes.push(Date.now());
      });
    });
  }

  /**
   * Requests of one kind since `t0`. A list read that may answer a push is left out: each push that came once the
   * document on screen had asked for both lists (until then the app leaves it to that load) excuses the first read of
   * each list that starts within PUSH_READ_MS after it on that document, and no more. Every other read counts.
   */
  count(kind: 'owned' | 'shared' | 'more' | 'write', t0: number): number {
    const all = this.requests.filter((r) => r.kind === kind);
    const excused = new Set<(typeof all)[number]>();
    for (const p of kind === 'owned' || kind === 'shared' ? this.pushes : []) {
      const loaded = Math.max(0, ...this.loads.filter((l) => l <= p));
      const asked = (k: string) => this.requests.some((r) => r.kind === k && r.t >= loaded && r.t < p);
      if (!asked('owned') || !asked('shared')) continue;
      const read = all.find((r) => !excused.has(r) && r.t >= p && r.t - p <= PUSH_READ_MS && !this.loads.some((l) => l > p && l <= r.t));
      if (read) excused.add(read);
    }
    const left = all.filter((r) => r.t >= t0 && excused.has(r)).length;
    if (left) console.log(`rail probe: left out ${left} ${kind} list read(s) that followed a live update`);
    return all.filter((r) => r.t >= t0 && !excused.has(r)).length;
  }

  pending(): number {
    return this.requests.filter((r) => !r.done).length;
  }

  private async answerList(route: Route): Promise<void> {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') return route.fallback();
    const source = url.searchParams.get('source') === 'shared' ? 'shared' : 'owned';
    const page = Number(url.searchParams.get('page') ?? '1');
    const limit = Number(url.searchParams.get('limit') ?? '20');
    const search = (url.searchParams.get('search') ?? '').toLowerCase();
    // The rail asks for 10; the More panel for 20, or for a search.
    const kind = search || limit !== 10 ? 'more' : source;
    const entry = { t: Date.now(), kind: kind as 'owned' | 'shared' | 'more', done: false };
    this.requests.push(entry);
    try {
      if (this.delayMs) await new Promise((resolve) => setTimeout(resolve, this.delayMs));
      const all = (source === 'shared' ? this.shared : this.owned)
        .filter((r) => !search || r.title.toLowerCase().includes(search))
        .sort((a, b) => Number(b.isPinned === true) - Number(a.isPinned === true) || b.lastActivityAt - a.lastActivityAt);
      const rows = all.slice((page - 1) * limit, page * limit);
      const totalPages = Math.max(1, Math.ceil(all.length / limit));
      await route.fulfill({
        json: {
          source,
          conversations: rows.map((r) => this.apiRow(r, source === 'owned')),
          pagination: { page, limit, totalCount: all.length, totalPages, hasNextPage: page < totalPages, hasPrevPage: page > 1 },
        },
      });
    } finally {
      entry.done = true;
    }
  }

  private async answerWrite(route: Route): Promise<void> {
    const request = route.request();
    if (request.method() === 'GET') return route.fallback();
    const [, id, action] = WRITE_RX.exec(new URL(request.url()).pathname) ?? [];
    if (!id?.startsWith(FAKE_ID_PREFIX)) return route.abort();
    const entry = { t: Date.now(), kind: 'write' as const, done: false };
    this.requests.push(entry);
    const row = [...this.owned, ...this.shared].find((r) => r._id === id);
    if (action === 'pin' && row) row.isPinned = true;
    if (action === 'unpin' && row) delete row.isPinned;
    if (action === 'title' && row) row.title = (request.postDataJSON() as { title: string }).title;
    if ((action === 'archive' || (!action && request.method() === 'DELETE')) && row) {
      if (action === 'archive') this.archived.set(id, row);
      this.owned = this.owned.filter((r) => r._id !== id);
      this.shared = this.shared.filter((r) => r._id !== id);
    }
    if (action === 'unarchive' && this.archived.has(id)) {
      this.owned.push(this.archived.get(id)!);
      this.archived.delete(id);
    }
    entry.done = true;
    await route.fulfill({ json: { success: true } });
  }

  private apiRow(r: FakeRow, own: boolean) {
    return {
      _id: r._id,
      title: r.title,
      userId: 'e2e-owner',
      orgId: 'e2e-org',
      initiator: 'e2e-owner',
      isShared: !own,
      isDeleted: false,
      isArchived: false,
      lastActivityAt: r.lastActivityAt,
      status: 'complete',
      sharedWith: [],
      createdAt: new Date(r.lastActivityAt).toISOString(),
      updatedAt: new Date(r.lastActivityAt).toISOString(),
      isOwner: own,
      accessLevel: own ? 'owner' : 'read',
      ...(own && r.isPinned ? { isPinned: true } : {}),
      ...(own ? {} : { sharedBy: { name: 'E2E Teammate' } }),
    };
  }
}

/** The probe runs in every document the page opens, from before the app's own code. */
export async function installRailProbe(page: Page): Promise<void> {
  await page.addInitScript(railProbe);
}

/** Until the rail has not changed for `quietMs` and no list request is out. */
export async function waitForRailToSettle(page: Page, lists: FakeChatLists, quietMs = 1500, maxMs = 20_000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const last = await page.evaluate(() => (window as ProbeWindow).__railProbe?.lastChange() ?? 0).catch(() => Date.now());
    if (lists.pending() === 0 && Date.now() - Math.max(last, start) >= quietMs) return;
    await page.waitForTimeout(100);
  }
}

/** Scrolls the rail's list to `y` and gives the scroll a moment to land. */
export async function scrollRail(page: Page, y: number): Promise<number> {
  const at = await page.evaluate((top) => (window as ProbeWindow).__railProbe!.scrollRail(top), y);
  await page.waitForTimeout(200);
  return at;
}

/** Runs `action` and reports what the rail did until it settled. */
export async function measure(
  page: Page,
  lists: FakeChatLists,
  action: () => Promise<unknown>,
  { quietMs = 1500 }: { quietMs?: number } = {},
): Promise<RailMeasure> {
  const before = await page.evaluate(() => (window as ProbeWindow).__railProbe!.mark());
  const counted: Array<{ t: number; url: string }> = [];
  const onRequest = (r: { url(): string }) => counted.push({ t: Date.now(), url: r.url() });
  page.on('request', onRequest);
  const t0 = Date.now();
  try {
    await action();
    await waitForRailToSettle(page, lists, quietMs);
  } finally {
    page.off('request', onRequest);
  }
  const after = await page.evaluate((t) => (window as ProbeWindow).__railProbe!.collect(t), t0);
  const paths = counted.filter((r) => r.t >= t0).map((r) => new URL(r.url).pathname);
  return {
    ...after,
    scrollBefore: before.scrollTop,
    requests: {
      owned: lists.count('owned', t0),
      shared: lists.count('shared', t0),
      more: lists.count('more', t0),
      org: paths.filter((p) => /^\/api\/v1\/org\/?$/.test(p)).length,
      logo: paths.filter((p) => /^\/api\/v1\/org\/logo\/?$/.test(p)).length,
      agentChats: paths.filter((p) => /^\/api\/v1\/agents\/[^/]+\/conversations\/?$/.test(p)).length,
    },
  };
}

/** A row link of the rail's main column. */
export function railRow(page: Page, id: string) {
  return page.locator(`[data-ph-sidebar-slot] a[href*="conversationId=${id}"]`).first();
}

/** A navigation row of the rail, by its label (New chat, Agents, All artifacts, Collections, …). */
export async function clickRailNav(page: Page, label: string): Promise<void> {
  await page.locator('[data-ph-sidebar-slot]').getByText(label, { exact: true }).first().click();
}
