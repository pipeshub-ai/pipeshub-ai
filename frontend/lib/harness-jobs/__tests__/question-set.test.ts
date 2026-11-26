import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadHistoricalMessages } from '@/chat/runtime';
import type { ConversationMessage, PendingAskUserQuestion } from '@/chat/types';
import { trailFromFrames } from '../durable-trail';
import { needsYou } from '../needs-you';
import { advance, answerTyped, back, openStepper, pick, stepView, type as typeWords } from '../question-stepper';
import { words } from './fixtures';

/**
 * A recorded conversation (FIX-UI2): the agent asks three trip questions, the person answers them, and the agent
 * asks three more. The loader merges both asks into one set of six with the first three answered; the new interface
 * drew all six in one tall card (answered ones again, some pre-ticked), and the trail repeated each question as a
 * waiting row. The classic view asks "Step 4 of 6", one question at a time.
 */
const FIXTURE = JSON.parse(readFileSync(path.join(__dirname, 'data/trip-questions.json'), 'utf8')) as { messages: ConversationMessage[] };

function loaded() {
  const { messages, unansweredAskUserQuestion } = loadHistoricalMessages(FIXTURE.messages);
  if (!unansweredAskUserQuestion) throw new Error('the recorded trip chat has a question waiting');
  const pending: PendingAskUserQuestion = unansweredAskUserQuestion;
  return { messages, pending };
}

describe('the recorded trip question set', () => {
  it('is one set of six, three of them answered', () => {
    const { pending } = loaded();
    expect(pending.payload.questions).toHaveLength(6);
    const start = openStepper(pending.payload, pending.answers);
    const view = stepView(pending.payload, start);
    expect(view).toMatchObject({ index: 3, total: 6, answered: 3, complete: false, sends: false });
    expect(view?.question.question).toBe('How long do you want to stay?');
  });

  it('the card asks one question at a time, on the first still open, and never the answered ones again', () => {
    const { messages, pending } = loaded();
    const newest = [...messages].reverse().find((m) => m.role === 'assistant');
    const items = needsYou(
      { isStreaming: false, pendingApprovals: [], pendingAskUserQuestion: pending, newestReplyId: String(newest?.id), newestParts: [], streamingParts: [], canDecide: true, now: Date.parse('2026-11-09T12:00:00Z') },
      words,
    );
    expect(items).toHaveLength(1);
    const [item] = items;
    expect(item.source).toBe('question');
    expect(item.ask.count).toBe(6);
    expect(item.ask.progress).toEqual({ answered: 3, total: 6 });
  });

  it('Back keeps the earlier answers; a multi-select keeps its picks; a one-of pick moves on', () => {
    const { pending } = loaded();
    const p = pending.payload;
    let s = openStepper(p, pending.answers);
    s = back(s);
    const focus = stepView(p, s);
    expect(focus?.question.multiSelect).toBe(true);
    expect([...(focus?.picked ?? [])].sort()).toEqual(['opt_nightlife_and_entertainment', 'opt_outdoor_adventure']);
    s = advance(p, s);
    expect(stepView(p, s)?.index).toBe(3);
    s = pick(p, s, 'opt_5–7_nights');
    expect(stepView(p, s)).toMatchObject({ index: 4, answered: 4 });
    s = typeWords(p, s, 'opt_enter_a_total_budget_and_whether_flights_are_included', '$5k with flights');
    expect(stepView(p, s)).toMatchObject({ index: 4, ready: true, answered: 5, sends: false });
    s = advance(p, s);
    const last = answerTyped(p, s, 'Seattle');
    expect(last.complete).toBe(true);
    expect(stepView(p, last.state)).toMatchObject({ answered: 6, complete: true });
  });

  it('typing in the message box answers the question shown and moves on', () => {
    const { pending } = loaded();
    const { state, complete } = answerTyped(pending.payload, openStepper(pending.payload, pending.answers), 'Ten nights');
    expect(complete).toBe(false);
    expect(stepView(pending.payload, state)).toMatchObject({ index: 4, answered: 4 });
  });

  it('a live ask of three is one waiting row in the durable trail too', () => {
    const tool = FIXTURE.messages.find((m) => m.messageType === 'tool_call') as unknown as { tools: Array<{ toolResult: unknown }> };
    const trail = trailFromFrames([{ type: 'CUSTOM', name: 'ask_user_question', value: { toolData: tool.tools[0].toolResult } } as never], words);
    const decisions = trail.entries.filter((e) => e.kind === 'decision');
    expect(decisions).toEqual([expect.objectContaining({ title: 'Asked you 3 questions · waiting for you', state: 'waiting' })]);
  });
});
