import React, { useRef } from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { useNarrationGlide } from '../narration-glide';

/** A row as the answer draws it: the trail's narration, then the live answer text. */
function Row({ narration, answer }: { narration: string[]; answer: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useNarrationGlide(ref, true);
  return (
    <div ref={ref} data-testid="row">
      {narration.map((text) => (
        <div key={text} className="agent-activity-enter">
          <div className="narration-text agent-activity-enter">{text}</div>
        </div>
      ))}
      {answer ? <div className="answer-prose">{answer}</div> : null}
    </div>
  );
}

const rect = (top: number, left: number) => ({ top, left, bottom: top + 24, right: left + 600, width: 600, height: 24, x: left, y: top, toJSON: () => ({}) });

describe('useNarrationGlide', () => {
  const animate = vi.fn();
  beforeEach(() => {
    document.documentElement.style.setProperty('--motion-page', '320ms');
    document.documentElement.style.setProperty('--motion-ease-settle', 'cubic-bezier(0.22, 1, 0.36, 1)');
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.dataset.testid === 'row') return rect(100, 400) as DOMRect;
      if (this.classList.contains('answer-prose')) return rect(300, 400) as DOMRect;
      if (this.classList.contains('narration-text')) return rect(160, 436) as DOMRect;
      return rect(0, 0) as DOMRect;
    });
    Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    animate.mockReset();
    delete (HTMLElement.prototype as { animate?: unknown }).animate;
  });

  it('glides the sentence that moved into the trail from where it was being read', () => {
    const { rerender } = render(<Row narration={[]} answer="I'll search the handbook for the freeze rules." />);
    rerender(<Row narration={["I'll search the handbook for the freeze rules."]} answer="" />);
    expect(animate).toHaveBeenCalledTimes(1);
    const [frames, options] = animate.mock.calls[0];
    // From the answer's spot (row +200, +0) to the trail's (row +60, +36).
    expect(frames[0].transform).toBe('translate(-36px, 140px)');
    expect(options.duration).toBe(320);
  });

  it('does not move anything with reduced motion, nor narration that never was the answer', () => {
    document.documentElement.style.setProperty('--motion-page', '0ms');
    const { rerender } = render(<Row narration={[]} answer="I'll search the handbook." />);
    rerender(<Row narration={["I'll search the handbook."]} answer="" />);
    cleanup();
    document.documentElement.style.setProperty('--motion-page', '320ms');
    const second = render(<Row narration={[]} answer="Here is the plan." />);
    second.rerender(<Row narration={['Something else entirely.']} answer="" />);
    expect(animate).not.toHaveBeenCalled();
  });

  it('reads no layout while the run has no answer text to note', () => {
    const rects = vi.mocked(HTMLElement.prototype.getBoundingClientRect);
    const { rerender } = render(<Row narration={[]} answer="" />);
    rerender(<Row narration={['Searching the handbook.']} answer="" />);
    rerender(<Row narration={['Searching the handbook.', 'Reading two documents.']} answer="" />);
    expect(rects).not.toHaveBeenCalled();
  });

  it('tells the sentence again from its first words, however its text is split and spaced', () => {
    const answer = "I'll   search\n the handbook   for the freeze rules, then the calendar.";
    const { rerender } = render(<Row narration={[]} answer={answer} />);
    rerender(<Row narration={["I'll search the handbook for the freeze rules, then the calendar."]} answer="" />);
    expect(animate).toHaveBeenCalledTimes(1);
  });
});
