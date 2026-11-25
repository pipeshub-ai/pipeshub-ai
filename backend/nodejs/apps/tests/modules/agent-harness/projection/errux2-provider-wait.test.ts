import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import {
  RunProjector,
  WAITING_PROVIDER,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { CONTRACTS_DIR } from '../../../support/harness/contracts';

/** ERR-UX2 (EU-O5, D-5 parity): a durable run's provider wait is the frame today's loop sends for its retry. */

const VECTOR = JSON.parse(
  readFileSync(join(CONTRACTS_DIR, 'vectors', 'provider-wait.json'), 'utf8'),
) as { frameType: string; status: string; retryAtField: string };

const RUN = 'run-errux2';
let seq = 0;
const event = (type: string, data: Record<string, unknown>, writer = 'worker'): JournalEvent =>
  ({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId: RUN, attemptId: `${RUN}.a1`,
    fence: 1, type, schemaVersion: 1, critical: true, ts: `2026-10-11T12:00:0${seq}.000Z`, writer,
    visibility: 'owner', data,
  }) as JournalEvent;

describe('ERR-UX2: the provider wait frame (provider-wait.json)', () => {
  it("Node projects run.yielded{llm_backoff} as the vector's frame, with the yield's notBefore", () => {
    seq = 0;
    const frames = new RunProjector({
      runId: RUN, sessionId: 'ses-1', conversationId: 'conv-1', goal: 'q', audience: 'owner',
    }).project([
      event('run.claimed', { fence: 1 }),
      event('turn.started', { turn: 1 }),
      event('run.yielded', { notBefore: '2026-10-11T12:00:42.000Z', reason: 'llm_backoff' }, 'node'),
    ]);
    const waits = frames
      .map((f) => (f as { frame?: Record<string, unknown> }).frame ?? (f as Record<string, unknown>))
      .filter((f) => f['type'] === VECTOR.frameType)
      .map((f) => f['snapshot'] as Record<string, unknown>)
      .filter((s) => s['status'] === VECTOR.status);
    expect(WAITING_PROVIDER).to.equal(VECTOR.status);
    expect(waits.map((s) => s[VECTOR.retryAtField])).to.deep.equal(['2026-10-11T12:00:42.000Z']);
  });
});
