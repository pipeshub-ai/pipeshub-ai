import { describe, expect, it } from 'vitest';
import { clampSidebarWidth } from '../sidebar-width-store';

describe('clampSidebarWidth', () => {
  it('caps a saved desktop width to 40% of a tablet viewport', () => {
    expect(clampSidebarWidth(450, 800)).toBe(320);
  });
  it('leaves the width alone on desktop', () => {
    expect(clampSidebarWidth(300, 1440)).toBe(300);
  });
});
