/**
 * WP-51 (WP-31 A–C): the run gates' own surfaces in the chat. A: "Dry run", "Approve plan first" and "Check the
 * result" in the composer's "+" menu, shown as chips once on and sent with that message only; an agent's own gate shows
 * on and cannot be turned off. B: the plan card (GA-03). C: the verification row under the answer (GA-04).
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import type { MessagePart, PendingApproval } from '../../../types';
import { ApprovalInteraction } from '../interaction-card';
import { RunNotices } from '../agent-activity';
import { VerificationPanel, planOf } from '../run-gate-cards';
import { PlusMenuContent } from '../../chat-panel/plus-menu-button';
import { RunGateChips } from '../../chat-panel/run-gate-chips';
import { CITATION_CHECKS, NO_TURN_GATES, turnGatesField, useRunGatesStore } from '../../../run-gates';
import { useChatStore } from '../../../store';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  useRunGatesStore.getState().clear();
  useChatStore.setState({ agentGates: null });
});
afterEach(() => cleanup());

const words = (el: Element | null): string => el?.textContent ?? '';

describe('A. per-turn gates in the composer (GA-02..GA-04)', () => {
  it('GA-02: only the gates the person turned on go with the message, and none the agent already holds it to', () => {
    expect(turnGatesField(NO_TURN_GATES, null)).toEqual({});
    expect(turnGatesField({ dryRun: true, planApproval: true, verify: true }, null)).toEqual({
      gates: { dryRun: true, planApproval: 'required', verify: CITATION_CHECKS },
    });
    const agent = { planApproval: 'required' as const, verify: { checks: [{ kind: 'citations' as const }], onFail: 'flag' as const } };
    expect(turnGatesField({ dryRun: true, planApproval: true, verify: true }, agent)).toEqual({ gates: { dryRun: true } });
  });

  it('GA-02: the "+" menu offers the three gates with what each does; turning one on shows its chip, × turns it off', () => {
    const onToggle = vi.fn((key: 'dryRun' | 'planApproval' | 'verify', on: boolean) => useRunGatesStore.getState().set(key, on));
    render(
      <Theme>
        <PlusMenuContent
          onAttachFiles={vi.fn()}
          internalSearch
          webSearch={false}
          onToggleInternalSearch={vi.fn()}
          onToggleWebSearch={vi.fn()}
          gates={{ turn: NO_TURN_GATES, locks: { planApproval: false, verify: false }, onToggle }}
        />
      </Theme>,
    );
    expect(words(screen.getByTestId('plus-menu-gate-dryRun'))).toContain(
      'Reads for real; nothing is changed, sent or deleted. You see what it would have done.',
    );
    expect(words(screen.getByTestId('plus-menu-gate-planApproval'))).toContain(
      'The agent looks things up, then shows you its plan before it acts.',
    );
    fireEvent.click(screen.getByTestId('plus-menu-gate-dryRun'));
    expect(onToggle).toHaveBeenCalledWith('dryRun', true);
    cleanup();
    render(
      <Theme>
        <RunGateChips agentScoped={false} />
      </Theme>,
    );
    expect(words(screen.getByTestId('run-gate-chip-dryRun'))).toContain('Dry run');
    fireEvent.click(screen.getByRole('button', { name: 'Turn off Dry run' }));
    expect(useRunGatesStore.getState().turn.dryRun).toBe(false);
  });

  it("GA-03: an agent's own plan approval shows on, says why, and cannot be turned off for one message", () => {
    useChatStore.setState({ agentGates: { planApproval: 'required' } });
    const onToggle = vi.fn();
    render(
      <Theme>
        <PlusMenuContent
          onAttachFiles={vi.fn()}
          internalSearch
          webSearch={false}
          onToggleInternalSearch={vi.fn()}
          onToggleWebSearch={vi.fn()}
          gates={{ turn: NO_TURN_GATES, locks: { planApproval: true, verify: false }, onToggle }}
        />
        <RunGateChips agentScoped />
      </Theme>,
    );
    const row = screen.getByTestId('plus-menu-gate-planApproval');
    expect(words(row)).toContain('This agent always shows its plan before it acts.');
    fireEvent.click(row);
    expect(onToggle).not.toHaveBeenCalled();
    const chip = screen.getByTestId('run-gate-chip-planApproval');
    expect(chip.getAttribute('data-locked')).toBe('true');
    expect(within(chip).queryByRole('button')).toBeNull();
  });
});

const PLAN = {
  plan: 'File the login bug in Jira and tell ops by email, with the error rate from the last 24 hours.',
  steps: ['Look up the error rate for the login service', 'Create a Jira issue in OPS with the numbers', 'Email ops@acme.com a link to the issue'],
};
const planCard = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: 'ix.approve.t1.0',
  toolCallId: 't1.0',
  toolName: 'submit_plan',
  args: JSON.stringify(PLAN),
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  runId: 'run-1',
  reason: { rule: 'plan_approval', sources: [], moreSources: 0, conversationGrant: false } as never,
  ...over,
});

function showPlan(item: PendingApproval, state: 'active' | 'done' = 'active') {
  const onDecide = vi.fn();
  render(
    <Theme>
      <ApprovalInteraction group={{ key: 'p', items: [item] }} state={state} onDecide={onDecide} onDecideAll={vi.fn()} conversationChoice paused={false} />
    </Theme>,
  );
  return onDecide;
}

describe('B. the plan card (GA-03)', () => {
  it('GA-03: the plan reads as a plan, its steps numbered; no raw JSON, no "for this conversation"', () => {
    showPlan(planCard());
    const card = screen.getByRole('region', { name: "Review the agent's plan" });
    expect(words(card)).toContain('Nothing that changes anything runs until you approve it.');
    expect(words(card)).toContain(PLAN.plan);
    expect(within(screen.getByTestId('plan-steps')).getAllByRole('listitem').map((li) => li.textContent)).toEqual(PLAN.steps);
    expect(words(card)).not.toContain('{');
    expect(screen.queryByRole('button', { name: /conversation/i })).toBeNull();
  });

  it('GA-03: Approve plan approves it; Decline asks why and declines with the reason', () => {
    const onDecide = showPlan(planCard());
    fireEvent.click(screen.getByRole('button', { name: 'Approve plan' }));
    expect(onDecide).toHaveBeenLastCalledWith(expect.objectContaining({ interactionId: 'ix.approve.t1.0' }), 'approved', 'once');
    fireEvent.click(screen.getByRole('button', { name: 'Decline…' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Ask ops first' } });
    fireEvent.click(screen.getByTestId('deny-confirm'));
    expect(onDecide).toHaveBeenLastCalledWith(expect.anything(), 'denied', 'once', 'Ask ops first');
  });

  it('GA-03: a decided plan leaves one line; a plan the card cannot read falls back to the approval card', () => {
    showPlan(planCard({ status: 'denied' }), 'done');
    expect(words(screen.getByTestId('interaction-receipt'))).toContain('Plan declined · nothing was done');
    expect(planOf('{"plan": "x"}')).toBeNull();
    expect(planOf('not json')).toBeNull();
  });
});

const CHECKS: MessagePart = {
  type: 'notice',
  code: 'unverified',
  item: 'The command pytest exited with code 1.',
  round: 2,
  runId: 'run-1',
  checks: [
    { kind: 'file', target: 'out/report.pdf', passed: true },
    { kind: 'command', target: 'pytest', passed: false, reason: 'The command pytest exited with code 1.' },
    { kind: 'citations', passed: false, reason: 'The answer quotes "net revenue rose 40%" beside Q3 report, and that text is not in the cited source.' },
  ],
};

describe('C. the verification row (GA-04)', () => {
  it('GA-04: under the answer, one row: not verified, how many failed, checked twice; it opens to every check, failed first', () => {
    render(
      <Theme>
        <VerificationPanel parts={[CHECKS]} />
      </Theme>,
    );
    const panel = screen.getByTestId('verification-panel');
    expect(panel.getAttribute('data-status')).toBe('unverified');
    const toggle = screen.getByRole('button');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(words(toggle)).toContain('Not verified');
    expect(words(toggle)).toContain('2 of 3 checks failed · checked twice');
    fireEvent.click(toggle);
    const lines = within(screen.getByTestId('verification-checks')).getAllByRole('listitem');
    expect(lines.map((li) => li.getAttribute('data-passed'))).toEqual(['false', 'false', 'true']);
    expect(words(lines[2])).toContain('out/report.pdf opens');
  });

  it('GA-04: a verified answer is green and says how many checks passed; the amber notice line is not repeated', () => {
    const verified: MessagePart = { type: 'notice', code: 'verified', round: 1, runId: 'run-1', checks: [{ kind: 'citations', passed: true }] };
    render(
      <Theme>
        <RunNotices parts={[verified]} />
        <VerificationPanel parts={[verified]} />
      </Theme>,
    );
    expect(screen.queryAllByRole('note')).toEqual([]);
    expect(screen.getByTestId('verification-panel').getAttribute('data-status')).toBe('verified');
    expect(words(screen.getByRole('button'))).toContain('Verified');
    expect(words(screen.getByRole('button'))).toContain('1 check passed');
  });
});
