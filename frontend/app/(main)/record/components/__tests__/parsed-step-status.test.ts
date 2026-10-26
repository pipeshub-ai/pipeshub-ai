import { describe, expect, it } from 'vitest';
import { parsedStepStatus } from '../parsed-step-status';

describe('parsedStepStatus', () => {
  it('shows a recorded parse status as it is', () => {
    expect(parsedStepStatus('IN_PROGRESS', 'IN_PROGRESS')).toBe('IN_PROGRESS');
    expect(parsedStepStatus('FAILED', 'FAILED')).toBe('FAILED');
    expect(parsedStepStatus('COMPLETED', 'COMPLETED')).toBe('COMPLETED');
  });

  it('reads a searchable or empty record as parsed when its parse status was never written', () => {
    expect(parsedStepStatus('NOT_STARTED', 'COMPLETED')).toBe('COMPLETED');
    expect(parsedStepStatus(undefined, 'COMPLETED')).toBe('COMPLETED');
    expect(parsedStepStatus(null, 'EMPTY')).toBe('COMPLETED');
  });

  it('keeps NOT_STARTED when nothing says the parse ran', () => {
    expect(parsedStepStatus('NOT_STARTED', 'QUEUED')).toBe('NOT_STARTED');
  });

  it('hides the step when the record has no parse status to show', () => {
    expect(parsedStepStatus(undefined, 'IN_PROGRESS')).toBeNull();
    expect(parsedStepStatus(null, 'FAILED')).toBeNull();
  });
});
