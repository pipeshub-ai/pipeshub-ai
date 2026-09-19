import { describe, it, expect } from 'vitest';
import { canToggleSkillAvailability } from '../skill-availability';
import type { SkillMetadata } from '../types';

function skill(patch: Partial<Pick<SkillMetadata, 'source' | 'status'>>): Pick<SkillMetadata, 'source' | 'status'> {
  return { source: 'manual', status: 'active', ...patch };
}

describe('canToggleSkillAvailability', () => {
  it('lets the creator toggle a custom active or disabled skill', () => {
    expect(canToggleSkillAvailability(skill({ source: 'manual', status: 'active' }), false)).toBe(true);
    expect(canToggleSkillAvailability(skill({ source: 'imported', status: 'disabled' }), false)).toBe(true);
  });

  it('hides the switch for deprecated and candidate custom skills', () => {
    expect(canToggleSkillAvailability(skill({ status: 'deprecated' }), false)).toBe(false);
    expect(canToggleSkillAvailability(skill({ status: 'candidate' }), true)).toBe(false);
  });

  it('lets only org admins toggle a builtin skill', () => {
    expect(canToggleSkillAvailability(skill({ source: 'builtin', status: 'active' }), false)).toBe(false);
    expect(canToggleSkillAvailability(skill({ source: 'builtin', status: 'disabled' }), false)).toBe(false);
    expect(canToggleSkillAvailability(skill({ source: 'builtin', status: 'active' }), true)).toBe(true);
    expect(canToggleSkillAvailability(skill({ source: 'builtin', status: 'disabled' }), true)).toBe(true);
  });

  it('never lets an admin undeprecate a builtin via the switch', () => {
    expect(canToggleSkillAvailability(skill({ source: 'builtin', status: 'deprecated' }), true)).toBe(false);
  });
});
