import { describe, expect, it } from 'vitest';
import type { StageStateSummary } from '@/app/(main)/knowledge-base/types';
import { buildStageTrackSteps } from '../stage-track-steps';

const t = (key: string, options?: Record<string, unknown>) =>
  options?.n !== undefined ? `${key}:${options.n}` : key;

const record = {
  parsingStatus: 'COMPLETED',
  indexingStatus: 'COMPLETED',
  extractionStatus: 'COMPLETED',
} as const;

function stage(overrides: Partial<StageStateSummary> & Pick<StageStateSummary, 'stage' | 'status'>): StageStateSummary {
  return {
    reason: null,
    attempt: 1,
    startedAtMs: 1,
    finishedAtMs: 2,
    updatedAtMs: 2,
    ...overrides,
  };
}

const classifyDone = stage({ stage: 'classify', status: 'COMPLETED' });
const baseSteps = buildStageTrackSteps(record, [classifyDone], t);

function entitiesStep(states: StageStateSummary[] | undefined) {
  return buildStageTrackSteps(record, states, t).find((s) => s.key === 'entities');
}

describe('buildStageTrackSteps entities step', () => {
  it('shows a completed entities stage after the classified step', () => {
    const steps = buildStageTrackSteps(
      record,
      [classifyDone, stage({ stage: 'entities', status: 'COMPLETED' })],
      t,
    );
    expect(steps.map((s) => s.key)).toEqual(['parsed', 'searchable', 'classified', 'entities']);
    expect(steps[3]).toEqual({
      key: 'entities',
      label: 'recordView.stageEntities',
      status: 'COMPLETED',
      hint: 'recordView.stageAttempt:1',
    });
  });

  it('shows a failed entities stage with its reason', () => {
    const step = entitiesStep([
      classifyDone,
      stage({ stage: 'entities', status: 'FAILED', attempt: 3, reason: ' LLM timed out ' }),
    ]);
    expect(step?.status).toBe('FAILED');
    expect(step?.hint).toBe('recordView.stageAttempt:3 · LLM timed out');
  });

  it('shows a skipped entities stage with its reason', () => {
    const step = entitiesStep([stage({ stage: 'entities', status: 'SKIPPED', reason: 'turned off' })]);
    expect(step?.status).toBe('SKIPPED');
    expect(step?.hint).toBe('recordView.stageAttempt:1 · turned off');
  });

  it('shows an in-progress entities stage', () => {
    const step = entitiesStep([stage({ stage: 'entities', status: 'IN_PROGRESS', finishedAtMs: null })]);
    expect(step?.status).toBe('IN_PROGRESS');
    expect(step?.hint).toBe('recordView.stageAttempt:1');
  });

  it('hides the step when stageStates has no entities entry', () => {
    expect(entitiesStep([classifyDone])).toBeUndefined();
    expect(entitiesStep([])).toBeUndefined();
    expect(entitiesStep(undefined)).toBeUndefined();
    expect(buildStageTrackSteps(record, null, t).map((s) => s.key)).toEqual([
      'parsed',
      'searchable',
      'classified',
    ]);
  });

  it('leaves the other steps unchanged whatever the entities stage reports', () => {
    const steps = buildStageTrackSteps(
      record,
      [classifyDone, stage({ stage: 'entities', status: 'FAILED', reason: 'boom' })],
      t,
    );
    expect(steps.slice(0, 3)).toEqual(baseSteps);
  });
});
