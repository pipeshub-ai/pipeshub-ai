import { describe, it, expect } from 'vitest';
import { JUMP_TO_NEWEST_THRESHOLD_PX, shouldShowJumpToNewest, tailClearance } from '../jump-to-newest';

describe('shouldShowJumpToNewest', () => {
  it('hides when there is no message', () => {
    expect(shouldShowJumpToNewest(null, 500)).toBe(false);
  });

  it('hides when the latest message ends inside the view', () => {
    expect(shouldShowJumpToNewest(300, 500)).toBe(false);
  });

  it('hides when the latest message ends exactly at the bottom edge', () => {
    expect(shouldShowJumpToNewest(500, 500)).toBe(false);
  });

  it('hides for a small overhang so a nudge does not flash the button', () => {
    expect(shouldShowJumpToNewest(500 + JUMP_TO_NEWEST_THRESHOLD_PX, 500)).toBe(false);
  });

  it('shows once the latest message ends past the threshold', () => {
    expect(shouldShowJumpToNewest(500 + JUMP_TO_NEWEST_THRESHOLD_PX + 1, 500)).toBe(true);
  });

  it('shows when the latest message is far below', () => {
    expect(shouldShowJumpToNewest(5000, 500)).toBe(true);
  });

  it('hides when the view is scrolled past the latest message (spacer area)', () => {
    expect(shouldShowJumpToNewest(-200, 500)).toBe(false);
  });

  it('respects a custom threshold', () => {
    expect(shouldShowJumpToNewest(560, 500, 50)).toBe(true);
    expect(shouldShowJumpToNewest(540, 500, 50)).toBe(false);
  });
});

describe('tailClearance', () => {
  it('scrolls by the part that went under the composer, only when the end was in view', () => {
    expect(tailClearance(true, 700.4, 600)).toBe(101);
    expect(tailClearance(true, 500, 600)).toBe(0);
    expect(tailClearance(false, 700, 600)).toBe(0);
  });
});
