/**
 * WP-10 card (C3.71 UX-03, C3.72 AP-05): an approval card shows what the call would change (a diff, cut lines and a
 * worker-cut diff said so) and what it costs (only a declared cost), and Deny opens a reason field whose reason goes
 * with the denial; the decided card's receipt quotes it.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalCard } from '../approval-card';
import { ApprovalInteraction } from '../interaction-card';
import { DIFF_PREVIEW_LINES } from '../approval-effect';
import { approvalEffectOf, diffLines } from '../../../approval-preview';
import { approvalFromPart } from '../../../durable-run-ui';
import { withApprovalRequested } from '../../../approvals';
import type { PendingApproval } from '../../../types';

const h = React.createElement;
beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const DIFF = [
  '--- a/docs/launch-plan.md',
  '+++ b/docs/launch-plan.md',
  '@@ -1,4 +1,4 @@',
  ' ## Timeline',
  '-Beta opens in Q3 for design partners.',
  '+Beta opens in Q4 for design partners.',
  ' Owner: Ana Ruiz',
  '',
].join('\n');

const card = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: 'ix.approve.k1',
  toolCallId: 'ix.approve.k1',
  toolName: 'github__edit_file',
  args: JSON.stringify({ path: 'docs/launch-plan.md', old_string: 'Q3', new_string: 'Q4' }),
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  ...over,
});

const renderCard = (approval: PendingApproval) => {
  const onDecide = vi.fn();
  render(h(Theme, null, h(ApprovalCard, { approval, onDecide })));
  return onDecide;
};

describe('UX-03: the card shows the concrete effect (C3.71)', () => {
  it('UX-03: an edit shows its diff, added and removed lines marked, in place of the raw arguments', () => {
    renderCard(card({ effect: { diff: DIFF } }));
    const diff = screen.getByTestId('approval-diff');
    expect(within(diff).getByTestId('approval-diff-target').textContent).toBe('docs/launch-plan.md');
    expect(diff.querySelector('[data-kind="add"]')?.textContent).toBe('+Beta opens in Q4 for design partners.');
    expect(diff.querySelector('[data-kind="del"]')?.textContent).toBe('-Beta opens in Q3 for design partners.');
    expect(screen.queryByTestId('approval-preview')).toBeNull();
    expect(diff.textContent).not.toContain('old_string');
    expect(screen.getByRole('region', { name: 'Changes to docs/launch-plan.md' })).toBeTruthy();
  });

  it('UX-03: a long diff shows its first lines and opens to all of them; a diff the worker cut says so', () => {
    const long = ['--- a/a.txt', '+++ b/a.txt', ...Array.from({ length: 30 }, (_, i) => `+line ${i}`)].join('\n');
    renderCard(card({ effect: { diff: long, diffTruncated: true } }));
    const diff = screen.getByTestId('approval-diff');
    expect(diff.querySelectorAll('[data-kind]').length).toBe(DIFF_PREVIEW_LINES);
    fireEvent.click(screen.getByRole('button', { name: 'Show the full change (30 lines)' }));
    expect(diff.querySelectorAll('[data-kind]').length).toBe(30);
    expect(screen.getByTestId('approval-diff-truncated').textContent).toContain('cut');
  });

  it('UX-03: a declared cost reads as an estimate; no declared cost shows nothing', () => {
    renderCard(card({ effect: { costEstimate: { usd: 0.04, basis: 'declared' } } }));
    expect(screen.getByTestId('approval-cost').textContent).toBe('Estimated cost: about $0.04as the tool states it');
    expect(screen.getByTestId('approval-preview')).toBeTruthy();
    cleanup();
    renderCard(card());
    expect(screen.queryByTestId('approval-cost')).toBeNull();
    expect(screen.queryByTestId('approval-diff')).toBeNull();
  });

  it('UX-03: a secret inside a diff is masked like the arguments preview', () => {
    const lines = diffLines('+token = ghp_abcdefghijklmnopqrstuvwxyz0123456789\n');
    expect(lines[0].text).not.toContain('ghp_abcdefghijklmnopqrstuvwxyz0123456789');
  });

  it('UX-03: a malformed preview is dropped field by field, live and saved alike', () => {
    expect(approvalEffectOf({ diff: '', costEstimate: { usd: -1 } })).toBeUndefined();
    expect(approvalEffectOf({ diff: DIFF, diffTruncated: 'yes', costEstimate: { usd: 'free' } })).toEqual({ diff: DIFF });
    const [live] = withApprovalRequested([], { ...card(), effect: { diff: 7 } as never });
    expect(live).not.toHaveProperty('effect');
    const saved = approvalFromPart({
      type: 'approval',
      interactionId: 'ix.approve.k1',
      toolName: 'github__edit_file',
      status: 'pending',
      effect: { diff: DIFF, diffTruncated: true, costEstimate: { usd: 0.01, basis: 'declared' } },
    });
    expect(saved?.effect).toEqual({ diff: DIFF, diffTruncated: true, costEstimate: { usd: 0.01, basis: 'declared' } });
  });
});

describe('AP-05: deny with a reason the harness receives (C3.72)', () => {
  it('AP-05: Deny opens the reason field in place; Deny again sends the trimmed reason', () => {
    const onDecide = renderCard(card());
    fireEvent.click(screen.getByRole('button', { name: /^Deny / }));
    expect(onDecide).not.toHaveBeenCalled();
    const input = screen.getByLabelText('Why not? (optional, the agent reads this)');
    fireEvent.change(input, { target: { value: '  Post it in #team-updates, not #general ' } });
    expect(screen.getByText('41 / 1000')).toBeTruthy();
    fireEvent.click(screen.getByTestId('deny-confirm'));
    expect(onDecide).toHaveBeenCalledWith('denied', undefined, 'Post it in #team-updates, not #general');
  });

  it('AP-05: an empty reason denies with none; Cancel goes back to the buttons', () => {
    const onDecide = renderCard(card());
    fireEvent.click(screen.getByRole('button', { name: /^Deny / }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('deny-reason')).toBeNull();
    expect(screen.getByRole('button', { name: /^Allow once: / })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^Deny / }));
    fireEvent.click(screen.getByTestId('deny-confirm'));
    expect(onDecide).toHaveBeenCalledWith('denied');
  });

  it('AP-05: the field holds at most what the decision route takes', () => {
    renderCard(card());
    fireEvent.click(screen.getByRole('button', { name: /^Deny / }));
    expect(screen.getByTestId('deny-reason-input').getAttribute('maxlength')).toBe('1000');
  });

  it('AP-05: the decided card folds to a receipt that quotes the reason', () => {
    const decided = card({ status: 'denied', denyReason: 'Post it in #team-updates' });
    render(
      h(Theme, null, h(ApprovalInteraction, {
        group: { key: 'g', items: [decided] },
        state: 'done',
        onDecide: vi.fn(),
        onDecideAll: vi.fn(),
        conversationChoice: false,
        paused: false,
      })),
    );
    expect(screen.getByTestId('interaction-receipt').textContent).toContain('Denied by you, because “Post it in #team-updates”');
  });

  it('AP-05: a saved denial keeps its reason for the receipt after a reload; any other decision has none', () => {
    const part = { type: 'approval' as const, interactionId: 'ix.approve.k1', toolName: 'slack__send_message' };
    expect(approvalFromPart({ ...part, status: 'denied', denyReason: '  Not to #general ' })?.denyReason).toBe('Not to #general');
    expect(approvalFromPart({ ...part, status: 'approved', denyReason: 'stale' })).not.toHaveProperty('denyReason');
    expect(approvalFromPart({ ...part, status: 'denied', denyReason: 7 })).not.toHaveProperty('denyReason');
  });

  it('AP-05: "Deny all" of a batch opens the same field and sends one reason for every call', () => {
    const onDecideAll = vi.fn();
    const items = [0, 1, 2].map((i) =>
      card({ interactionId: `ix.approve.t${i}`, toolCallId: `t${i}`, args: JSON.stringify({ to: `+1555000${i}`, body: 'Reminder' }) }),
    );
    render(
      h(Theme, null, h(ApprovalInteraction, {
        group: { key: 'g', items },
        state: 'active',
        onDecide: vi.fn(),
        onDecideAll,
        conversationChoice: false,
        paused: false,
      })),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Deny all' }));
    fireEvent.change(screen.getByTestId('deny-reason-input'), { target: { value: 'Not tonight' } });
    fireEvent.click(screen.getByTestId('deny-confirm'));
    expect(onDecideAll).toHaveBeenCalledWith(items, 'denied', undefined, 'Not tonight');
  });

  it('UX-03: a batch of calls that each declare a cost shows the total, per call × count', () => {
    const items = [0, 1, 2].map((i) =>
      card({
        interactionId: `ix.approve.t${i}`,
        toolCallId: `t${i}`,
        args: JSON.stringify({ to: `+1555000${i}`, body: 'Reminder' }),
        effect: { costEstimate: { usd: 0.0079, basis: 'declared' } },
      }),
    );
    render(
      h(Theme, null, h(ApprovalInteraction, {
        group: { key: 'g', items },
        state: 'active',
        onDecide: vi.fn(),
        onDecideAll: vi.fn(),
        conversationChoice: false,
        paused: false,
      })),
    );
    expect(screen.getByTestId('approval-cost').textContent).toBe(
      'Estimated cost: about $0.02$0.0079 per call × 3, as the tool states it',
    );
  });
});
