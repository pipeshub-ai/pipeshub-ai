import { describe, it, expect } from 'vitest';
import type { AgentSkillReference } from '../../types';
import { skillNodeWarning } from '../skill-node-warning';

function t(key: string, opts?: Record<string, unknown>): string {
  if (key === 'agentBuilder.skillDeprecatedReplacedBy') {
    return `Use ${String(opts?.name)} instead`;
  }
  if (key === 'agentBuilder.skillDeprecated') {
    return 'This skill has been deprecated';
  }
  if (key === 'agentBuilder.skillDisabled') {
    return "This skill is disabled and won't be available to the agent until re-enabled";
  }
  return key;
}

function skill(overrides: Partial<AgentSkillReference> = {}): AgentSkillReference {
  return { name: 'pdf-extractor', status: 'active', ...overrides };
}

describe('skillNodeWarning', () => {
  it('returns undefined for active skills', () => {
    expect(skillNodeWarning(skill(), t)).toBeUndefined();
  });

  it('returns undefined when status is missing', () => {
    expect(skillNodeWarning(skill({ status: undefined }), t)).toBeUndefined();
  });

  it('returns undefined for non-deprecated statuses (deleted is not a canvas warning)', () => {
    expect(skillNodeWarning(skill({ status: 'deleted' }), t)).toBeUndefined();
  });

  it('warns for deprecated skills regardless of status casing', () => {
    expect(skillNodeWarning(skill({ status: 'DEPRECATED' }), t)).toBe(
      'This skill has been deprecated',
    );
  });

  it('names the replacement when replacedBy is set', () => {
    expect(skillNodeWarning(skill({ status: 'deprecated', replacedBy: 'pdf-extractor-v2' }), t)).toBe(
      'Use pdf-extractor-v2 instead',
    );
  });

  it('warns for disabled skills, taking priority over any replacedBy field', () => {
    expect(skillNodeWarning(skill({ status: 'disabled' }), t)).toBe(
      "This skill is disabled and won't be available to the agent until re-enabled",
    );
  });

  it('warns for disabled skills regardless of status casing', () => {
    expect(skillNodeWarning(skill({ status: 'DISABLED' }), t)).toBe(
      "This skill is disabled and won't be available to the agent until re-enabled",
    );
  });
});
