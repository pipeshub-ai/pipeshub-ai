import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import type { Browser, Locator, Page } from '@playwright/test';
import { KNOWN_EXCEPTIONS } from './a11y';
import { storageStateFor } from './two-users.fixture';
import { NodeApi, stackState, type Actor } from './stack';

/** Opt-in switch: the tour is a visual QA aid, not part of the gate. */
export const TOUR_ENABLED = process.env.PCC_VISUAL_TOUR === '1';

export const OUT_DIR = path.resolve('test-results', 'visual-tour');
const MANIFEST = path.join(OUT_DIR, '.manifest.jsonl');

export interface Combo {
  viewport: 'desktop' | 'mobile';
  theme: 'light' | 'dark';
  size: { width: number; height: number };
}

export const COMBOS: Combo[] = [
  { viewport: 'desktop', theme: 'light', size: { width: 1440, height: 900 } },
  { viewport: 'desktop', theme: 'dark', size: { width: 1440, height: 900 } },
  { viewport: 'mobile', theme: 'light', size: { width: 375, height: 812 } },
  { viewport: 'mobile', theme: 'dark', size: { width: 375, height: 812 } },
];

export interface Person {
  actor: Actor;
  page: Page;
  api: NodeApi;
}

/** The fixed cast. Display names come from the lane's seeded roster ("User Owner", "User Writer", ...). */
export const CAST = { alice: 'owner', bob: 'write_recipient', carol: 'read_recipient' } as const;
export type RosterKey = 'owner' | 'write_recipient' | 'read_recipient' | 'team_writer' | 'team_reader' | 'project_viewer' | 'project_editor' | 'stranger' | 'admin';

/** Browser contexts for roster users in one viewport and theme (the theme is the stored preference the app reads). */
export function makeCast(browser: Browser, combo: Combo) {
  const { roster } = stackState();
  const opened: { close: () => Promise<void> }[] = [];
  const cache = new Map<RosterKey, Person>();
  const open = async (key: RosterKey): Promise<Person> => {
    const hit = cache.get(key);
    if (hit) return hit;
    const actor = roster[key];
    const state = storageStateFor(actor);
    state.origins[0].localStorage.push({ name: 'pipeshub-theme-preference', value: combo.theme });
    const context = await browser.newContext({
      storageState: state,
      viewport: combo.size,
      colorScheme: combo.theme,
      locale: 'en-US',
      timezoneId: 'UTC',
      reducedMotion: 'reduce',
    });
    opened.push(context);
    // The Next dev-server badge would sit on every screenshot.
    await context.addInitScript(() => {
      const hide = () => {
        const s = document.createElement('style');
        s.textContent = 'nextjs-portal{display:none!important}';
        document.documentElement.appendChild(s);
      };
      if (document.documentElement) hide();
      else document.addEventListener('DOMContentLoaded', hide);
    });
    const person = { actor, page: await context.newPage(), api: new NodeApi(actor) };
    cache.set(key, person);
    return person;
  };
  return { open, close: () => Promise.all(opened.map((c) => c.close())) };
}

export type Cast = ReturnType<typeof makeCast>;

function record(entry: Record<string, unknown>) {
  mkdirSync(OUT_DIR, { recursive: true });
  appendFileSync(MANIFEST, JSON.stringify(entry) + '\n');
}

let current: Combo = COMBOS[0];
export const setCurrentCombo = (c: Combo) => {
  current = c;
};

/**
 * Saves the viewport as `<id>-<slug>--<viewport>--<theme>.png` and, when `el` is given, the element alone as
 * `<id>-<slug>-element--...`. `id` is the two-digit running number the INDEX lists.
 */
export async function shot(page: Page, id: string, slug: string, description: string, el?: Locator): Promise<void> {
  mkdirSync(OUT_DIR, { recursive: true });
  const suffix = `--${current.viewport}--${current.theme}.png`;
  await page.waitForTimeout(450);
  const base = `${id}-${slug}`;
  await page.screenshot({ path: path.join(OUT_DIR, base + suffix), fullPage: false, animations: 'disabled', caret: 'hide' });
  record({ file: base, description });
  await audit(page, `${base}${suffix.replace('.png', '')}`);
  if (el) {
    await el.first().screenshot({ path: path.join(OUT_DIR, `${base}-element${suffix}`), animations: 'disabled', caret: 'hide' });
    record({ file: `${base}-element`, description: `${description} (the element alone)` });
  }
}

/** A state the tour could not reach; it ends up in INDEX.md. */
export function unreachable(id: string, slug: string, reason: string) {
  record({ unreachable: `${id}-${slug}`, reason });
}

/** Observations about the UI worth the lead's attention; they end up in INDEX.md. */
export function observe(id: string, note: string) {
  record({ observation: id, note });
}

const STATE_MAP = [
  '1 solo chat, flag on: 01. 2 flag off: 02.',
  '3 share drawer as owner: 03 empty, 04 Bob and Carol selected with a note (one level for the batch, as the drawer works), 05 level menu, 06 members and settings, 07 row role menu, 08 settings on.',
  '4 org-wide confirm: 09; large-team confirm: 10 (member count injected into the team list because the fake returns none).',
  '5 summary view: 11, as Bob from a stale tab. Carol cannot open it: readers have no Share button.',
  '6 invite mode as Bob with Editors can invite: 12.',
  '7 author chips and answered-as labels: 13 (Bob\'s tab), 14 (Alice\'s tab).',
  '8 audience notice: 15; agent "Share tool results" checkbox: 16.',
  '9 busy banner: 17 (Alice streaming), 18 (Bob), 19 (Bob queued).',
  '10 conversation changed: 20. 11 read-only: 21. 12 access lost: 22.',
  '13 ask-user-question: 23 (Bob), 24 (Alice read-only), 25 (RESUME_NOT_ALLOWED toast).',
  '14 access panel: 26 (owner), 27 (owner looking at Bob, team path redacted), 28 (Bob), 29 (team member).',
  '15 access-change dialog: 30 visibility on, 31 visibility off, 32 preview error, 44 project link, 45 project unlink; 33 and 34 are the sidebar menu and Move to project dialog that lead to 44.',
  '16 sidebar: 35 badges and unread dot, 36 shared-chat menu, 37 Leave dialog.',
  '17 notifications: 38 all five types with a coalesced activity item, 39 mute button, 40 after muting. 18 profile preferences: 41.',
  '19 errors: 42 COLLABORATOR_LIMIT (real: 199 fake teams plus two people), 43 RATE_LIMITED (real: 22 calls inside a minute).',
];

const REVIEW_NOTES = [
  'FIXED, was blocking on mobile: SidebarBase called its hooks after an early return for isMobile ("Rendered fewer hooks than expected" at 375 px). The tour no longer patches useIsMobile in the browser.',
  'FIXED, 20 (mobile): the "N new messages above" notice and the busy banner give the text `flex: 1 1 12rem` so the buttons wrap below it on narrow screens (busy-banner.tsx).',
  'FIXED, 38-40 (mobile): the notifications panel is capped at the viewport (`maxWidth: calc(100vw - left offset)`), so Mark all as read, Filter and the row actions are reachable (panel.tsx).',
  'FIXED, 11 (summary drawer): the summary view no longer lists suggested members or teams or offers Create a New Team (share-sidebar.tsx).',
  'FIXED, 04/05/09/42: recipient chips wrap in the chat share drawer (share-search-input.tsx, only when the adapter passes roleOptions). Also fixed: the drawer suggests no disabled users, service accounts or owner; on phones it is full screen.',
  'NEEDS A PRODUCT DECISION, 35/36: in the 232 px sidebar the "Can continue" / "Can view" badge and the unread dot leave room for only about 12 characters of the chat title ("When is the..."). Moving the badge into the subtitle or shortening it contradicts the text-badge decision (UX-01), so it is left as is.',
  'NEEDS A PRODUCT DECISION, 39/40: a muted chat\'s notification row shows no marker; only the hover button changes to "Unmute this chat".',
  'ASSIGNED ELSEWHERE: the chat-header participant avatars overlap into one word ("UWJR") in 14, 26, 27, 30-32 and the other owner shots.',
  'NOT COLLABORATIVE CHATS, 41 (mobile): the profile page is squeezed (workspace nav column plus a very narrow settings column, labels wrapping per word, switches cut off). Existing workspace layout; the new section inherits it.',
  'NOT COLLABORATIVE CHATS, contrast (see the mechanical audit): the red Leave button (3.9:1 light, 3.3:1 dark) and the jade primary buttons (3.15:1) are theme-level; notification timestamps (2.1:1) and the sidebar group labels "Shared Chats", "Today" (3.7:1) predate the feature.',
  '06/08: the "Access shared" / "Role updated" toast lands on top of the drawer footer, over the Share button for about three seconds. Global toast placement.',
  '42/43: COLLABORATOR_LIMIT and RATE_LIMITED show as red inline text in the drawer, not as toasts (consistent with the drawer\'s other errors).',
  '03/04: "Suggested members" is capped at five users by design (the code says so), so Writer and Reader appear only once typed in the search box.',
  'FIXED, was 20: GET /api/v1/conversations/:id now returns `seq` and `author` per message with the flag on (same mapping as the feed), so a detail-only tab names the other people and sends baseSeq.',
  '45: a "Not Found" toast with a reference id shows on the project page behind the unlink dialog (not traced; probably a project call the fake connectors service does not implement).',
  'LANE ARTIFACTS: the fake connectors service returns no member count, so "Everyone at Acme" reads "0 members" (09); the chat "flag probe" that the lane creates shows in the owner\'s sidebar in 33/34/44/45; timestamps (for example "6:53 AM") are real time and differ between runs.',
];

export function writeIndex() {
  const lines = existsSync(MANIFEST) ? readFileSync(MANIFEST, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
  const pngs = readdirSync(OUT_DIR).filter((f) => f.endsWith('.png')).sort();
  const desc = new Map<string, string>();
  for (const l of lines) if (l.file && !desc.has(l.file)) desc.set(l.file, l.description);
  const out: string[] = ['# Collaborative chats: visual tour', '', `${pngs.length} screenshots. File names: \`<NN>-<slug>[-element]--<viewport>--<theme>.png\`; viewports desktop 1440x900 and mobile 375x812; themes light and dark.`, '', '| File | What it should show |', '| --- | --- |'];
  for (const f of pngs) {
    const key = f.replace(/--(desktop|mobile)--(light|dark)\.png$/, '');
    out.push(`| ${f} | ${desc.get(key) ?? ''} |`);
  }
  const skipped = new Map<string, string>();
  for (const l of lines) if (l.unreachable) skipped.set(l.unreachable, l.reason);
  out.push('', '## Where each requested state is', '');
  for (const row of STATE_MAP) out.push(`- ${row}`);
  out.push('', '## Not captured', '');
  if (skipped.size === 0) out.push('None.');
  for (const [k, v] of skipped) out.push(`- ${k}: ${v}`);
  const notes = new Map<string, string>();
  for (const l of lines) if (l.observation) notes.set(`${l.observation}: ${l.note}`, '');
  out.push('', '## Observations while capturing', '');
  if (notes.size === 0) out.push('None.');
  for (const k of notes.keys()) out.push(`- ${k}`);
  const audits = new Map<string, string[]>();
  for (const l of lines) if (l.audit) audits.set(l.note, [...(audits.get(l.note) ?? []), l.audit]);
  out.push('', '## Mechanical audit (raw i18n keys, horizontal overflow, cut-off controls, colour contrast)', '');
  if (audits.size === 0) out.push('Nothing found.');
  for (const [note, labels] of audits) out.push(`- ${note} [${[...new Set(labels)].slice(0, 4).join(', ')}${new Set(labels).size > 4 ? `, +${new Set(labels).size - 4} more` : ''}]`);
  out.push('', '## Review notes (from looking at every screenshot of the reference run)', '');
  for (const n of REVIEW_NOTES) out.push(`- ${n}`);
  writeFileSync(path.join(OUT_DIR, 'INDEX.md'), out.join('\n') + '\n');
}

/**
 * The collaboration routes allow 20 mutations a minute per user (in-process counter in the Node API). The tour runs
 * far more than that for the owner, so every test reserves what it is going to spend and waits for room first.
 */
const WINDOW_MS = 61_000;
const spent: { at: number; n: number }[] = [];
export async function reserveMutations(n: number, limit = 18): Promise<void> {
  for (;;) {
    const now = Date.now();
    while (spent.length && now - spent[0].at > WINDOW_MS) spent.shift();
    const used = spent.reduce((a, b) => a + b.n, 0);
    if (used + n <= limit) break;
    await new Promise((r) => setTimeout(r, 1_000));
  }
  spent.push({ at: Date.now(), n });
}

/** Waits until the owner's mutation window is empty (before the test that deliberately exhausts it), then books the whole window. */
export async function waitQuiet(): Promise<void> {
  for (;;) {
    const now = Date.now();
    while (spent.length && now - spent[0].at > WINDOW_MS) spent.shift();
    if (spent.length === 0) break;
    await new Promise((r) => setTimeout(r, 1_000));
  }
  spent.push({ at: Date.now(), n: 100 });
}

/** Called after a test has used up the owner's window on purpose: the next reservation waits for a full minute from now. */
export function markExhausted(): void {
  spent.push({ at: Date.now(), n: 100 });
}

/**
 * Mechanical findings for one screenshot, appended to the manifest: untranslated keys in the text, horizontal page
 * overflow, controls that stick out of the viewport, and colour-contrast violations that are not in KNOWN_EXCEPTIONS.
 */
async function audit(page: Page, label: string): Promise<void> {
  const found = await page.evaluate(() => {
    const out: string[] = [];
    const text = document.body.innerText;
    const raw = text.match(/\b(?:chat|notifications|nav|sidebar|common)\.[a-zA-Z]+(?:\.[a-zA-Z]+)+\b/g);
    if (raw) out.push(`raw i18n key in text: ${[...new Set(raw)].slice(0, 3).join(', ')}`);
    const root = document.documentElement;
    if (root.scrollWidth > window.innerWidth + 1) out.push(`page scrolls horizontally (${root.scrollWidth}px wide in a ${window.innerWidth}px viewport)`);
    const seen = new Set<string>();
    for (const el of document.querySelectorAll('button, a, input, textarea, [role="button"], [role="menuitem"], [role="switch"], [role="checkbox"], [data-testid]')) {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
      if (r.right > window.innerWidth + 1 || r.left < -1) {
        const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('data-testid') || el.tagName).trim().slice(0, 40);
        const key = `${el.tagName}:${name}`;
        if (!seen.has(key)) {
          seen.add(key);
          out.push(`${el.tagName.toLowerCase()} "${name}" is cut off by the viewport (left ${Math.round(r.left)}, right ${Math.round(r.right)}, viewport ${window.innerWidth})`);
        }
      }
    }
    return out.slice(0, 8);
  });
  for (const f of found) record({ audit: label, note: f });
  try {
    const { violations } = await new AxeBuilder({ page }).withRules(['color-contrast']).analyze();
    for (const v of violations) {
      for (const n of v.nodes) {
        if (KNOWN_EXCEPTIONS.some((e) => e.rule === v.id && n.html.includes(e.htmlIncludes))) continue;
        record({ audit: label, note: `contrast: ${n.any[0]?.message ?? v.help} -> ${n.target.join(' ').slice(0, 90)} ${n.html.slice(0, 90)}` });
      }
    }
  } catch {
    /* axe cannot run while a page is navigating; the next shot will catch it */
  }
}
