/**
 * The Beta badge is the flag's own property (ADR-SCH-02): Scheduled runs no longer needs the agent harness, and stays
 * Beta. Every switch of the harness is still Beta.
 */
import { describe, expect, it } from 'vitest';
import { isBetaFlag } from '../beta';

describe('Labs Beta', () => {
  it('follows `beta`, and every harness switch', () => {
    expect(isBetaFlag({ beta: true })).toBe(true);
    expect(isBetaFlag({ requiresAgentHarness: true })).toBe(true);
    expect(isBetaFlag({ beta: false, requiresAgentHarness: true })).toBe(true);
    expect(isBetaFlag({})).toBe(false);
    expect(isBetaFlag({ beta: false })).toBe(false);
  });
});
