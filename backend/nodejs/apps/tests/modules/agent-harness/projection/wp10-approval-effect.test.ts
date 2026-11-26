/**
 * UX-03 (C3.71): an engine approval line's preview of what the call would change and cost reaches the live card
 * frame, the approval view and its saved part, as far as it is well formed; the chat's card renders it.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { approvalEffect, foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { approvalPart } from '../../../../src/modules/agent-harness/projection/transcript';
import { approvalCard } from '../../../../src/modules/agent-harness/projection/run-projector';
import { resolvedData } from '../../../../src/modules/agent-harness/domain/execution';
import { readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const head = base.events.filter((e) => e.type === 'run.admitted' || e.type === 'run.claimed');
const last = head[head.length - 1] as JournalEvent;
const IX = 'ix.approve.k0123';
const at = (seq: number, type: string, data: Record<string, unknown>, critical = true): JournalEvent => ({
  ...last,
  seq,
  type,
  writer: 'worker',
  critical,
  data,
});

const DIFF = '--- a/plan.md\n+++ b/plan.md\n@@ -1 +1 @@\n-Q3\n+Q4\n';
const held = (effect: unknown): JournalEvent[] => [
  ...head,
  at(
    last.seq + 1,
    'x.pipeshub.engine.progress',
    {
      tool: 'code_task',
      taskId: base.runId,
      kind: 'approval',
      text: 'files__edit',
      data: { interactionId: IX, toolName: 'files__edit', argsJson: '{"path":"plan.md"}', effect },
      turnId: 'trn_1',
      seq: 1,
    },
    false,
  ),
  at(last.seq + 2, 'interaction.requested', { interactionId: IX, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
];

describe('UX-03: an approval carries what the call would change and cost (C3.71)', () => {
  it('UX-03: the diff and the declared cost reach the view and the saved part', () => {
    const effect = { diff: DIFF, costEstimate: { usd: 0.04, basis: 'declared' } };
    const [view] = foldTimeline(held(effect), base.runId).approvals;
    expect(view?.effect).to.deep.equal(effect);
    expect(approvalPart(view!, base.runId)['effect']).to.deep.equal(effect);
  });

  it('UX-03: the live card frame carries the preview, so the card shows it before any reload', () => {
    const effect = { diff: DIFF, diffTruncated: true, costEstimate: { usd: 0.04, basis: 'declared' } };
    const [view] = foldTimeline(held(effect), base.runId).approvals;
    expect(approvalCard(view!)['effect']).to.deep.equal(effect);
    const [plain] = foldTimeline(held(undefined), base.runId).approvals;
    expect(approvalCard(plain!)).not.to.have.property('effect');
  });

  it('UX-03: a malformed preview is dropped field by field, never the approval', () => {
    expect(approvalEffect({ diff: '', costEstimate: { usd: -1 } })).to.equal(undefined);
    expect(approvalEffect({ diff: DIFF, diffTruncated: 'yes', costEstimate: { usd: 'free' } })).to.deep.equal({ diff: DIFF });
    expect(approvalEffect('diff')).to.equal(undefined);
    const [view] = foldTimeline(held({ costEstimate: { usd: Number.NaN } }), base.runId).approvals;
    expect(view?.interactionId).to.equal(IX);
    expect(view).not.to.have.property('effect');
    expect(approvalPart(view!, base.runId)).not.to.have.property('effect');
    expect(approvalCard(view!)).not.to.have.property('effect');
  });
});

describe('AP-05: a denial keeps its reason for the receipt after a reload (C3.72)', () => {
  const resolved = (decision: string, reason?: string): JournalEvent =>
    at(last.seq + 3, 'interaction.resolved', {
      interactionId: IX,
      decision,
      ...(reason !== undefined ? { answerRef: resolvedData({ interactionId: IX, decision: 'denied', channel: 'web', reason }, 'p_u')['answerRef'] } : {}),
    });

  it('AP-05: the reason journaled with a denial reaches the view and the saved part, trimmed', () => {
    const [view] = foldTimeline([...held(undefined), resolved('denied', '  Post it in #team-updates  ')], base.runId).approvals;
    expect(view?.status).to.equal('denied');
    expect(view?.denyReason).to.equal('Post it in #team-updates');
    expect(approvalPart(view!, base.runId)['denyReason']).to.equal('Post it in #team-updates');
  });

  it('AP-05: a denial without a reason, or an approval, keeps none', () => {
    for (const event of [resolved('denied'), resolved('approved')]) {
      const [view] = foldTimeline([...held(undefined), event], base.runId).approvals;
      expect(view).not.to.have.property('denyReason');
      expect(approvalPart(view!, base.runId)).not.to.have.property('denyReason');
    }
  });
});
