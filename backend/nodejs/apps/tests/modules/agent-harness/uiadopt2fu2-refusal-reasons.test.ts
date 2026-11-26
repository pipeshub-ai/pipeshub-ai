/**
 * UIADOPT2-FU-2: an Inbox notice of a workflow that did not run names why from a closed set (`WorkflowRefusal`), so
 * a client words each reason; the firing keeps its exact code in its history. Every code the firer and the scheduled
 * admission can give maps to a reason; anything else, a notice written before, reads `unavailable`.
 */
import { expect } from 'chai';
import { ERROR_CODES } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/errors';
import { inboxItemSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/inbox-item';
import { noticeItem } from '../../../src/modules/agent-harness/domain/inbox';
import { WORKFLOW_REFUSALS, workflowRefusalOf } from '../../../src/modules/agent-harness/domain/schedules';
import type { NotificationRow } from '../../../src/libs/repositories/notification/types';

const groupCodes = (group: string): string[] =>
  Object.entries(ERROR_CODES)
    .filter(([, v]) => (v as { group: string }).group === group)
    .map(([k]) => k.toLowerCase());

const refusedRow = (reason: unknown): NotificationRow =>
  ({
    _id: 'nid1',
    orgId: '6650a1b2c3d4e5f607182930',
    assignedTo: '6650a1b2c3d4e5f607182940',
    status: 'unread',
    type: 'AGENT_SCHEDULE_FIRING_REFUSED',
    title: 'A scheduled run could not start',
    message: '"Monday digest" did not run.',
    redirectLink: '/workflows/',
    payload: { scheduleId: 's1', scheduleName: 'Monday digest', reason },
    createdAt: new Date(1_000),
  }) as unknown as NotificationRow;

describe('UIADOPT2-FU-2: a workflow refusal is named from a closed set', () => {
  it('RR-1: each of the firer’s own codes maps to its reason', () => {
    expect(workflowRefusalOf('schedule_deleted')).to.equal('workflow_changed');
    expect(workflowRefusalOf('schedule_paused')).to.equal('workflow_changed');
    expect(workflowRefusalOf('schedule_disabled')).to.equal('workflow_changed');
    expect(workflowRefusalOf('actor_changed')).to.equal('workflow_changed');
    expect(workflowRefusalOf('template_missing')).to.equal('workflow_changed');
    expect(workflowRefusalOf('scheduled_runs_disabled')).to.equal('scheduled_runs_off');
    expect(workflowRefusalOf('rollout_legacy')).to.equal('scheduled_runs_off');
    expect(workflowRefusalOf('agent_forbidden')).to.equal('no_agent_access');
    expect(workflowRefusalOf('quota')).to.equal('too_busy');
    expect(workflowRefusalOf('run_active')).to.equal('too_busy');
    expect(workflowRefusalOf('not_found')).to.equal('not_found');
    expect(workflowRefusalOf('unavailable')).to.equal('unavailable');
  });

  it('RR-2: every admission refusal code of a refusing group maps to that group’s reason, never the fallback', () => {
    const expected: Array<[string, string]> = [
      ['model_access', 'model_not_allowed'],
      ['model_account', 'model_not_allowed'],
      ['engine_profile', 'engine_not_allowed'],
      ['group_access', 'no_agent_access'],
      ['turn', 'no_agent_access'],
      ['operator_control', 'stopped_by_admin'],
      ['budget', 'budget_reached'],
    ];
    for (const [group, reason] of expected) {
      const codes = groupCodes(group);
      expect(codes, group).to.not.be.empty;
      for (const code of codes) expect(workflowRefusalOf(code), `${group}:${code}`).to.equal(reason);
    }
  });

  it('RR-3: an unknown code, an old notice, or no code reads unavailable; the reasons are the contract’s', () => {
    expect(workflowRefusalOf('something_new')).to.equal('unavailable');
    expect(workflowRefusalOf('')).to.equal('unavailable');
    const item = noticeItem(refusedRow('quota'), new Set());
    for (const reason of WORKFLOW_REFUSALS) {
      expect(inboxItemSchema.safeParse({ ...item, subject: { ...item.subject, reason } }).success, reason).to.equal(true);
    }
    expect(inboxItemSchema.safeParse({ ...item, subject: { ...item.subject, reason: 'quota' } }).success, 'a raw code').to.equal(false);
  });

  it('RR-4: the notice’s subject carries the reason, and parses on the contract', () => {
    for (const [raw, reason] of [
      ['engine_profile_widening', 'engine_not_allowed'],
      ['agent_unavailable', 'unavailable'],
      [42, 'unavailable'],
    ] as const) {
      const item = noticeItem(refusedRow(raw), new Set());
      expect(item.subject, String(raw)).to.deep.equal({ workflow: 'Monday digest', outcome: 'refused', reason });
      expect(() => inboxItemSchema.parse(item)).to.not.throw();
    }
  });
});
