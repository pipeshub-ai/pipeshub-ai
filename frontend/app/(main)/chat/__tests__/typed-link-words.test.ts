/**
 * A link still being typed shows nothing of itself until it is whole, so no word shown while it streams later changes or
 * vanishes. Replayed through the stream handler as the server sends it: the text in 1-character and 1-4-character
 * pieces, the server's numbered copy arriving as the stream goes on, then the final one. After every event the shown
 * text is read as the reader reads it (a numbered citation is a chip, a whole link its label), and every word shown must
 * be the final text's word at that place (the last one may still be growing), with no bracket the final does not show.
 * The streams are QA round 8's link probe fixtures, numbered by the server's own labelling rules.
 */
import { describe, it, expect, vi } from 'vitest';
import { createAGUIEventHandler } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';
import type { SSEEvent } from '@/lib/api';

const frame = (type: string, fields: Record<string, unknown> = {}): SSEEvent => ({ event: type, data: { type, ...fields } });
const tidied = (normalizedAnswer: string, rawLength: number) =>
  frame('STATE_DELTA', {
    delta: [
      { op: 'replace', path: '/citations', value: [] },
      { op: 'replace', path: '/normalizedAnswer', value: normalizedAnswer },
      { op: 'replace', path: '/rawLength', value: rawLength },
    ],
  });

/** The stream as the server sends it: the model's text, and its numbered copy (`[at, text]`) as it catches up. */
interface Shape {
  raw: string;
  final: string;
  snaps: [number, string][];
}

const SHAPES: Record<string, Shape> = {
  // Two descriptive citations with nothing between them: their words must not show glued ("first rulesecond rule").
  glued: {
    raw: "Rules apply [first rule](ref8)[second rule](ref9) to every team.",
    final: "Rules apply first rule [8](https://h/record/r8/preview#blockIndex=8)second rule [9](https://h/record/r9/preview#blockIndex=9) to every team.",
    snaps: [[12, "Rules apply "], [23, "Rules apply [first rule"], [32, "Rules apply first rule [8](https://h/record/r8/preview#blockIndex=8)[s"], [34, "Rules apply first rule [8](https://h/record/r8/preview#blockIndex=8)[sec"], [45, "Rules apply first rule [8](https://h/record/r8/preview#blockIndex=8)[second rule](r"], [51, "Rules apply first rule [8](https://h/record/r8/preview#blockIndex=8)second rule [9](https://h/record/r9/preview#blockIndex=9) t"], [56, "Rules apply first rule [8](https://h/record/r8/preview#blockIndex=8)second rule [9](https://h/record/r9/preview#blockIndex=9) to eve"]],
  },
  // A space before the parenthesis: no link at all, so the brackets stay as text.
  spaceBeforeParen: {
    raw: "See [the guide] (https://example.com/guide) for details.",
    final: "See [the guide] (https://example.com/guide) for details.",
    snaps: [[12, "See [the gui"], [23, "See [the guide] (https:"], [34, "See [the guide] (https://example.c"], [45, "See [the guide] (https://example.com/guide) f"]],
  },
  // A target the line ends before closing: no link either.
  targetCutByNewline: {
    raw: "See [the guide](https://example.com/gui\nNext line of text here.",
    final: "See [the guide](https://example.com/gui\nNext line of text here.",
    snaps: [[12, "See [the gui"], [23, "See [the guide](https:/"], [34, "See [the guide](https://example.co"], [45, "See [the guide](https://example.com/gui\nNext "], [56, "See [the guide](https://example.com/gui\nNext line of tex"]],
  },
  // A descriptive label the server keeps as words.
  descriptive: {
    raw: "Code freeze starts ten days early. See [release process](ref22) for the steps to follow.",
    final: "Code freeze starts ten days early. See release process [22](https://h/record/r22/preview#blockIndex=22) for the steps to follow.",
    snaps: [[12, "Code freeze "], [23, "Code freeze starts ten "], [34, "Code freeze starts ten days early."], [45, "Code freeze starts ten days early. See [relea"], [56, "Code freeze starts ten days early. See [release process]"], [65, "Code freeze starts ten days early. See release process [22](https://h/record/r22/preview#blockIndex=22) f"], [67, "Code freeze starts ten days early. See release process [22](https://h/record/r22/preview#blockIndex=22) for"], [78, "Code freeze starts ten days early. See release process [22](https://h/record/r22/preview#blockIndex=22) for the steps "]],
  },
  // Two generic citations side by side: one group of chips.
  groupGeneric: {
    raw: "Rules apply [source](ref10) [source](ref11) to every team.",
    final: "Rules apply [10](https://h/record/r10/preview#blockIndex=10) [11](https://h/record/r11/preview#blockIndex=11) to every team.",
    snaps: [[12, "Rules apply "], [23, "Rules apply [source](re"], [29, "Rules apply [10](https://h/record/r10/preview#blockIndex=10) ["], [34, "Rules apply [10](https://h/record/r10/preview#blockIndex=10) [sourc"], [45, "Rules apply [10](https://h/record/r10/preview#blockIndex=10) [11](https://h/record/r11/preview#blockIndex=11) t"], [56, "Rules apply [10](https://h/record/r10/preview#blockIndex=10) [11](https://h/record/r11/preview#blockIndex=11) to every tea"]],
  },
  // A label that repeats the words just before it: dropped.
  echo: {
    raw: "Read the release guide [release guide](ref1) before you ship.",
    final: "Read the release guide [1](https://h/record/r1/preview#blockIndex=1) before you ship.",
    snaps: [[12, "Read the rel"], [23, "Read the release guide "], [34, "Read the release guide [release gu"], [45, "Read the release guide [1](https://h/record/r1/preview#blockIndex=1) "], [46, "Read the release guide [1](https://h/record/r1/preview#blockIndex=1) b"], [56, "Read the release guide [1](https://h/record/r1/preview#blockIndex=1) before you "]],
  },
};

const BADGE = /\[\d+\]\([^)]*\)/g;
const LINK = /\[([^\]]*)\]\(([^)]*)\)/g;
/** What the reader reads: a numbered citation is a chip (no words), a whole link its label. */
const words = (text: string): string[] => {
  const read = text.replace(BADGE, ' ').replace(LINK, (_m, label: string) => label).replace(/\s+/g, ' ').trim();
  return read ? read.split(' ') : [];
};
const brackets = (text: string): number => (text.replace(BADGE, ' ').replace(LINK, '').match(/[[\]]/g) ?? []).length;

/** Where each piece of the text ends: one character at a time, or 1-4 characters at a time from a seed. */
function cuts(length: number, seed: number | null): number[] {
  if (seed === null) return Array.from({ length }, (_, i) => i + 1);
  let x = seed;
  const out: number[] = [];
  for (let at = 0; at < length; ) {
    x = (x * 1103515245 + 12345) % 2147483648;
    at = Math.min(length, at + 1 + Math.floor((x / 2147483648) * 4));
    out.push(at);
  }
  return out;
}

/** Every text the reader is shown while `shape` streams in the given pieces, and the last one. */
function replay(shape: Shape, ends: number[]): { states: string[]; end: string } {
  const spies = {
    onConnected: vi.fn(), onStatus: vi.fn(), onChunk: vi.fn(), onComplete: vi.fn(), onArtifact: vi.fn(),
    onAskUserQuestion: vi.fn(), onCustomEvent: vi.fn(), onAnswerFinal: vi.fn(), onReasoning: vi.fn(), onParts: vi.fn(),
    onError: vi.fn(),
  };
  const handle = createAGUIEventHandler(spies as unknown as StreamMessageCallbacks);
  const shown = () => (spies.onChunk.mock.calls.at(-1)?.[0] as { accumulated?: string } | undefined)?.accumulated ?? '';
  const states: string[] = [];
  handle(frame('TEXT_MESSAGE_START'));
  let from = 0;
  let snap = 0;
  for (const at of ends) {
    handle(frame('TEXT_MESSAGE_CONTENT', { delta: shape.raw.slice(from, at) }));
    from = at;
    states.push(shown());
    for (; snap < shape.snaps.length && shape.snaps[snap][0] <= at; snap += 1) {
      handle(tidied(shape.snaps[snap][1], shape.snaps[snap][0]));
      states.push(shown());
    }
  }
  handle(tidied(shape.final, shape.raw.length));
  return { states, end: shown() };
}

describe('a link still being typed shows no words that later change or vanish', () => {
  for (const [name, shape] of Object.entries(SHAPES)) {
    for (const seed of [null, 3, 7, 11]) {
      it(`${name}, ${seed === null ? '1-character' : `1-4-character (seed ${seed})`} pieces`, () => {
        const { states, end } = replay(shape, cuts(shape.raw.length, seed));
        expect(end).toBe(shape.final);
        const final = words(end);
        for (const state of states) {
          const shownWords = words(state);
          // Every word shown is the final's word at that place; the last one may still be growing.
          shownWords.forEach((word, i) => {
            if (i < shownWords.length - 1) expect(`${word} @${i} in "${state}"`).toBe(`${final[i]} @${i} in "${state}"`);
            else expect((final[i] ?? '').startsWith(word), `"${word}" @${i} in "${state}"`).toBe(true);
          });
          expect(brackets(state), state).toBeLessThanOrEqual(brackets(end));
        }
      });
    }
  }
});
