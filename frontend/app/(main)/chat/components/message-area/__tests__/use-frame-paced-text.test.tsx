import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, cleanup } from '@testing-library/react';
import { REVEAL_LAG_MS, revealEnd, useFramePacedText } from '../use-frame-paced-text';

describe('revealEnd', () => {
  const at = (text: string, want: number, floor = 0) => text.slice(0, revealEnd(text, want, floor));

  it('ends on a whole word', () => {
    expect(at('Code freeze begins ten days early.', 7)).toBe('Code freeze');
  });

  it('shows a citation chip or a link whole, never its bracket alone', () => {
    expect(at('Ships Tuesday [12] and more', 15)).toBe('Ships Tuesday [12]');
    expect(at('see [the docs](https://x.example/a) now', 6)).toBe('see [the docs](https://x.example/a)');
  });

  it('shows a run of citation chips whole, with the mark the answer draws in front of it, so a group never grows', () => {
    expect(at('Freeze rules [1][2][3]. Next', 14)).toBe('Freeze rules [1][2][3].');
    expect(at('Freeze rules [1] [2] [3] and more', 16)).toBe('Freeze rules [1] [2] [3]');
    // A run still short of its end stays unrevealed rather than shown in part.
    expect(at('Freeze rules [1] [2] [3] and more', 6)).toBe('Freeze');
  });

  it('stops before markup still being written, and never goes back below what is shown', () => {
    expect(at('Ships **on Tues', 9)).toBe('Ships ');
    expect(at('Ships **on Tues', 9, 8)).toBe('Ships **');
    expect(at('Use `npm ru', 6)).toBe('Use ');
  });

  it('draws a table row only whole', () => {
    expect(at('| a | b |\n| --- | --- |\n| one | two |\n| three', 30)).toBe('| a | b |\n| --- | --- |\n| one | two |');
  });

  it('takes a bracket a finished line never closed as plain text', () => {
    expect(at('The range [0, 10) is open.\nNext line here', 12)).toBe('The range [0, 10) is open.');
  });

  it('inside a code block only counts words', () => {
    expect(at('```js\nconst a = [1, 2', 16)).toBe('```js\nconst a = [1,');
  });

  it('stops before markup still being written at the very end of what arrived, too', () => {
    const text = 'Rollback uses **the previous';
    expect(at(text, text.length)).toBe('Rollback uses ');
    expect(at('The captain is **Sam Example**', 30)).toBe('The captain is **Sam Example**');
    // A mark at the end may still open: what comes next has not arrived.
    expect(at('Ships on time, **', 17)).toBe('Ships on time, ');
  });

  it('draws a formula only whole: past it once it has arrived, before it while it is written', () => {
    const block = 'Rate:\n\n$$\n\\text{Pass rate} = \\frac{a}{b}\n$$\n\nAfter.';
    // The whole block has arrived: a reveal that would stop inside it goes on past its closing `$$`.
    expect(at(block, block.indexOf('\\frac'))).toBe(block.slice(0, block.indexOf('$$\n\nAfter') + 2));
    expect(at(block.slice(0, block.indexOf('\\frac') + 6), 30)).toBe('Rate:\n\n');
    expect(at('The share is \\(\\frac{a}{b}\\) each.', 16)).toBe('The share is \\(\\frac{a}{b}\\)');
    expect(at('The share is \\(\\frac{a}{b} for', 30)).toBe('The share is ');
    expect(at('So $$x^2 + y^2$$ holds.', 6)).toBe('So $$x^2 + y^2$$');
    expect(at('\\[\nE = m c^2\n\\]\n\nNext', 3)).toBe('\\[\nE = m c^2\n\\]');
    // A paragraph that ended without closing it never held a formula.
    expect(at('Costs $$ more.\n\nNext line', 8)).toBe('Costs $$');
  });

  it("draws a table's first row only with the rule under it, which makes it a table", () => {
    expect(at('| a | b |\n|', 10)).toBe('');
    expect(at('| a | b |\n| --- |', 10)).toBe('');
    expect(at('| a | b |\n| --- | --- |\n| one', 4)).toBe('| a | b |\n| --- | --- |');
    expect(at('| a | b |\nplain line\n', 4)).toBe('| a | b |');
  });

  it('takes the whole of emphasis or strikethrough, and none of it while it is being written', () => {
    expect(at('This is *really quite* key', 12)).toBe('This is *really quite*');
    expect(at('This is *really quite', 12)).toBe('This is ');
    expect(at('Note _this one here_ now', 10)).toBe('Note _this one here_');
    expect(at('And ~~not this part~~ then', 8)).toBe('And ~~not this part~~');
    expect(at('And ***both at once*', 18)).toBe('And ');
    // Marks that open nothing: a lone star, a word's own underscores, "about".
    expect(at('It costs 5 * 3 and uses snake_case_names for ~2 hours', 52)).toBe('It costs 5 * 3 and uses snake_case_names for ~2 hours');
  });

  it('shows a list, heading or footnote marker with its first word, never alone', () => {
    // Alone under a paragraph, "-" would draw that paragraph as a heading for a frame.
    expect(at('Rules:\n- Code freeze begins', 8)).toBe('Rules:\n- Code');
    expect(at('Rules:\n-', 8)).toBe('Rules:\n');
    expect(at('A note.[^1]\n\n[^1]: A short note', 18)).toBe('A note.[^1]\n\n[^1]: A');
    // Alone, "##" would draw an empty heading for a frame.
    expect(at('Intro.\n\n## Results so far', 10)).toBe('Intro.\n\n## Results');
    expect(at('Intro.\n\n##', 10)).toBe('Intro.\n\n');
    // The space after a marker can arrive before its first word.
    expect(at('Intro.\n\n## ', 11)).toBe('Intro.\n\n');
    expect(at('Rules:\n- ', 9)).toBe('Rules:\n');
  });

  it('never ends on a marker alone when its first word opens markup still being written', () => {
    const arrived = (text: string) => at(text, text.length);
    // A heading whose first word is bold, italic or code: no empty heading.
    expect(arrived('Intro line here.\n\n## **R')).toBe('Intro line here.\n\n');
    expect(arrived('Intro line here.\n\n### *Ne')).toBe('Intro line here.\n\n');
    expect(arrived('Intro line here.\n\n## `de')).toBe('Intro line here.\n\n');
    // A bold-labelled list item under a paragraph: a "-" alone would draw the paragraph as a heading.
    expect(arrived('Items to note:\n- **Fir')).toBe('Items to note:\n');
    // A formula as the first word, too.
    expect(arrived('Rate:\n- $$x^2')).toBe('Rate:\n');
    // Once the markup closes, the marker shows with it.
    const text = 'Intro line here.\n\n## **Results** so far';
    expect(at(text, text.indexOf('**Results**') + 4)).toBe('Intro line here.\n\n## **Results**');
  });

  it('never ends on a marker and the first mark of markup the next one may open, which draws as text', () => {
    const arrived = (text: string) => at(text, text.length);
    // `$` of `$$`, `\` of `\(` or `\[`, `~` of `~~`, `!` of `![`: alone, a heading would read "$" for a frame.
    for (const first of ['$', '\\', '~', '!']) {
      expect(arrived(`Intro line here.\n\n## ${first}`)).toBe('Intro line here.\n\n');
      expect(arrived(`Intro line here.\n\n###   ${first}`)).toBe('Intro line here.\n\n');
      expect(arrived(`Values to note:\n- ${first}`)).toBe('Values to note:\n');
      expect(arrived(`Steps to take:\n1. ${first}`)).toBe('Steps to take:\n');
      expect(arrived(`Remember this:\n> ${first}`)).toBe('Remember this:\n');
    }
    expect(arrived('Intro line here.\n\n## ![Lo')).toBe('Intro line here.\n\n');
    // Start tags draw nothing before their text, and an entity before its `;` draws as text.
    expect(arrived('Intro line here.\n\n## <sup>')).toBe('Intro line here.\n\n');
    expect(arrived('Items to note:\n- <b>')).toBe('Items to note:\n');
    expect(arrived('Items to note:\n- <b>**Fi')).toBe('Items to note:\n');
    expect(at('Items to note:\n- <b> First</b> item', 19)).toBe('Items to note:\n- <b> First</b>');
    expect(arrived('Intro line here.\n\n## &am')).toBe('Intro line here.\n\n');
    expect(arrived('Intro line here.\n\n## &amp;')).toBe('Intro line here.\n\n## &amp;');
    // Once the markup is whole, it shows with its marker.
    expect(arrived('Intro line here.\n\n## $$x^2$$')).toBe('Intro line here.\n\n## $$x^2$$');
    expect(arrived('Intro line here.\n\n## \\(a+b\\)')).toBe('Intro line here.\n\n## \\(a+b\\)');
    expect(arrived('Intro line here.\n\n## ~~Old~~')).toBe('Intro line here.\n\n## ~~Old~~');
    // A mark the next character shows to open nothing is text, and so is one anywhere but first on its line.
    expect(arrived('Intro line here.\n\n## $5 budget')).toBe('Intro line here.\n\n## $5 budget');
    expect(arrived('Intro line here.\n\n## ~2 hours')).toBe('Intro line here.\n\n## ~2 hours');
    expect(arrived('Intro line here.\n\n## ! ')).toBe('Intro line here.\n\n## ! ');
    expect(arrived('It costs $')).toBe('It costs $');
    expect(arrived('## Budget in $')).toBe('## Budget in $');
  });

  it('never ends on a quote mark alone, which draws an empty quote', () => {
    const arrived = (text: string) => at(text, text.length);
    expect(arrived('Remember this:\n>')).toBe('Remember this:\n');
    expect(arrived('Remember this:\n> ')).toBe('Remember this:\n');
    expect(arrived('Remember this:\n> **No')).toBe('Remember this:\n');
    // A list item or a quote inside a quote, too.
    expect(arrived('Remember this:\n> - ')).toBe('Remember this:\n');
    expect(arrived('Remember this:\n> > ')).toBe('Remember this:\n');
    // With its first word, it shows.
    expect(at('Remember this:\n> Keep it short', 16)).toBe('Remember this:\n> Keep');
    expect(arrived('Remember this:\n> **Note:** keep')).toBe('Remember this:\n> **Note:** keep');
    // A `>` inside a line is text.
    expect(arrived('So a > b')).toBe('So a > b');
  });
});

describe('useFramePacedText', () => {
  let now = 0;
  let frames: FrameRequestCallback[] = [];
  const shown: string[] = [];
  function Probe({ text, active }: { text: string; active: boolean }) {
    shown.push(useFramePacedText(text, active));
    return null;
  }
  const frame = (ms = 16) => {
    now += ms;
    act(() => {
      const pending = frames;
      frames = [];
      for (const cb of pending) cb(now);
    });
  };
  beforeEach(() => {
    now = 1000;
    frames = [];
    shown.length = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
    vi.stubGlobal('cancelAnimationFrame', () => {});
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('spreads a burst over the frames that follow, and has all of it within the lag', () => {
    const first = 'The release starts on Monday.';
    const burst = `${first} QA signs the checklist, then staging runs for two days before production.`;
    const { rerender } = render(<Probe text={first} active />);
    rerender(<Probe text={burst} active />);
    const lengths: number[] = [];
    for (let elapsed = 0; elapsed <= REVEAL_LAG_MS + 16; elapsed += 16) {
      frame();
      lengths.push(shown[shown.length - 1].length);
    }
    // Several frames each add a little, never going back, and the whole burst is out by the lag.
    expect(new Set(lengths).size).toBeGreaterThan(3);
    expect(lengths.every((n, i) => i === 0 || n >= lengths[i - 1])).toBe(true);
    expect(shown[shown.length - 1]).toBe(burst);
  });

  it('keeps revealing in strict mode, where effects are run, undone and run again on mount', () => {
    const view = (text: string) => (
      <React.StrictMode>
        <Probe text={text} active />
      </React.StrictMode>
    );
    const { rerender } = render(view('One.'));
    rerender(view('One. Two three four five six seven eight nine ten.'));
    for (let i = 0; i < 8; i += 1) frame();
    expect(shown[shown.length - 1]).toBe('One. Two three four five six seven eight nine ten.');
  });

  it('finishes a reveal under way when the answer is done, within the lag, and starts none after', () => {
    const full = 'Part one. Part two arrives in one go, with a few more words.';
    const { rerender } = render(<Probe text="Part one." active />);
    rerender(<Probe text={full} active />);
    frame();
    rerender(<Probe text={full} active={false} />);
    // Not all at once: the words already on their way keep coming in.
    expect(shown[shown.length - 1].length).toBeLessThan(full.length);
    for (let elapsed = 0; elapsed <= REVEAL_LAG_MS; elapsed += 16) frame();
    expect(shown[shown.length - 1]).toBe(full);
    // A saved answer that grows is shown as it is.
    rerender(<Probe text={`${full} Edited.`} active={false} />);
    expect(shown[shown.length - 1]).toBe(`${full} Edited.`);
  });

  it('finishes a reveal whose done answer ends on markup that never closed, as it is, and then asks for no frame', () => {
    // A run stopped mid-word, or an answer ending on a stray mark: nothing more comes to close what the reveal holds.
    for (const tail of ['Stopped at **Res', 'Then:\n\n## **Res', 'Then:\n\n## $', 'Remember:\n>', 'Table:\n\n| a | b |']) {
      shown.length = 0;
      frames = [];
      const full = `Part one. Part two arrives in one go, with a few more words. ${tail}`;
      const { rerender, unmount } = render(<Probe text="Part one." active />);
      rerender(<Probe text={full} active />);
      frame();
      rerender(<Probe text={full} active={false} />);
      for (let elapsed = 0; elapsed <= REVEAL_LAG_MS + 48; elapsed += 16) frame();
      expect({ tail, shown: shown[shown.length - 1] }).toEqual({ tail, shown: full });
      expect({ tail, pending: frames.length }).toEqual({ tail, pending: 0 });
      unmount();
    }
  });

  it('shows text that replaces rather than continues what is shown at once', () => {
    const { rerender } = render(<Probe text="Part one." active />);
    rerender(<Probe text="A new turn." active />);
    expect(shown[shown.length - 1]).toBe('A new turn.');
  });

  it('holds nothing back with reduced motion', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce'), addEventListener() {}, removeEventListener() {} }));
    const { rerender } = render(<Probe text="One." active />);
    rerender(<Probe text="One. Two three four five." active />);
    expect(shown[shown.length - 1]).toBe('One. Two three four five.');
  });
});
