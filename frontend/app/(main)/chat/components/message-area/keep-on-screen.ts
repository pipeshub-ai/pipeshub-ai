/** The longest the answer is held in place at a run's end: the trail's 300 ms fold, on a page that may be busy. */
const RUN_END_HOLD_MAX_MS = 1200;
/** The hold ends sooner once nothing has moved for this long (and the fold has had this long to start). */
const RUN_END_HOLD_QUIET_MS = 200;
/** The reader's own input ends the hold: what they do with the scroll is theirs. */
const READER_INPUT = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;

/**
 * Sent, bubbling, by a part of an answer that closes when the run ends: its trail folding (`CollapsibleActivitySection`),
 * or the badge's place closing on an answer that ended without a level (`ChatResponse`). The scroll box around it keeps
 * the answer under it still while it closes.
 */
/** On the chat's scroller while it follows the streaming answer's end (not while the question rests at the top). */
export const FOLLOWS_END_ATTR = 'data-follows-end';

/** Whether the chat's scroller follows the streaming answer's end now. */
export const viewFollowsEnd = (): boolean =>
  typeof document !== 'undefined' && Boolean(document.querySelector(`.chat-message-scroll[${FOLLOWS_END_ATTR}]`));

export const TRAIL_FOLD_EVENT = 'trailfold';

export interface ScreenHold {
  stop: () => void;
  /** Puts the target back before a commit is painted. */
  sync: () => void;
  readonly done: boolean;
}

/**
 * Keeps `target` where it is on screen while what is above it changes height (the trail folding at a run's end): each
 * frame the scroll position moves by however far the target moved. Without room above (a chat shorter than the view)
 * the target moves by what cannot be scrolled. It ends by itself once nothing moves, or at the reader's own input.
 * `from` is where the target was on screen before the change that starts the hold, when that change is already drawn
 * (the run's end): the first `sync` puts it back there.
 */
export function keepOnScreen(
  container: HTMLElement,
  target: Element,
  autoScrolling: { current: boolean },
  from?: number,
): ScreenHold {
  const top = from ?? target.getBoundingClientRect().top;
  const startedAt = performance.now();
  let movedAt = startedAt;
  let frame = 0;
  let stopped = false;
  const anchoring = container.style.overflowAnchor;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(frame);
    for (const type of READER_INPUT) container.removeEventListener(type, stop);
    container.style.overflowAnchor = anchoring;
  };
  /** Puts the target back where it was; true when it had moved. */
  const correct = (): boolean => {
    const drift = target.getBoundingClientRect().top - top;
    if (Math.abs(drift) < 0.5) return false;
    autoScrolling.current = true;
    container.scrollTop = Math.max(0, container.scrollTop + drift);
    queueMicrotask(() => {
      autoScrolling.current = false;
    });
    return true;
  };
  const step = (now: number) => {
    if (stopped) return;
    if (!target.isConnected) {
      stop();
      return;
    }
    if (correct()) movedAt = now;
    const quiet = now - startedAt > RUN_END_HOLD_QUIET_MS && now - movedAt > RUN_END_HOLD_QUIET_MS;
    if (quiet || now - startedAt > RUN_END_HOLD_MAX_MS) {
      stop();
      return;
    }
    frame = requestAnimationFrame(step);
  };
  // The browser's own scroll anchoring would move the position a second time for the same change.
  container.style.overflowAnchor = 'none';
  for (const type of READER_INPUT) container.addEventListener(type, stop, { passive: true });
  frame = requestAnimationFrame(step);
  // Each frame catches the fold's motion; a commit (the saved answer, the badge) is caught before it is painted.
  const sync = () => {
    if (!stopped && target.isConnected && correct()) movedAt = performance.now();
  };
  return {
    stop,
    sync,
    get done() {
      return stopped;
    },
  };
}
