import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadHistoricalMessages } from '@/chat/runtime';
import type { AskUserQuestionAnswer, AskUserQuestionPayload, ConversationMessage, PendingAskUserQuestion } from '@/chat/types';
import { trailFromFrames } from '../durable-trail';
import { needsYou } from '../needs-you';
import { advance, answerTyped, back, openStepper, pick, stepView, type as typeWords } from '../question-stepper';
import { words } from './fixtures';

/**
 * A recorded conversation (FIX-UI2): the agent asks three trip questions, the person answers them, and the agent
 * asks three more. The new interface drew all six in one tall card (answered ones again, some pre-ticked), and the
 * trail repeated each question as a waiting row. Here the waiting card holds only the three new questions, asked one
 * at a time ("Step 1 of 3"); the first ask's three answers are kept, never asked again.
 */
const FIXTURE = JSON.parse(readFileSync(path.join(__dirname, 'data/trip-questions.json'), 'utf8')) as { messages: ConversationMessage[] };

function loaded() {
  const { messages, unansweredAskUserQuestion } = loadHistoricalMessages(FIXTURE.messages);
  if (!unansweredAskUserQuestion) throw new Error('the recorded trip chat has a question waiting');
  const pending: PendingAskUserQuestion = unansweredAskUserQuestion;
  return { messages, pending };
}

/** The questions each recorded ask put, in order: the first ask's three, then the second's. */
const ASKS = FIXTURE.messages
  .filter((m) => m.messageType === 'tool_call')
  .map((m) => (m as unknown as { tools: Array<{ toolResult: AskUserQuestionPayload }> }).tools[0].toolResult.questions);

/** The answers the reply's row keeps, by question. */
function keptAnswers(messages: ReturnType<typeof loaded>['messages']): Record<string, AskUserQuestionAnswer> {
  const kept = messages
    .map((m) => (m.metadata?.custom as { persistedAskUserQuestionAnswers?: Record<string, AskUserQuestionAnswer> } | undefined)?.persistedAskUserQuestionAnswers)
    .find((answers) => answers !== undefined);
  if (!kept) throw new Error('the recorded trip chat keeps the first ask’s answers');
  return kept;
}

describe('the recorded trip question set', () => {
  it('is its own set of three, none answered yet; the first ask’s three answers are kept, not asked again', () => {
    const { messages, pending } = loaded();
    const [first, second] = ASKS;
    expect(pending.payload.questions.map((q) => q.uuid)).toEqual(second.map((q) => q.uuid));
    const start = openStepper(pending.payload, pending.answers);
    const view = stepView(pending.payload, start);
    expect(view).toMatchObject({ index: 0, total: 3, answered: 0, complete: false, sends: false });
    expect(view?.question.question).toBe('How long do you want to stay?');
    expect(Object.keys(keptAnswers(messages)).sort()).toEqual(first.map((q) => q.uuid).sort());
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
    expect(item.ask.count).toBe(3);
    expect(item.ask.progress).toEqual({ answered: 0, total: 3 });
  });

  it('Back keeps the earlier answers; a multi-select keeps its picks; a one-of pick moves on', () => {
    const { messages, pending } = loaded();
    // The first ask's multi-select keeps both its picks.
    const focus = ASKS[0].find((q) => q.multiSelect);
    expect([...(keptAnswers(messages)[focus?.uuid ?? '']?.selectedOptionIds ?? [])].sort()).toEqual(['opt_nightlife_and_entertainment', 'opt_outdoor_adventure']);
    const p = pending.payload;
    let s = openStepper(p, pending.answers);
    s = pick(p, s, 'opt_5–7_nights');
    expect(stepView(p, s)).toMatchObject({ index: 1, answered: 1 });
    s = typeWords(p, s, 'opt_enter_a_total_budget_and_whether_flights_are_included', '$5k with flights');
    expect(stepView(p, s)).toMatchObject({ index: 1, ready: true, answered: 2, sends: false });
    s = back(s);
    const stay = stepView(p, s);
    expect(stay).toMatchObject({ index: 0, answered: 2 });
    expect([...(stay?.picked ?? [])]).toEqual(['opt_5–7_nights']);
    // On to the first still open: the budget, answered, is not asked again.
    s = advance(p, s);
    expect(stepView(p, s)).toMatchObject({ index: 2, sends: true });
    const last = answerTyped(p, s, 'Seattle');
    expect(last.complete).toBe(true);
    expect(stepView(p, last.state)).toMatchObject({ answered: 3, complete: true });
  });

  it('typing in the message box answers the question shown and moves on', () => {
    const { pending } = loaded();
    const { state, complete } = answerTyped(pending.payload, openStepper(pending.payload, pending.answers), 'Ten nights');
    expect(complete).toBe(false);
    expect(stepView(pending.payload, state)).toMatchObject({ index: 1, answered: 1 });
  });

  it('a live ask of three is one waiting row in the durable trail too', () => {
    const tool = FIXTURE.messages.find((m) => m.messageType === 'tool_call') as unknown as { tools: Array<{ toolResult: unknown }> };
    const trail = trailFromFrames([{ type: 'CUSTOM', name: 'ask_user_question', value: { toolData: tool.tools[0].toolResult } } as never], words);
    const decisions = trail.entries.filter((e) => e.kind === 'decision');
    expect(decisions).toEqual([expect.objectContaining({ title: 'Asked you 3 questions · waiting for you', state: 'waiting' })]);
  });
});
