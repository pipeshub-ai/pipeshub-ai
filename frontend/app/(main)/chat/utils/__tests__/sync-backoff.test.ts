import { describe, it, expect } from 'vitest';
import { applyRetryAfter, nextPollDelayMs, withJitter } from '../sync-backoff';

describe('nextPollDelayMs', () => {
  it('polls every 4 s while the chat is active', () => {
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 0 })).toBe(4000);
    expect(nextPollDelayMs({ visible: true, idleMs: 60_000, failures: 0 })).toBe(4000);
  });

  it('slows to 15 s after 5 idle minutes and never past it', () => {
    expect(nextPollDelayMs({ visible: true, idleMs: 300_000, failures: 0 })).toBe(15000);
    expect(nextPollDelayMs({ visible: true, idleMs: 9_999_999, failures: 0 })).toBe(15000);
    const mid = nextPollDelayMs({ visible: true, idleMs: 180_000, failures: 0 })!;
    expect(mid).toBeGreaterThan(4000);
    expect(mid).toBeLessThan(15000);
  });

  it('doubles per failure and caps at 60 s', () => {
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 1 })).toBe(8000);
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 2 })).toBe(16000);
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 3 })).toBe(32000);
    expect(nextPollDelayMs({ visible: true, idleMs: 300_000, failures: 3 })).toBe(60000);
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 30 })).toBe(60000);
  });

  it('puts a visible window without focus on the idle cadence, even while the chat is active', () => {
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 0, focused: false })).toBe(15000);
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 1, focused: false })).toBe(30000);
    expect(nextPollDelayMs({ visible: true, idleMs: 0, failures: 0, focused: true })).toBe(4000);
  });

  it('does not poll in a hidden tab', () => {
    expect(nextPollDelayMs({ visible: false, idleMs: 0, failures: 0 })).toBeNull();
  });

  it('treats negative idle time as active', () => {
    expect(nextPollDelayMs({ visible: true, idleMs: -5, failures: 0 })).toBe(4000);
  });
});

describe('withJitter', () => {
  it('spreads by +-20% and is never negative', () => {
    expect(withJitter(1000, () => 0)).toBe(800);
    expect(withJitter(1000, () => 0.5)).toBe(1000);
    expect(withJitter(1000, () => 1)).toBe(1200);
    expect(withJitter(0, () => 0)).toBe(0);
  });
  it('uses Math.random by default', () => {
    const v = withJitter(1000);
    expect(v).toBeGreaterThanOrEqual(800);
    expect(v).toBeLessThanOrEqual(1200);
  });
});

describe('applyRetryAfter', () => {
  it('raises the delay to the server floor and never lowers it', () => {
    expect(applyRetryAfter(4000, 10)).toBe(10000);
    expect(applyRetryAfter(30000, 10)).toBe(30000);
    expect(applyRetryAfter(4000, undefined)).toBe(4000);
    expect(applyRetryAfter(4000, 0)).toBe(4000);
  });
});
