/**
 * Recorded runs the adapters are tested on, read where they live (nothing copied):
 *   - contracts/harness/vectors/journals/*.frames.json and *.parts.json: journals projected to frames and to saved
 *     parts by the Node and Python contract suites;
 *   - backend/nodejs/apps/tests/contracts/golden/*.sse: what Node sends the browser for the legacy goldens;
 *   - app/(main)/chat/__tests__/fixtures/ctl4-9d1315d0.json: a real Codex engine run's saved answer.
 */
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import en from '@/lib/i18n/locales/en-US.json';
import type { Words } from '../words';

export const REPO = path.resolve(__dirname, '../../../..');
const JOURNALS = path.join(REPO, 'contracts/harness/vectors/journals');
const GOLDEN = path.join(REPO, 'backend/nodejs/apps/tests/contracts/golden');

const lookup = (key: string): unknown => key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], en);

/** The en-US catalogue as `t`, with i18next's English plurals (`_one` / `_other` by `count`). */
export const words: Words = (key, vars = {}) => {
  let raw = lookup(key);
  if (raw === undefined && typeof vars.count === 'number') raw = lookup(`${key}_${vars.count === 1 ? 'one' : 'other'}`);
  if (typeof raw !== 'string') throw new Error(`missing en-US key ${key}`);
  return raw.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(vars[name] ?? ''));
};

export function journalFrames(scenario: string): unknown[] {
  return (JSON.parse(readFileSync(path.join(JOURNALS, `${scenario}.frames.json`), 'utf8')) as { frames: unknown[] }).frames;
}

export function journalParts(scenario: string): { parts: never[]; answer?: string } {
  return JSON.parse(readFileSync(path.join(JOURNALS, `${scenario}.parts.json`), 'utf8'));
}

export const frameScenarios = (): string[] =>
  readdirSync(JOURNALS).filter((f) => f.endsWith('.frames.json')).map((f) => f.replace('.frames.json', ''));

export const partScenarios = (): string[] =>
  readdirSync(JOURNALS).filter((f) => f.endsWith('.parts.json')).map((f) => f.replace('.parts.json', ''));

export const goldenScenarios = (): string[] => readdirSync(GOLDEN).filter((f) => f.endsWith('.sse')).map((f) => f.replace('.sse', ''));

/** An SSE recording as the stream client hands its events over: `{ event, data }`. */
export function goldenEvents(scenario: string): Array<{ event: string; data: unknown }> {
  const text = readFileSync(path.join(GOLDEN, `${scenario}.sse`), 'utf8');
  return text
    .split(/\n\n+/)
    .map((block) => {
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      return event && data ? { event, data: JSON.parse(data) } : null;
    })
    .filter((e): e is { event: string; data: unknown } => e !== null);
}

export function ctl4(): { parts: Array<{ type: string; frames?: never[] }> } {
  return JSON.parse(readFileSync(path.join(REPO, 'frontend/app/(main)/chat/__tests__/fixtures/ctl4-9d1315d0.json'), 'utf8'));
}

const ID_OR_DATA = /[{}]|"\s*:|\b[0-9a-f]{8}-[0-9a-f]{4}-|\bix\.approve\.|\brun-[a-z0-9-]+\b|\bcall[-_][A-Za-z0-9]+\b/;

/** What the design forbids in a main surface: an id or raw JSON. */
export function plainText(text: string | undefined): boolean {
  return !text || !ID_OR_DATA.test(text);
}
