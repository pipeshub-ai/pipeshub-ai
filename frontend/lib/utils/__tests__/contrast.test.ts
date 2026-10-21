import { describe, it, expect } from 'vitest';
import { contrastRatio, meetsAA, relativeLuminance } from '../contrast';

describe('contrast', () => {
  it('matches the WCAG reference extremes', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
  });

  it('is symmetric and accepts a missing #', () => {
    expect(contrastRatio('047857', '#ffffff')).toBeCloseTo(contrastRatio('#ffffff', '#047857'), 10);
  });

  it('flags stock Radix jade-9 as failing AA with white text', () => {
    expect(contrastRatio('#ffffff', '#29a383')).toBeCloseTo(3.15, 2);
    expect(meetsAA('#ffffff', '#29a383')).toBe(false);
  });

  it('rejects malformed colors', () => {
    expect(() => relativeLuminance('#fff')).toThrow();
  });
});
