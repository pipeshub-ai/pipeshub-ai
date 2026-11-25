/**
 * WP-113 FLOWGRANT: a sub-agent's card offers "Allow onyx.app for this conversation" (one click, beside the default
 * Allow once), the receipt names what it covers, and the list holds only calls still waiting: an engine's
 * sequential asks are never folded into one "Allow all", and asks a grant answered drop out with their receipt.
 * Replays a recorded conversation's 14 asks (`contracts/harness/vectors/flow-grants-recorded.json`).
 */
import React from 'react';
import { readFileSync } from 'fs';
import path from 'path';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { ApprovalInteraction } from '../interaction-card';
import { groupApprovals, isOpenApproval, isOpenGroup } from '../../../interactions';
import { withApprovalResolved } from '../../../approvals';
import type { PendingApproval } from '../../../types';

const h = React.createElement;

const RECORDED = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../../../../contracts/harness/vectors/flow-grants-recorded.json'), 'utf8'),
) as { childRunId: string; events: Array<{ type: string; data: Record<string, unknown> }> };

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

/** The recorded asks as the chat holds them: one per `interaction.explained`, asked one at a time by the engine. */
function recordedCards(withDecidedBefore = true): PendingApproval[] {
  return RECORDED.events
    .filter((event) => event.type === 'interaction.explained')
    .map((event, index) => {
      const data = event.data;
      const destination = (data['destination'] as { label: string }).label;
      return {
        interactionId: String(data['interactionId']),
        toolCallId: String(data['interactionId']),
        toolName: String(data['toolId']),
        args: JSON.stringify(destination === 'SearXNG' ? { query: `q${index}` } : { url: `https://${destination}/p${index}` }),
        argsSummary: null,
        status: 'pending',
        errorCode: null,
        runId: 'root-run',
        childRunId: RECORDED.childRunId,
        childEngine: 'claude_code',
        afterTurns: 0,
        ...(withDecidedBefore ? { decidedBefore: index } : {}),
        reason: {
          rule: 'flow_external_write',
          layer: 'flow',
          sensitivity: 'private',
          sources: [],
          moreSources: 0,
          destination,
          conversationGrant: true,
        },
      } as PendingApproval;
    });
}

const decideProps = () => ({
  onDecide: vi.fn(),
  onDecideAll: vi.fn(),
  conversationChoice: true,
  subAgent: true,
  paused: false,
});

describe('WP-113: allow a place for the whole conversation from a sub-agent card', () => {
  it("FG-F1: a Claude Code child's ask offers the conversation, naming the place, beside the default Allow once", () => {
    const [, , onyx] = recordedCards();
    const props = decideProps();
    render(h(Theme, null, h(ApprovalInteraction, { group: { key: 'g', items: [onyx as PendingApproval] }, state: 'active', ...props })));
    const allow = screen.getByRole('button', { name: /onyx\.app/i, hidden: false });
    expect(screen.getByText('Allow onyx.app for this conversation')).toBeTruthy();
    expect(screen.getByText('Allow once')).toBeTruthy();
    expect(screen.getByTestId('approval-conversation-scope').textContent).toBe(
      'Allowing it for this conversation covers onyx.app only. Anything else still asks.',
    );
    fireEvent.click(screen.getByText('Allow onyx.app for this conversation'));
    expect(props.onDecide).toHaveBeenCalledWith(onyx, 'approved', 'conversation');
    expect(allow).toBeTruthy();
  });

  it("FG-F2: a sub-agent's card never offers \"Don't ask again\" or Edit, which belong to the conversation's own run", () => {
    const [, , onyx] = recordedCards();
    const card = { ...(onyx as PendingApproval), reason: { ...(onyx as PendingApproval).reason!, ruleGrant: true, editable: true } };
    render(
      h(Theme, null, h(ApprovalInteraction, {
        group: { key: 'g', items: [card] }, state: 'active', ...decideProps(), authority: { approve: true, deny: true, edit: true },
        onApproveEdited: vi.fn(),
      })),
    );
    expect(screen.queryByText(/Don.t ask again/i)).toBeNull();
    expect(screen.queryByTestId('approval-edit')).toBeNull();
    expect(screen.getByText('Allow onyx.app for this conversation')).toBeTruthy();
  });

  it('FG-F3: the receipt says what the grant covers', () => {
    const [, , onyx] = recordedCards();
    render(
      h(Theme, null, h(ApprovalInteraction, {
        group: { key: 'g', items: [{ ...(onyx as PendingApproval), status: 'approved', scope: 'conversation' }] },
        state: 'done',
        ...decideProps(),
      })),
    );
    expect(screen.getByText(/Allowed onyx\.app for this conversation by you/)).toBeTruthy();
  });
});

describe('WP-113: the list holds only what still waits', () => {
  it('FG-F4 replay: an engine asking one call at a time gets a card per call, never a combined list', () => {
    const groups = groupApprovals(recordedCards());
    expect(groups).toHaveLength(14);
    // Before WP-113 (no decidedBefore, one model turn for the whole engine run) the six page fetches and the six searches were each one "Allow all".
    const old = groupApprovals(recordedCards(false));
    expect(old.map((group) => group.items.length)).toEqual([2, 6, 6]);
  });

  it('FG-F5: a group lists, counts and allows only the calls still open', () => {
    const cards = recordedCards(false).filter((card) => card.toolName === 'web@searxng/web_search');
    cards[0] = { ...(cards[0] as PendingApproval), status: 'approved' };
    cards[1] = { ...(cards[1] as PendingApproval), status: 'approved' };
    const [group] = groupApprovals(cards);
    const props = decideProps();
    render(h(Theme, null, h(ApprovalInteraction, { group: group!, state: 'active', ...props })));
    expect(screen.getAllByTestId('approval-group-item')).toHaveLength(4);
    expect(screen.getAllByTestId('approval-group-item').every((item) => item.getAttribute('data-status') === 'pending')).toBe(true);
    expect(screen.getByTestId('allow-all').textContent).toBe('Allow all 4');
    expect(screen.getByTestId('allow-conversation').textContent).toBe('Allow SearXNG for this conversation');
    fireEvent.click(screen.getByTestId('allow-conversation'));
    expect(props.onDecideAll.mock.calls[0]?.[0]).toHaveLength(4);
    expect(props.onDecideAll.mock.calls[0]?.[2]).toBe('conversation');
  });

  it('FG-F6 (UXINT-0): three asks that waited together stay one card while "Review each" decides them in any order', () => {
    const base = recordedCards(false).filter((card) => card.toolName === 'web@searxng/web_search').slice(0, 3);
    const props = decideProps();
    const at = (statuses: PendingApproval['status'][]) => ({
      key: 'g',
      items: base.map((card, i) => ({ ...card, status: statuses[i] ?? 'pending' })),
    });
    const { rerender } = render(h(Theme, null, h(ApprovalInteraction, { group: at([]), state: 'active', ...props })));
    fireEvent.click(screen.getByTestId('review-each'));
    // The second, then the third: the first keeps its own Allow once inside the same card.
    for (const statuses of [['pending', 'approved', 'pending'], ['pending', 'approved', 'approved']] as PendingApproval['status'][][]) {
      rerender(h(Theme, null, h(ApprovalInteraction, { group: at(statuses), state: 'active', ...props })));
      expect(screen.getByTestId('approval-group')).toBeTruthy();
      const rows = screen.getAllByTestId('approval-group-item');
      expect(rows.map((row) => row.getAttribute('data-status'))).toEqual(statuses);
      expect(screen.getAllByRole('button', { name: /^Allow once/ }).length).toBeGreaterThan(0);
    }
  });

  it('FG-F7 replay: after onyx.app is allowed for the conversation, nothing about onyx.app is left waiting', () => {
    let list = recordedCards();
    const onyx = list.filter((card) => card.reason?.destination === 'onyx.app');
    expect(onyx).toHaveLength(3);
    // The person allows the first; Node answers the other two and the stream says so, scope and all.
    for (const card of onyx) {
      list = withApprovalResolved(list, {
        interactionId: card.interactionId,
        decision: 'approved',
        scope: 'conversation',
        childRunId: card.childRunId,
        runId: card.runId,
      });
    }
    const open = list.filter(isOpenApproval);
    expect(open.some((card) => card.reason?.destination === 'onyx.app')).toBe(false);
    expect(open).toHaveLength(11);
    const decided = list.filter((card) => card.reason?.destination === 'onyx.app');
    expect(decided.every((card) => card.status === 'approved' && card.scope === 'conversation')).toBe(true);
    expect(groupApprovals(decided).every((group) => !isOpenGroup(group))).toBe(true);
  });
});
