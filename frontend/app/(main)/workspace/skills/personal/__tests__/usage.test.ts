import { describe, it, expect } from 'vitest';
import { isAssignedToAgents, isRequiredByOtherSkills, namesWithHidden } from '../usage';
import type { SkillUsage } from '../types';

function usage(patch: Partial<SkillUsage>): SkillUsage {
  return { usedByAgents: [], requiredBySkills: [], otherAgentCount: 0, otherSkillCount: 0, ...patch };
}

describe('skill usage on the delete page', () => {
  it('is free to delete with nothing named or counted', () => {
    expect(isAssignedToAgents(usage({}))).toBe(false);
    expect(isRequiredByOtherSkills(usage({}))).toBe(false);
    expect(isAssignedToAgents(null)).toBe(false);
  });

  it('detaches when only agents the user cannot open use the skill', () => {
    expect(isAssignedToAgents(usage({ otherAgentCount: 2 }))).toBe(true);
    expect(isAssignedToAgents(usage({ usedByAgents: [{ id: 'a', name: 'A' }] }))).toBe(true);
  });

  it('blocks when only skills the user cannot see require it', () => {
    expect(isRequiredByOtherSkills(usage({ otherSkillCount: 1 }))).toBe(true);
    expect(isRequiredByOtherSkills(usage({ requiredBySkills: ['dep'] }))).toBe(true);
  });

  it('lists the named ones and a count for the rest, never a name it was not given', () => {
    const label = (count: number) => `${count} hidden`;
    expect(namesWithHidden(['A', 'B'], 0, label)).toBe('A, B');
    expect(namesWithHidden(['A'], 3, label)).toBe('A, 3 hidden');
    expect(namesWithHidden([], 1, label)).toBe('1 hidden');
    expect(namesWithHidden([], undefined, label)).toBe('');
  });
});
