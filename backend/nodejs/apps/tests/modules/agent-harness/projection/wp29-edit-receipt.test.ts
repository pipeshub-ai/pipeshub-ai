/**
 * AP-06 (C3.75): the card learns that a held call may be edited before it is approved (the worker's `editable`),
 * and after an edited approval the receipt keeps the arguments that ran, live and after a reload.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { approvalReason, foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { approvalPart } from '../../../../src/modules/agent-harness/projection/transcript';
import { resolvedData } from '../../../../src/modules/agent-harness/domain/execution';
import { canonicalJson } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const head = base.events.filter((e) => e.type === 'run.admitted' || e.type === 'run.claimed');
const last = head[head.length - 1] as JournalEvent;
const IX = 'ix.approve.k0456';
const EDITS = { to: 'team@acme.example', body: 'Summary only' };
const at = (seq: number, type: string, data: Record<string, unknown>, writer = 'worker', critical = true): JournalEvent => ({
  ...last,
  seq,
  type,
  writer: writer as JournalEvent['writer'],
  critical,
  data,
});

const held = (editable: boolean): JournalEvent[] => [
  ...head,
  at(
    last.seq + 1,
    'x.pipeshub.engine.progress',
    {
      tool: 'code_task',
      taskId: base.runId,
      kind: 'approval',
      text: 'mail__send_email',
      data: { interactionId: IX, toolName: 'mail__send_email', argsJson: '{"to":"all@acme.example","body":"Full report"}' },
      turnId: 'trn_1',
      seq: 1,
    },
    'worker',
    false,
  ),
  at(
    last.seq + 2,
    'interaction.explained',
    {
      interactionId: IX,
      rule: 'writes',
      layer: 'org',
      toolId: 'mail@default/send_email',
      conversationGrant: false,
      ...(editable ? { editable: true } : {}),
    },
    'worker',
    false,
  ),
  at(last.seq + 3, 'interaction.requested', { interactionId: IX, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
];

describe('AP-06: the card offers an edit and the receipt keeps what ran (C3.75)', () => {
  it('AP-06: the worker’s offer reaches the card’s reason, and only when it was made', () => {
    expect(approvalReason({ rule: 'writes', editable: true, conversationGrant: false })).to.deep.include({ editable: true });
    const [offered] = foldTimeline(held(true), base.runId).approvals;
    expect(offered?.reason).to.deep.include({ editable: true });
    const [plain] = foldTimeline(held(false), base.runId).approvals;
    expect(plain?.reason).not.to.have.property('editable');
  });

  it('AP-06: an edited approval keeps the edit on the view and its saved part; an approval as asked keeps none', () => {
    const answer = resolvedData({ interactionId: IX, decision: 'approved', channel: 'web', edits: EDITS }, 'p_u')['answerRef'];
    const edited = [...held(true), at(last.seq + 4, 'interaction.resolved', { interactionId: IX, decision: 'approved', answerRef: answer }, 'node')];
    const [view] = foldTimeline(edited, base.runId).approvals;
    expect(view?.status).to.equal('approved');
    expect(view?.editedArgs).to.equal(canonicalJson(EDITS));
    expect(approvalPart(view!, base.runId)['editedArgs']).to.equal(canonicalJson(EDITS));
    const asAsked = [...held(true), at(last.seq + 4, 'interaction.resolved', { interactionId: IX, decision: 'approved' }, 'node')];
    const [plain] = foldTimeline(asAsked, base.runId).approvals;
    expect(plain).not.to.have.property('editedArgs');
  });
});

describe('WP-56: the edit card follows the tool’s own argument order', () => {
  it('WP56-ORDER-5: the worker’s field order reaches the card’s reason with the offer, never without it', async () => {
    const { NODE_CAPABILITIES, APPROVAL_EDIT_ORDER_FEATURE } = await import(
      '../../../../src/modules/agent-harness/domain/node-capabilities'
    );
    expect(NODE_CAPABILITIES.features).to.include(APPROVAL_EDIT_ORDER_FEATURE);
    expect(APPROVAL_EDIT_ORDER_FEATURE).to.equal('approval_edit_order');
    expect(
      approvalReason({ rule: 'writes', editable: true, fieldOrder: ['to', 'subject', 'body'], conversationGrant: false }),
    ).to.deep.include({ editable: true, fieldOrder: ['to', 'subject', 'body'] });
    expect(approvalReason({ rule: 'writes', fieldOrder: ['to'], conversationGrant: false })).not.to.have.property('fieldOrder');
    expect(
      approvalReason({ rule: 'writes', editable: true, fieldOrder: ['to', 7, '', 'x'.repeat(129), 'body'], conversationGrant: false }),
    ).to.deep.include({ fieldOrder: ['to', 'body'] });
    expect(approvalReason({ rule: 'writes', editable: true, fieldOrder: 'to', conversationGrant: false })).not.to.have.property(
      'fieldOrder',
    );
  });
});
