'use client';

import { useLayoutEffect, useRef, type RefObject } from 'react';

/** Where the live answer text sat in the row, relative to the row, and how it began. */
interface AnswerSpot {
  start: string;
  x: number;
  y: number;
}

/** The page motion role (duration in ms and easing) from the theme tokens: 0 ms with reduced motion. */
function pageMotion(): { ms: number; easing: string } {
  const root = window.getComputedStyle(document.documentElement);
  const raw = root.getPropertyValue('--motion-page').trim();
  const ms = raw.endsWith('ms') ? parseFloat(raw) : raw.endsWith('s') ? parseFloat(raw) * 1000 : 0;
  const reduced = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  return { ms: reduced || !Number.isFinite(ms) ? 0 : ms, easing: root.getPropertyValue('--motion-ease-settle').trim() || 'ease-out' };
}

/** How much of a text's start tells the answer's sentence again in the trail. */
const START_LENGTH = 24;

/**
 * The first words of an element's text, to tell the answer's sentence again in the trail. Read from its first text
 * nodes only: it runs on every streaming commit, and the whole answer's text is a new string each time.
 */
function startOf(el: Element): string {
  const walker = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let text = '';
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    text += node.nodeValue ?? '';
    if (text.replace(/\s+/g, ' ').trim().length >= START_LENGTH) break;
  }
  return text.replace(/\s+/g, ' ').trim().slice(0, START_LENGTH);
}

/**
 * Text that streams as the answer can turn out to be narration: a tool call follows, and it moves into the trail
 * above. Most narration never shows as the answer (`mayBeNarration` keeps a sentence that may be it out of the answer
 * until the call settles it); text longer than that can still move. It glides there from where it was being read,
 * instead of leaving one place and fading in at another. Every
 * streaming commit notes where the answer's text sits; the commit that moves it into the trail animates the
 * trail's newest narration from that spot. With reduced motion it simply moves.
 */
export function useNarrationGlide(root: RefObject<HTMLElement | null>, isStreaming: boolean): void {
  const spot = useRef<AnswerSpot | null>(null);
  useLayoutEffect(() => {
    const row = root.current;
    if (!row || !isStreaming) {
      spot.current = null;
      return;
    }
    const answer = row.querySelector('.answer-prose');
    const start = answer ? startOf(answer) : '';
    // Nothing to note and nothing to move (the run has no answer text yet): no layout is read.
    if (!(answer && start) && !spot.current) return;
    const origin = row.getBoundingClientRect();
    if (answer && start) {
      const at = answer.getBoundingClientRect();
      spot.current = { start, x: at.left - origin.left, y: at.top - origin.top };
      return;
    }
    const from = spot.current;
    spot.current = null;
    if (!from) return;
    const narrations = row.querySelectorAll<HTMLElement>('.narration-text');
    const moved = narrations[narrations.length - 1];
    if (!moved || !startOf(moved).startsWith(from.start.slice(0, 12))) return;
    const { ms, easing } = pageMotion();
    if (ms <= 0) return;
    const to = moved.getBoundingClientRect();
    const dx = from.x - (to.left - origin.left);
    const dy = from.y - (to.top - origin.top);
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    // The same words, not new ones: no entrance fade for the row or the text, only the move.
    for (let el: HTMLElement | null = moved; el && el !== row; el = el.parentElement) {
      if (el.classList.contains('agent-activity-enter')) el.getAnimations?.().forEach((a) => a.cancel());
    }
    // The open trail's body clips its content for its fold; the words come in from outside it, so not while they move.
    const clip = moved.closest('.trail-body')?.firstElementChild;
    const unclip = clip instanceof HTMLElement ? clip : null;
    if (unclip) unclip.style.overflow = 'visible';
    const glide = moved.animate?.([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'translate(0, 0)' }], { duration: ms, easing });
    const restore = () => {
      if (unclip) unclip.style.overflow = '';
    };
    if (glide?.finished) glide.finished.then(restore, restore);
    else restore();
  });
}
