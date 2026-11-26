/**
 * R46FIX-FU-2: several files handed to a job that it may not name are one attachment item with their `count`, so the
 * chat says "2 of the attached files" as the job's step does. The contract takes the count on the attachment kinds,
 * the projection keeps it for the chat, and this Node names `attachment_count` so a worker sends it only here.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import {
  ATTACHMENT_COUNT_FEATURE,
  NODE_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { couldNotDoOf } from '../../../src/modules/agent-harness/projection/timeline';

const LINE = "I couldn't read 2 of the attached files: their content could not be read.";
const ITEM = { kind: 'attachment.unreadable', count: 2 };
const LIMITS = { items: [ITEM], line: LINE };
const appended = (type: string, data: Record<string, unknown>) => () =>
  validateJournalAppend({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', runId: 'run-1', writer: 'worker',
    events: [{ type, schemaVersion: 1, critical: false, visibility: 'owner', data }],
  } as never);
const answer = (limits: Record<string, unknown>) =>
  appended('x.pipeshub.answer.finalized', {
    outcome: 'answered', answer: `${LINE}\n\nDone.`, partTexts: [], citations: [], confidence: 'Low', limits,
  });

describe('R46FIX-FU-2: several files a job may not name, counted', () => {
  it('the contract takes a count on each attachment kind, on the answer and on the job step', () => {
    for (const kind of ['attachment.not_accessible', 'attachment.unreadable', 'attachment.too_large']) {
      expect(answer({ items: [{ kind, count: 3 }], line: LINE }), kind).to.not.throw();
    }
    expect(
      appended('x.pipeshub.tool.result_presented', {
        callId: 'c1', status: 'completed', content: '', resultSummary: 'Done.', limits: [ITEM],
      }),
    ).to.not.throw();
    expect(answer({ items: [{ kind: 'attachment.unreadable', count: 1 }], line: LINE })).to.throw();
    expect(answer({ items: [{ kind: 'attachment.unreadable', count: 101 }], line: LINE })).to.throw();
  });

  it('the projection keeps the count for the chat to word', () => {
    expect(couldNotDoOf({ limits: LIMITS })).to.deep.equal({ limits: LIMITS });
  });

  it('this Node names attachment_count on its claims', () => {
    expect(ATTACHMENT_COUNT_FEATURE).to.equal('attachment_count');
    expect(NODE_CAPABILITIES.features).to.include(ATTACHMENT_COUNT_FEATURE);
  });
});
