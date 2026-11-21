/**
 * R33JGAPS-FU-6: a file attached to the turn that the model never read is named in the answer's typed `limits`
 * (`attachment.not_accessible`, `attachment.unreadable`, with `file`), carried to the completion as a clone the run
 * could not make is; and this Node names `attachment_limits` on its claims, so a worker sends those items only to a
 * Node whose contract takes them.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import {
  ATTACHMENT_LIMITS_FEATURE,
  NODE_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { couldNotDoOf } from '../../../src/modules/agent-harness/projection/timeline';

const LINE = "I couldn't read the attached file notes.txt: access to it was refused.";
const LIMITS = { items: [{ kind: 'attachment.not_accessible', file: 'notes.txt' }], line: LINE };
const appended = (limits: Record<string, unknown>) => () =>
  validateJournalAppend({
    orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', runId: 'run-1', writer: 'worker',
    events: [{
      type: 'x.pipeshub.answer.finalized', schemaVersion: 1, critical: false, visibility: 'owner',
      data: { outcome: 'answered', answer: `${LINE}\n\nDone.`, partTexts: [], citations: [], confidence: 'Low', limits },
    }],
  } as never);

describe('R33JGAPS-FU-6: an attached file the run could not read, in the answer', () => {
  it('the contract takes the attachment kinds with the file name', () => {
    expect(appended(LIMITS)).to.not.throw();
    expect(appended({ items: [{ kind: 'attachment.unreadable' }], line: LINE })).to.not.throw();
    expect(appended({ items: [{ kind: 'attachment.unreadable', file: '' }], line: LINE })).to.throw();
  });

  it('the projection keeps the item for the chat to word', () => {
    expect(couldNotDoOf({ limits: LIMITS })).to.deep.equal({ limits: LIMITS });
  });

  it('this Node names attachment_limits on its claims', () => {
    expect(NODE_CAPABILITIES.features).to.include(ATTACHMENT_LIMITS_FEATURE);
  });
});
