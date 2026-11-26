/**
 * UX2 VC-5: Node labels the history it imports from what the conversation
 * knows of each message: a user's turn names its attachments, a durable
 * answer carries the label recorded with it, and an answer legacy wrote is
 * unknown. The label travels as `x.pipeshub.history.labelled` with the
 * import, and the journal's catalog accepts it from Node only.
 */
import { expect } from 'chai';
import { admittedJournalAppend } from '../../../src/modules/agent-harness/domain/execution';
import { validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import {
  HistoryMessage,
  historyLabelledEvent,
  importLabel,
  planSessionImport,
  sessionImportedEvent,
} from '../../../src/modules/agent-harness/domain/session-import';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { messageProvenance } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import type { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';

const PRIVATE = { complete: true, sources: [{ kind: 'indexed' as const, recordId: 'rec-1' }] };
const PUBLIC = { complete: true, sources: [] };
const UNKNOWN = { complete: false, sources: [] };

const message = (seq: number, role: string, provenance: HistoryMessage['provenance']): HistoryMessage => ({
  seq,
  turns: [{ role, content: `m${seq}` }],
  provenance,
});

describe('the label of imported history (UX2 VC-5)', () => {
  it("a user's turn names its attachments; a durable answer its label; a legacy answer is unknown", () => {
    const user = { messageType: 'user_query', content: 'q', attachments: [{ recordId: 'att-1' }] } as unknown as IMessage;
    expect(messageProvenance(user)).to.deep.equal({ complete: true, sources: [{ kind: 'attachment', recordId: 'att-1' }] });
    expect(messageProvenance({ messageType: 'user_query', content: 'q' } as IMessage)).to.deep.equal(PUBLIC);
    expect(messageProvenance({ messageType: 'bot_response', content: 'a', label: PRIVATE } as IMessage)).to.deep.equal(PRIVATE);
    expect(messageProvenance({ messageType: 'bot_response', content: 'a' } as IMessage)).to.equal(null);
    expect(messageProvenance({ messageType: 'bot_response', content: 'a', label: { complete: 'yes' } } as unknown as IMessage)).to.equal(null);
    expect(messageProvenance({ messageType: 'system', content: 's' } as IMessage)).to.equal(null);
  });

  it('the import joins what the model will read of it; a message it reads nothing of adds nothing', () => {
    const durable = [message(1, 'user_query', PUBLIC), message(2, 'bot_response', PRIVATE)];
    expect(importLabel(durable, 0)).to.deep.equal(PRIVATE);
    expect(importLabel([message(1, 'user_query', PUBLIC)], 0)).to.deep.equal(PUBLIC);
    expect(importLabel([...durable, message(3, 'bot_response', null)], 0)).to.deep.equal(UNKNOWN);
    expect(importLabel([...durable, { seq: 3, turns: [], provenance: null }], 0)).to.deep.equal(PRIVATE);
    // Only what the plan imports: a delta after the ledger counts its own messages.
    expect(importLabel([message(1, 'bot_response', null), message(2, 'user_query', PUBLIC)], 1)).to.deep.equal(PUBLIC);
  });

  it('the plan carries it, and the admission journals it beside its import, as Node', () => {
    const messages = [message(1, 'user_query', PUBLIC), message(2, 'bot_response', PRIVATE)];
    const { plan } = planSessionImport({ messages, lastSeq: 2, ledger: null }, false);
    expect(plan!.label).to.deep.equal(PRIVATE);
    const ref = inlineContentRef(canonicalJsonBytes(plan!.turns));
    const append = admittedJournalAppend(
      { orgId: 'org_1', sessionId: 'ses_1', runId: 'run_1', actorId: 'user_1' },
      'session',
      [sessionImportedEvent(plan!, ref), historyLabelledEvent(ref, plan!.label)],
    );
    expect(append.events.map((e) => e.type)).to.deep.equal(['session.imported', 'x.pipeshub.history.labelled', 'run.admitted']);
    expect(append.events[1]!.data).to.deep.equal({ transcriptDigest: ref.hash, label: PRIVATE });
    expect(append.events[1]!.critical).to.equal(false);
    expect(() => validateJournalAppend(append)).to.not.throw();
    expect(() => validateJournalAppend({ ...append, writer: 'worker' })).to.throw();
  });
});
