/**
 * UXINT-1/2: the reply's interactions as one ordered list. Same-batch, same-tool, same-rule calls are one card;
 * the live copy of a saved card replaces it; order is the timeline row, then journal seq; the first open one is
 * the call to action.
 */
import { describe, expect, it } from 'vitest';
import {
  activeInteractionKey,
  approvalAnchor,
  groupApprovals,
  mergeApprovals,
  orderInteractions,
  partAnchors,
  questionAnchor,
  receiptCounts,
  openCalls,
  savedApprovals,
  slotWaitingCount,
} from '../interactions';
import type { MessagePart, PendingApproval } from '../types';

const card = (id: string, over: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: `ix.approve.${id}`,
  toolCallId: id,
  toolName: 'image_generator__generate_image',
  args: '{"prompt":"x"}',
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  runId: 'run-1',
  afterTurns: 7,
  seq: 100,
  reason: { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false },
  ...over,
});

describe('groupApprovals (D2: same tool, same rule, same batch only)', () => {
  it('UXG-1: seven image calls of one batch are one card; the order is kept', () => {
    const seven = Array.from({ length: 7 }, (_, i) => card(`t7.${i}`, { seq: 100 + i }));
    const groups = groupApprovals(seven);
    expect(groups).toHaveLength(1);
    expect(groups[0].items.map((a) => a.toolCallId)).toEqual(seven.map((a) => a.toolCallId));
  });

  it('UXG-2: another batch, tool, rule, run or sub-agent is another card', () => {
    const groups = groupApprovals([
      card('t7.0'),
      card('t8.0', { afterTurns: 8 }),
      card('t7.1', { toolName: 'web__web_search' }),
      card('t7.2', { reason: { rule: 'flow_external_write', sources: [], moreSources: 0, conversationGrant: true } }),
      card('t7.3', { runId: 'run-2' }),
      card('t7.4', { childRunId: 'child-1' }),
    ]);
    expect(groups.map((g) => g.items.length)).toEqual([1, 1, 1, 1, 1, 1]);
  });

  it('UXG-3: a card without its batch (an older server) and an unknown effect stay alone', () => {
    expect(groupApprovals([card('a', { afterTurns: undefined }), card('b', { afterTurns: undefined })])).toHaveLength(2);
    expect(groupApprovals([card('a', { kind: 'unknown_effect' }), card('b', { kind: 'unknown_effect' })])).toHaveLength(2);
  });
});

describe('saved and live cards are one list (UXINT-1 R4)', () => {
  it('UXM-1: the saved part becomes a card with its run, seq and batch', () => {
    const parts: MessagePart[] = [
      { type: 'tool_call', toolCallId: 'c1', toolName: 'x' },
      { type: 'approval', interactionId: 'ix.approve.t1.0', toolCallId: 'c1', toolName: 'x', args: '{}', status: 'approved', runId: 'run-1', seq: 12, afterTurns: 1 },
    ];
    expect(savedApprovals(parts)).toEqual([
      expect.objectContaining({ interactionId: 'ix.approve.t1.0', status: 'approved', runId: 'run-1', seq: 12, afterTurns: 1 }),
    ]);
  });

  it('UXM-2: a live copy replaces its saved card in place; a new live card follows', () => {
    const saved = [card('a', { status: 'pending' })];
    const merged = mergeApprovals(saved, [card('b'), card('a', { status: 'approved', afterTurns: undefined })]);
    expect(merged.map((a) => [a.toolCallId, a.status, a.afterTurns])).toEqual([
      ['a', 'approved', 7],
      ['b', 'pending', 7],
    ]);
  });
});

describe('order and the one call to action (D1)', () => {
  const parts: MessagePart[] = [
    { type: 'tool_call', toolCallId: 'ask', toolName: 'internaltools__ask_user_question' },
    { type: 'tool_call', toolCallId: 'c1', toolName: 'jira__create_issue' },
    { type: 'sub_agent', runId: 'child-1' },
    { type: 'tool_call', toolCallId: 'c2', toolName: 'jira__create_issue' },
  ];
  const rows = new Map<string, number>();
  parts.forEach((part, i) => partAnchors(part).forEach((id) => rows.set(id, i)));

  it('UXO-1: anchors are the tool call, or the sub-agent row for its own cards', () => {
    expect(approvalAnchor(card('c1'))).toBe('c1');
    expect(approvalAnchor(card('c9', { childRunId: 'child-1' }))).toBe('run:child-1');
    expect(questionAnchor(parts)).toBe('ask');
    expect(questionAnchor([])).toBeNull();
    // An engine's saved approval names its interaction, not a row: it must not anchor itself, or it would vanish.
    expect(partAnchors({ type: 'approval', interactionId: 'ix.approve.k1', toolCallId: 'ix.approve.k1' })).toEqual([]);
  });

  it('UXO-2: the question asked first stays first, whatever arrived last; rows before seq; no row is last', () => {
    const ordered = orderInteractions(
      [
        { key: 'late-row', anchor: 'c2', open: true, seq: 1 },
        { key: 'nowhere', anchor: 'gone', open: true, seq: 0 },
        { key: 'child', anchor: 'run:child-1', open: true, seq: 50 },
        { key: 'c1-b', anchor: 'c1', open: false, seq: 20 },
        { key: 'c1-a', anchor: 'c1', open: true, seq: 10 },
        { key: 'question', anchor: 'ask', open: false, seq: -1 },
      ],
      (anchor) => rows.get(anchor),
    );
    expect(ordered.map((i) => i.key)).toEqual(['question', 'c1-a', 'c1-b', 'child', 'late-row', 'nowhere']);
    expect(activeInteractionKey(ordered)).toBe('c1-a');
    expect(activeInteractionKey(ordered.map((i) => ({ ...i, open: false })))).toBeNull();
  });
});

describe('receipts and the waiting count', () => {
  it('UXR-1: a group counts what each call became', () => {
    const group = {
      key: 'g',
      items: [
        card('a', { status: 'approved' }),
        card('b', { status: 'approved', scope: 'conversation' }),
        card('c', { status: 'denied' }),
        card('d', { status: 'expired' }),
      ],
    };
    expect(receiptCounts(group)).toEqual({ approved: 1, conversation: 1, denied: 1, lapsed: 1 });
  });

  it('UXR-2 (WP-71): the slot counts each call waiting for the person, as the card\'s "Allow all N" does, and its open question once', () => {
    const pendingApprovals = [card('t7.0'), card('t7.1'), card('t8.0', { afterTurns: 8 }), card('t9.0', { status: 'approved', afterTurns: 9 })];
    expect(slotWaitingCount({ isStreaming: true, pendingApprovals, pendingAskUserQuestion: null })).toBe(3);
    expect(groupApprovals(pendingApprovals).map(openCalls)).toEqual([2, 1, 0]);
    expect(slotWaitingCount({ isStreaming: false, pendingApprovals, pendingAskUserQuestion: null })).toBe(0);
    expect(
      slotWaitingCount({
        isStreaming: false,
        pendingApprovals: [],
        pendingAskUserQuestion: { assistantMessageId: 'r', payload: { name: 'ask_user_question', questions: [] }, answers: {}, status: 'pending' },
      }),
    ).toBe(1);
    expect(slotWaitingCount(undefined)).toBe(0);
  });
});
