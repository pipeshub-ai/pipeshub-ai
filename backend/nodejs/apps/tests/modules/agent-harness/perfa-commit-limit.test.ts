/**
 * PERFA-1, PERFA-2 (CON-15, JRN-29; QA22-F1): the most events one commit
 * carries is the contract's `journal.maxCommitEvents`, read through the
 * generated `RELEASE_DEFAULTS`. A worker splits by the same value, so the two
 * cannot drift apart again.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  commitRunSchema,
  MAX_COMMIT_EVENTS,
} from '../../../src/modules/agent-harness/transport/schemas';

const REPO = join(__dirname, '../../../../../..');
const RELEASE = JSON.parse(
  readFileSync(join(REPO, 'contracts/harness/core/policy/release.json'), 'utf8'),
) as { journal: { maxCommitEvents: number } };

const event = (n: number) => ({
  type: 'tool.outcome',
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  data: { invocationId: `inv-${String(n)}`, status: 'ok' },
});

const legacyEvent = (n: number) => ({ type: 'progress', data: { n } });

const batched = (events: number, legacyEvents = 0, transition?: object) => ({
  params: { runId: 'run-1' },
  body: {
    commandId: 'commit-1',
    attemptId: 'attempt-1',
    fence: 1,
    events: Array.from({ length: events }, (_, n) => event(n)),
    legacyEvents: Array.from({ length: legacyEvents }, (_, n) => legacyEvent(n)),
    ...(transition !== undefined ? { transition } : {}),
  },
});

const legacy = (events: number) => ({
  params: { runId: 'run-1' },
  body: {
    commandId: 'commit-1',
    attemptId: 'attempt-1',
    fence: 1,
    expectedRevision: 2,
    status: 'running',
    events: Array.from({ length: events }, (_, n) => legacyEvent(n)),
    outbox: [],
  },
});

describe('PERF-A: the commit event limit is the contract value', () => {
  it('PERFA-1: MAX_COMMIT_EVENTS is release.json journal.maxCommitEvents', () => {
    expect(MAX_COMMIT_EVENTS).to.equal(RELEASE.journal.maxCommitEvents);
  });

  it('PERFA-2: a commit of exactly the limit is accepted, one more is refused', () => {
    expect(commitRunSchema.safeParse(batched(MAX_COMMIT_EVENTS)).success).to.equal(true);
    const over = commitRunSchema.safeParse(batched(MAX_COMMIT_EVENTS + 1));
    expect(over.success).to.equal(false);
  });

  it('PERFA-2: the limit holds for the legacy event list and the v1 commit body', () => {
    expect(commitRunSchema.safeParse(batched(0, MAX_COMMIT_EVENTS)).success).to.equal(true);
    expect(commitRunSchema.safeParse(batched(0, MAX_COMMIT_EVENTS + 1)).success).to.equal(false);
    expect(commitRunSchema.safeParse(legacy(MAX_COMMIT_EVENTS)).success).to.equal(true);
    expect(commitRunSchema.safeParse(legacy(MAX_COMMIT_EVENTS + 1)).success).to.equal(false);
  });

  it('PERFA-7: a terminal commit at the limit is accepted', () => {
    const failed = { status: 'failed', errorType: 'kernel_error', detail: { kind: 'fail' } };
    expect(commitRunSchema.safeParse(batched(MAX_COMMIT_EVENTS, 0, failed)).success).to.equal(true);
    expect(commitRunSchema.safeParse(batched(0, 0, failed)).success, 'the bare transition').to.equal(true);
    const quarantined = {
      status: 'failed',
      errorType: 'crash_loop',
      detail: { kind: 'quarantine', attempts: 4, lastErrorType: 'crash_loop' },
    };
    expect(commitRunSchema.safeParse(batched(0, 0, quarantined)).success).to.equal(true);
  });
});
