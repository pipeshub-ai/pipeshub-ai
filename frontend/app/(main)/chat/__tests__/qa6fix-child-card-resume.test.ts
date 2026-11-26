/**
 * QA6-F12: a sub-agent's card on its parent's stream. The page keeps one card
 * per interaction whatever id a frame arrives under, and a reload restores the
 * sub-agents' open cards with the saved stream id that names them (the server
 * does not announce a card that id says the reader holds).
 */
import { describe, it, expect, vi } from 'vitest';
import type { SSEEvent } from '@/lib/api';
import { createAGUIEventHandler, type AGUIHandlerCheckpoint } from '../agui-event-handler';
import { withApprovalRequested, withApprovalResolved } from '../approvals';
import type { PendingApproval, SSEApprovalRequestedEvent, SSEApprovalResolvedEvent } from '../types';
import type { StreamMessageCallbacks } from '../api';

const RUN = 'run-parent';
const CHILD = 'run-child';

const card = (interactionId: string, childRunId?: string): SSEApprovalRequestedEvent => ({
  interactionId,
  toolCallId: `call-${interactionId}`,
  toolName: 'slack__post_message',
  args: '{}',
  argsSummary: null,
  ...(childRunId ? { childRunId, childRole: 'research' } : {}),
});

const custom = (id: string, name: string, value: unknown): SSEEvent => ({
  id,
  event: 'CUSTOM',
  data: { type: 'CUSTOM', name, value, runId: RUN },
});

/** A reader whose callbacks keep the cards as the chat slot does. */
function reader(resumeFrom?: AGUIHandlerCheckpoint) {
  let cards: PendingApproval[] = [];
  const onApprovalRequested = vi.fn((data: SSEApprovalRequestedEvent) => {
    cards = withApprovalRequested(cards, data);
  });
  const onApprovalResolved = vi.fn((data: SSEApprovalResolvedEvent) => {
    cards = withApprovalResolved(cards, data);
  });
  const callbacks: StreamMessageCallbacks = { onApprovalRequested, onApprovalResolved };
  const handler = createAGUIEventHandler(callbacks, undefined, resumeFrom ? { resumeFrom } : {});
  return { handler, cards: () => cards.map((c) => [c.childRunId ?? null, c.interactionId, c.status]), onApprovalRequested };
}

const saved = (handler: ReturnType<typeof reader>['handler']): AGUIHandlerCheckpoint =>
  JSON.parse(JSON.stringify(handler.checkpoint())) as AGUIHandlerCheckpoint;

describe('QA6-F12: one card per interaction, whatever id the frame carries', () => {
  it('F12-9: the same interaction announced under two ids is one card', () => {
    const page = reader();
    page.handler(custom('j:3.3/0.0.0^3.14+1', 'approval_requested', card('ix.approve.t1.0', CHILD)));
    page.handler(custom('j:9.1/0.0.0^3.14+1', 'approval_requested', card('ix.approve.t1.0', CHILD)));
    expect(page.cards()).toEqual([[CHILD, 'ix.approve.t1.0', 'pending']]);
  });

  it('F12-9: two interactions under one id are two cards (an older server repeats an id)', () => {
    const page = reader();
    page.handler(custom('j:3.3/0.0.0', 'approval_requested', card('ix.approve.t1.0', CHILD)));
    page.handler(custom('j:3.3/0.0.0', 'approval_requested', card('ix.approve.t1.0')));
    page.handler(custom('j:3.3/0.0.0', 'unknown_effect', { ...card('ix.unknown.t1.0', CHILD), args: undefined }));
    expect(page.cards()).toEqual([
      [CHILD, 'ix.approve.t1.0', 'pending'],
      [null, 'ix.approve.t1.0', 'pending'],
      [CHILD, 'ix.unknown.t1.0', 'pending'],
    ]);
  });
});

describe('QA6-F12: a reload keeps the sub-agent cards its saved id names', () => {
  it('F12-9: a saved state restores a sub-agent\'s open card before any frame arrives', () => {
    const before = reader();
    before.handler(custom('j:3.3/0.0.0^3.14+1', 'approval_requested', card('ix.approve.t1.0', CHILD)));
    const reloaded = reader(saved(before.handler));
    expect(reloaded.cards()).toEqual([]);
    reloaded.handler.publishView();
    expect(reloaded.cards()).toEqual([[CHILD, 'ix.approve.t1.0', 'pending']]);
  });

  it('F12-9: the decision that follows the resume closes the restored card', () => {
    const before = reader();
    before.handler(custom('j:3.3/0.0.0^3.14+1', 'unknown_effect', card('ix.unknown.t1.0', CHILD)));
    const reloaded = reader(saved(before.handler));
    reloaded.handler.publishView();
    reloaded.handler(custom('j:3.3/0.0.0+2', 'unknown_effect_resolved', { interactionId: 'ix.unknown.t1.0', decision: 'answered', childRunId: CHILD }));
    expect(reloaded.cards()).toEqual([[CHILD, 'ix.unknown.t1.0', 'answered']]);
    expect(saved(reloaded.handler).childCards).toEqual([]);
  });

  it('F12-9: a card decided before the reload is not restored', () => {
    const before = reader();
    before.handler(custom('j:3.3/0.0.0^3.14+1', 'approval_requested', card('ix.approve.t1.0', CHILD)));
    before.handler(custom('j:3.3/0.0.0+2', 'approval_resolved', { interactionId: 'ix.approve.t1.0', decision: 'denied', childRunId: CHILD }));
    const reloaded = reader(saved(before.handler));
    reloaded.handler.publishView();
    expect(reloaded.cards()).toEqual([]);
  });

  it('F12-9: the run\'s own cards are not saved here: its timeline names the ones still waiting', () => {
    const before = reader();
    before.handler(custom('j:5.1/0.0.0', 'approval_requested', card('ix.approve.t2.0')));
    expect(saved(before.handler).childCards).toEqual([]);
  });

  it('F12-9: a state saved before cards were kept restores none', () => {
    const before = reader();
    const { childCards: _dropped, ...older } = saved(before.handler);
    const reloaded = reader(older as AGUIHandlerCheckpoint);
    reloaded.handler.publishView();
    expect(reloaded.onApprovalRequested).not.toHaveBeenCalled();
    expect(saved(reloaded.handler).childCards).toEqual([]);
  });
});
