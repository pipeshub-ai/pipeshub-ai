import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts ? `${key}:${JSON.stringify(opts)}` : key,
  }),
}));

import '@/lib/__tests__/test-i18n';
import { useAgentBuilderNodeTemplates } from '../use-node-templates';
import type { SkillForBuilder } from '../../../types';

function makeSkill(overrides: Partial<SkillForBuilder> = {}): SkillForBuilder {
  return {
    name: 'deploy-runbook',
    description: 'Deploys the current runbook',
    category: 'ops',
    isBuiltin: false,
    ...overrides,
  };
}

describe('useAgentBuilderNodeTemplates — skills', () => {
  it('emits no skill-* templates when availableSkills is empty', () => {
    const { result } = renderHook(() => useAgentBuilderNodeTemplates([], [], [], [], []));

    const skillTemplates = result.current.nodeTemplates.filter((t) => t.category === 'skills');
    expect(skillTemplates).toHaveLength(0);
  });

  it('emits one skill-* template per available skill with category "skills"', () => {
    const skills = [
      makeSkill({ name: 'deploy-runbook', description: 'Deploys the runbook', category: 'ops' }),
      makeSkill({ name: 'pdf-toolkit', description: '', category: null, isBuiltin: true }),
    ];

    const { result } = renderHook(() =>
      useAgentBuilderNodeTemplates([], [], [], [], skills),
    );

    const skillTemplates = result.current.nodeTemplates.filter((t) => t.category === 'skills');
    expect(skillTemplates.map((t) => t.type)).toEqual([
      'skill-deploy-runbook',
      'skill-pdf-toolkit',
    ]);

    const runbook = skillTemplates.find((t) => t.type === 'skill-deploy-runbook')!;
    expect(runbook.defaultConfig).toEqual({
      skillName: 'deploy-runbook',
      skillDescription: 'Deploys the runbook',
      skillCategory: 'ops',
    });
    expect(runbook.inputs).toEqual([]);
    expect(runbook.outputs).toEqual(['output']);
  });

  it('falls back to a default description when the skill has none', () => {
    const skills = [makeSkill({ name: 'pdf-toolkit', description: '' })];

    const { result } = renderHook(() =>
      useAgentBuilderNodeTemplates([], [], [], [], skills),
    );

    const template = result.current.nodeTemplates.find((t) => t.type === 'skill-pdf-toolkit')!;
    expect(template.description).toBe('agentBuilder.skillNodeTemplateDescription');
  });

  it('names a skill as the chat does: an office pack by its format, an acronym in capitals (FU-QUICK)', () => {
    const skills = ['docx', 'xlsx', 'pptx', 'pdf', 'deploy-runbook'].map((name) => makeSkill({ name }));
    const { result } = renderHook(() => useAgentBuilderNodeTemplates([], [], [], [], skills));
    const labels = result.current.nodeTemplates.filter((t) => t.category === 'skills').map((t) => t.label);
    expect(labels).toEqual(['Word document', 'Excel spreadsheet', 'PowerPoint presentation', 'PDF', 'Deploy Runbook']);
  });
});
