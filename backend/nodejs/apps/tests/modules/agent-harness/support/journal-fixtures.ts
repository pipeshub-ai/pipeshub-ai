import {
  JournalEventInput,
  JournalPosition,
} from '../../../../src/modules/agent-harness/domain/journal';

export const workerEvent = (
  type: string,
  data: Record<string, unknown>,
  overrides: Partial<JournalEventInput> = {},
): JournalEventInput => ({
  type,
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  data,
  ...overrides,
});

export const turnStarted = (turn: number): JournalEventInput =>
  workerEvent('turn.started', { turn });

export const citation = (): JournalEventInput =>
  workerEvent('x.pipeshub.citation.recorded', {
    citations: [{ citationId: 'c1', recordId: 'r1' }],
  });

/** A tool outcome whose canonical JSON is at least `bytes` long. */
export const bulkyOutcome = (bytes: number): JournalEventInput =>
  workerEvent('tool.outcome', {
    invocationId: 'inv-1',
    status: 'ok',
    receipt: { body: 'x'.repeat(bytes) },
    labels: { integrity: 'external' },
  });

export const position = (
  overrides: Partial<JournalPosition> = {},
): JournalPosition => ({
  orgId: 'org-1',
  sessionId: 'session-1',
  streamId: 'session-1',
  runId: 'run-1',
  attemptId: 'attempt-1',
  fence: 1,
  ...overrides,
});
