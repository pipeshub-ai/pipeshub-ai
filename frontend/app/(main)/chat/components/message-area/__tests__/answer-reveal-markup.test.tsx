/**
 * No frame of a streaming answer draws markup as text. The answer is fed to `AnswerContent` as the chat feeds it while
 * it streams (all that arrived, repaired, citations made markers), and its own reveal draws a little more every frame.
 * Every frame is read from the DOM, at 60, 120 and 144 Hz, with the text arriving as the model sends it, in small
 * pieces, and in large bursts: no bare `[^1]`, no LaTeX source, no stray `**`, `*`, `_`, `~~`, `$$` or table pipe, no
 * paragraph drawn as a heading, no heading that is not the start of the answer's, and no empty heading, list item or
 * quote, for a frame. It ends drawn as the answer is.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import { repairStreamingMarkdown } from '../../../utils/repair-streaming-markdown';
import { processMarkdownContent } from '../../../utils/process-markdown-content';

const h = React.createElement;

// Each test streams whole answers through the real renderer, a frame at a time, in several arrival patterns; on a busy
// machine one can run past Vitest's 5 s default.
vi.setConfig({ testTimeout: 20_000 });

let now = 0;
let frames: FrameRequestCallback[] = [];
beforeEach(() => {
  now = 1000;
  frames = [];
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal('cancelAnimationFrame', () => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** The answers: each construct, written as models write them. Their prose holds none of the marks checked for. */
const ANSWERS: Record<string, string> = {
  // A footnote reference whose definition arrives in the same piece of text.
  footnote:
    'This is the first paragraph, ending with the requested footnote reference.[^1]\n\n[^1]: A short note about the source.\n\n' +
    'Two more paragraphs follow here, plain and short.\n\nAnd this is the last one before the block.\n\n' +
    '<details><summary>More</summary>Hidden text.</details>\n\n' +
    '$$\n\\text{Pass rate} = \\frac{\\text{checks passed}}{\\text{checks total}} \\times 100\\%\n$$\n\nOne closing sentence.',
  // A display formula right after a code block, with its body arriving whole.
  mixed:
    '## Release checks\n\n- Build the release branch\n- Run the checks\n- Sign off\n\n' +
    '| Step | Owner | Time |\n| --- | --- | --- |\n| Build | Release captain | Monday |\n| Checks | QA lead | Tuesday |\n\n' +
    '```python\nchecks_passed = 48\nprint(checks_passed)\n```\n\n' +
    '$$\n\\text{Pass rate} = \\frac{\\text{checks passed}}{\\text{checks total}} \\times 100\\%\n$$\n\n' +
    'A footnote closes it.[^2]\n\n[^2]: From the release handbook.',
  maths:
    'The share is \\(\\frac{a}{b}\\) for each team, and $$x^2 + y^2 = z^2$$ holds for the sides.\n\n' +
    '\\[\nE = m c^2\n\\]\n\nWith inline \\(\\sqrt{2}\\) and a display one:\n\n$$\n\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}\n$$\n\nDone.',
  table:
    '| Step | Owner |\n| --- | --- |\n| Build | Release captain |\n| Checks | QA lead |\n\nThen a line, and a list:\n1. First\n2. Second',
  emphasis:
    'This is *really quite important for everyone* and _also this one here_ and ~~not this older part~~, ' +
    'with **bold words in the middle** of it, and ***both at once here*** too.\n\n' +
    'Rules:\n- Code freeze begins ten days early\n- Staging lasts two full days\n\n' +
    'It takes ~2 hours, costs 5 * 3 dollars, and uses snake_case_names in plain prose.',
  // A heading or a list item whose first word opens bold, code or italic: drawn before it closes, the marker would show
  // alone, an empty heading or a paragraph turned into one.
  headingBoldFirst: 'Intro line here.\n\n## **Results** so far\nEvery check passed.\n\n### **Next** steps\n- Ship it on Tuesday',
  headingCodeFirst: 'Intro line here.\n\n## `deploy` command\nRun it.\n\n### `config.yaml` keys\nSet them.',
  headingItalicFirst: 'Intro line here.\n\n### *Next* steps\nShip it.\n\n## _Notes_ below\nRead them.',
  listBoldAfterParagraph: 'Items to note:\n- **First:** four parts\n- **Second:** more parts',
  // A heading or list item whose first word is a formula or strikethrough: its first mark alone would draw as text, a
  // heading reading "$", "\" or "~".
  headingFormulaFirst: 'Intro line here.\n\n## $$x^2$$ squared\nIt grows.\n\n### \\(a+b\\) sums\nAdd them.',
  headingStrikeFirst: 'Intro line here.\n\n## ~~Old~~ New plan\nShip it.',
  listFormulaFirst: 'Values to note:\n- $$x^2$$ grows fast\n- \\(y\\) stays',
  // A start tag or an entity first: the tag alone draws an empty heading or item, a part of the entity draws as text.
  headingTagFirst:
    'Intro line here.\n\n## <sup>1</sup> Results\nPassed.\n\n### &amp; notes\nRead.\n\nItems to note:\n- <b>First</b> item\n- <b>Second</b> item',
  // A quote mark alone under a paragraph draws an empty quote.
  quoteAfterParagraph: 'Remember this:\n> **Note:** keep it short\n\nThen go.',
  // A heading's marker drawn before its first word would show an empty heading for a frame.
  headings:
    'What the checks found so far, in short.\n\n## Results\n\nEvery check passed on its first run.\n\n' +
    '### Next steps\n\n- Ship it on Tuesday\n- Tell the team\n\n#### One more note\nThe freeze starts on Friday.',
};

/** A heading's words, without the anchor mark the answer draws after a title. */
const headingText = (el: Element) => (el.textContent ?? '').replace(/#$/, '');

/** What a frame draws that is markup, not the answer's text; empty when there is none. */
function rawMarkup(root: HTMLElement, finalHeadings: string[]): string[] {
  const box = root.querySelector('.answer-prose') as HTMLElement;
  const copy = box.cloneNode(true) as HTMLElement;
  const found: string[] = [];
  if (copy.querySelector('.katex-error')) found.push('katex-error');
  // Drawn formulas, code and tables hold these marks by right.
  for (const el of Array.from(copy.querySelectorAll('.katex, pre, code, table'))) el.remove();
  const text = copy.textContent ?? '';
  const marks: [string, RegExp][] = [
    ['footnote', /\[\^/],
    ['latex', /\\(?:frac|text|sum|sqrt|times|\(|\)|\[|\])/],
    ['$$', /\$\$/],
    ['**', /\*\*/],
    ['*', /\*\w|\w\*/],
    ['_', /(?:^|[^\w])_\w|\w_[^\w]/],
    ['~~', /~~/],
    ['|', /\|/],
    ['](', /\]\(/],
  ];
  for (const [name, re] of marks) if (re.test(text)) found.push(`${name}: …${text.slice(-60)}`);
  for (const heading of Array.from(box.querySelectorAll('h1, h2, h3, h4, h5, h6'))) {
    const t = headingText(heading);
    if (!t.trim() || !finalHeadings.some((f) => f.startsWith(t))) found.push(`heading: ${t}`);
  }
  // A list item or a quote drawn before its first word.
  for (const block of Array.from(box.querySelectorAll('li, blockquote'))) {
    if (!(block.textContent ?? '').trim()) found.push(`empty ${block.tagName.toLowerCase()}`);
  }
  return found;
}

const view = (content: string, isStreaming: boolean) =>
  h(Theme, null, h(AnswerContent, { content, isStreaming }));
/** What the chat hands the answer while it streams: all that arrived, repaired, its citations made markers. */
const asStreamed = (arrived: string) => processMarkdownContent(repairStreamingMarkdown(arrived));

/** Pieces of `text` as they arrive: [time in ms, characters so far]. */
type Arrivals = [number, number][];

function pieces(text: string, seed: number, size: [number, number], gap: [number, number]): Arrivals {
  let x = seed;
  const rand = () => {
    x = (x * 1103515245 + 12345) % 2147483648;
    return x / 2147483648;
  };
  const out: Arrivals = [];
  for (let at = 0, t = 0; at < text.length; ) {
    at = Math.min(text.length, at + size[0] + Math.floor(rand() * (size[1] - size[0] + 1)));
    t += gap[0] + rand() * (gap[1] - gap[0]);
    out.push([t, at]);
  }
  return out;
}

/** Streams `text` by `arrivals`, frames `1000 / hz` ms apart, then finishes; returns every frame's raw markup. */
function stream(text: string, arrivals: Arrivals, hz: number) {
  const final = render(view(processMarkdownContent(text), false)).container;
  const finalHeadings = Array.from(final.querySelectorAll('h1, h2, h3, h4, h5, h6')).map(headingText);
  const finalText = final.querySelector('.answer-prose')?.textContent ?? '';
  cleanup();

  const dt = 1000 / hz;
  const start = now;
  const { container, rerender } = render(view(asStreamed(text.slice(0, arrivals[0][1])), true));
  const bad: { frame: number; t: number; found: string[] }[] = [];
  let next = 1;
  let frame = 0;
  const lastArrival = arrivals[arrivals.length - 1][0];
  for (let t = dt; t <= lastArrival + 1000; t += dt) {
    while (next < arrivals.length && arrivals[next][0] <= t) {
      now = start + arrivals[next][0];
      rerender(view(asStreamed(text.slice(0, arrivals[next][1])), true));
      next += 1;
    }
    now = start + t;
    act(() => {
      const pending = frames;
      frames = [];
      for (const cb of pending) cb(now);
    });
    frame += 1;
    const found = rawMarkup(container, finalHeadings);
    if (found.length) bad.push({ frame, t: Math.round(t), found });
  }
  // The run ends: the saved answer replaces the streamed one, and what is left of the reveal finishes.
  rerender(view(processMarkdownContent(text), false));
  for (let i = 0; i < 30; i += 1) {
    now += dt;
    act(() => {
      const pending = frames;
      frames = [];
      for (const cb of pending) cb(now);
    });
    const found = rawMarkup(container, finalHeadings);
    if (found.length) bad.push({ frame: frame + i + 1, t: -1, found });
  }
  return { bad, drawn: container.querySelector('.answer-prose')?.textContent ?? '', finalText };
}

const RATES = [60, 120, 144];

describe('the reveal never draws markup as text, at any frame rate', () => {
  for (const hz of RATES) {
    it(`${hz} Hz: a footnote whose definition arrives with its reference draws no bare [^1]`, () => {
      const text = ANSWERS.footnote;
      const para = text.indexOf('[^1]');
      // The paragraph, then one piece holding the reference, its definition and the start of the note.
      const arrivals: Arrivals = [[0, para], [400, para + '[^1]\n\n[^1]: A short'.length], [800, text.length]];
      const { bad, drawn, finalText } = stream(text, arrivals, hz);
      expect(bad).toEqual([]);
      expect(drawn).toBe(finalText);
    });

    it(`${hz} Hz: a display formula whose body arrives whole never draws its LaTeX`, () => {
      const text = ANSWERS.mixed;
      const formula = text.indexOf('$$');
      const arrivals: Arrivals = [[0, formula], [300, text.indexOf('\n$$\n\nA footnote') + 4], [700, text.length]];
      const { bad, drawn, finalText } = stream(text, arrivals, hz);
      expect(bad).toEqual([]);
      expect(drawn).toBe(finalText);
    });

    it(`${hz} Hz: a line whose text so far is its marker and the first mark of markup draws nothing of it`, () => {
      // The text pauses right after that first mark: `## $`, `### \`, `## ~`, `- $`, `- \`, a start tag, part of an
      // entity, or a quote's `>`.
      for (const [text, mark] of [
        [ANSWERS.headingFormulaFirst, '## $'],
        [ANSWERS.headingFormulaFirst, '### \\'],
        [ANSWERS.headingStrikeFirst, '## ~'],
        [ANSWERS.listFormulaFirst, '- $'],
        [ANSWERS.listFormulaFirst, '- \\'],
        [ANSWERS.headingTagFirst, '## <sup>'],
        [ANSWERS.headingTagFirst, '### &am'],
        [ANSWERS.headingTagFirst, '- <b>'],
        [ANSWERS.quoteAfterParagraph, '>'],
        [ANSWERS.quoteAfterParagraph, '> '],
      ]) {
        const cut = text.indexOf(mark) + mark.length;
        const { bad, drawn, finalText } = stream(text, [[0, cut], [300, cut + 6], [600, text.length]], hz);
        expect({ mark, bad }).toEqual({ mark, bad: [] });
        expect(drawn).toBe(finalText);
      }
    });

    for (const [name, text] of Object.entries(ANSWERS)) {
      it(`${hz} Hz: "${name}", in small pieces and in bursts, draws no markup in any frame`, () => {
        for (const [seed, size, gap] of [
          [1, [2, 9], [8, 40]],
          [7, [3, 14], [15, 70]],
          [11, [40, 160], [60, 260]],
          [3, [1, 4], [5, 30]],
        ] as [number, [number, number], [number, number]][]) {
          const { bad, drawn, finalText } = stream(text, pieces(text, seed, size, gap), hz);
          expect({ seed, bad }).toEqual({ seed, bad: [] });
          expect(drawn).toBe(finalText);
        }
      });
    }
  }
});
