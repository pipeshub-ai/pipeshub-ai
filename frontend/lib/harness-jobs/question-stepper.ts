import {
  SOMETHING_ELSE_ID,
  answerForQuestion,
  isQuestionAnswered,
  normalizeAskUserQuestionPayload,
} from '@/chat/components/message-area/ask-user-question-card';
import type { AskUserQuestionAnswer, AskUserQuestionItem, AskUserQuestionPayload } from '@/chat/types';

/**
 * A question set, asked one question at a time (design `decision-card.tsx` steps; the classic card's stepper).
 *
 * A run that asks again after an answer carries the earlier questions with their answers (the loader merges the two
 * asks into one payload), so the set is read the way the classic card reads it: each question normalized, each answer
 * found by the classic matcher, and the card opening on the first question still open ("Question 4 of 6"). An answered
 * question is never asked again; Back shows it with its answer kept. Pure: no React, no store.
 */

export interface StepperState {
  step: number;
  /** Keyed by each question's normalized id. */
  answers: Record<string, AskUserQuestionAnswer>;
}

export const questionsOf = (payload: AskUserQuestionPayload): AskUserQuestionItem[] => normalizeAskUserQuestionPayload(payload).questions;

const emptyAnswer = (q: AskUserQuestionItem): AskUserQuestionAnswer => ({ questionUuid: q.uuid, selectedOptionIds: [], userInputs: {} });

/** Each question's answer under its own id, whatever ids the answers were saved under. */
export function keyedAnswers(questions: AskUserQuestionItem[], answers: Record<string, AskUserQuestionAnswer> | undefined): Record<string, AskUserQuestionAnswer> {
  const out: Record<string, AskUserQuestionAnswer> = {};
  if (!answers) return out;
  for (const q of questions) {
    const a = answerForQuestion(q, answers, questions);
    if (a) out[q.uuid] = { ...a, questionUuid: q.uuid };
  }
  return out;
}

export const answeredCount = (questions: AskUserQuestionItem[], answers: Record<string, AskUserQuestionAnswer>): number =>
  questions.filter((q) => isQuestionAnswered(q, answers, questions)).length;

function firstOpen(questions: AskUserQuestionItem[], answers: Record<string, AskUserQuestionAnswer>, from = 0): number {
  for (let i = from; i < questions.length; i += 1) if (!isQuestionAnswered(questions[i], answers, questions)) return i;
  for (let i = 0; i < from; i += 1) if (!isQuestionAnswered(questions[i], answers, questions)) return i;
  return -1;
}

/** The card's opening state: the answers it already has, on the first question still open. */
export function openStepper(payload: AskUserQuestionPayload, answers: Record<string, AskUserQuestionAnswer> | undefined): StepperState {
  const questions = questionsOf(payload);
  const keyed = keyedAnswers(questions, answers);
  const open = firstOpen(questions, keyed);
  return { step: open >= 0 ? open : Math.max(0, questions.length - 1), answers: keyed };
}

export interface StepView {
  index: number;
  total: number;
  answered: number;
  question: AskUserQuestionItem;
  picked: ReadonlyArray<string>;
  /** Words typed for a pick that asks for them, by option id ("Something else" is `SOMETHING_ELSE_ID`). */
  typed: Readonly<Record<string, string>>;
  /** The question shown has a usable answer: Next (or Send on the last open one) is live. */
  ready: boolean;
  /** Every question has an answer: the set can be sent. */
  complete: boolean;
  /** No other question is still open: the button here sends rather than moves on. */
  sends: boolean;
}

export function stepView(payload: AskUserQuestionPayload, state: StepperState): StepView | null {
  const questions = questionsOf(payload);
  const index = Math.min(Math.max(0, state.step), questions.length - 1);
  const question = questions[index];
  if (!question) return null;
  const answer = state.answers[question.uuid];
  const answered = answeredCount(questions, state.answers);
  const ready = isQuestionAnswered(question, state.answers, questions);
  const othersOpen = questions.some((q, i) => i !== index && !isQuestionAnswered(q, state.answers, questions));
  return {
    index,
    total: questions.length,
    answered,
    question,
    picked: answer?.selectedOptionIds ?? [],
    typed: answer?.userInputs ?? {},
    ready,
    complete: answered === questions.length,
    sends: !othersOpen,
  };
}

function update(payload: AskUserQuestionPayload, state: StepperState, change: (q: AskUserQuestionItem, a: AskUserQuestionAnswer) => AskUserQuestionAnswer): StepperState {
  const questions = questionsOf(payload);
  const q = questions[state.step];
  if (!q) return state;
  const next = change(q, state.answers[q.uuid] ?? emptyAnswer(q));
  return { ...state, answers: { ...state.answers, [q.uuid]: next } };
}

/**
 * Picking an option. One-of: the pick answers the question and the card moves to the next open one (picking is
 * answering, design `decision-card.tsx`), unless the pick asks for words first. Any-of: the pick toggles and the
 * person presses Next.
 */
export function pick(payload: AskUserQuestionPayload, state: StepperState, optionId: string): StepperState {
  const questions = questionsOf(payload);
  const q = questions[state.step];
  if (!q) return state;
  const next = update(payload, state, (_q, a) => {
    if (q.multiSelect) {
      const on = a.selectedOptionIds.includes(optionId);
      return { ...a, selectedOptionIds: on ? a.selectedOptionIds.filter((id) => id !== optionId) : [...a.selectedOptionIds, optionId] };
    }
    return { ...a, selectedOptionIds: [optionId] };
  });
  const wantsWords = q.options.find((o) => o.id === optionId)?.isUserInput || optionId === SOMETHING_ELSE_ID;
  if (q.multiSelect || wantsWords) return next;
  return advance(payload, next);
}

/** Words for a pick that asks for them (or "Something else"): typing selects it, clearing the words drops it. */
export function type(payload: AskUserQuestionPayload, state: StepperState, optionId: string, text: string): StepperState {
  const questions = questionsOf(payload);
  const q = questions[state.step];
  if (!q) return state;
  return update(payload, state, (_q, a) => {
    const rest = a.selectedOptionIds.filter((id) => id !== optionId);
    const has = text.trim().length > 0;
    const selectedOptionIds = has ? (q.multiSelect ? [...rest, optionId] : [optionId]) : optionId === SOMETHING_ELSE_ID ? rest : a.selectedOptionIds;
    return { ...a, selectedOptionIds, userInputs: { ...a.userInputs, [optionId]: text } };
  });
}

/** Next: the next question still open after this one, else the next one along. */
export function advance(payload: AskUserQuestionPayload, state: StepperState): StepperState {
  const questions = questionsOf(payload);
  const open = firstOpen(questions, state.answers, state.step + 1);
  if (open >= 0 && open !== state.step) return { ...state, step: open };
  return { ...state, step: Math.min(state.step + 1, questions.length - 1) };
}

export const back = (state: StepperState): StepperState => ({ ...state, step: Math.max(0, state.step - 1) });

/**
 * What is typed in the message box while the card is up answers the question it shows, in the person's own words,
 * and the card moves on. `complete` says the set now has every answer, so the caller sends it.
 */
export function answerTyped(payload: AskUserQuestionPayload, state: StepperState, text: string): { state: StepperState; complete: boolean } {
  const typed = type(payload, state, SOMETHING_ELSE_ID, text);
  const questions = questionsOf(payload);
  const complete = answeredCount(questions, typed.answers) === questions.length;
  return { state: complete ? typed : advance(payload, typed), complete };
}

/** What the person answered to one question, in their words: picked labels, and typed text where they typed. */
export function answerLine(q: AskUserQuestionItem, a: AskUserQuestionAnswer | undefined): string {
  if (!a) return '';
  return a.selectedOptionIds
    .map((id) => {
      const typed = a.userInputs?.[id]?.trim();
      if (typed) return typed;
      if (id === SOMETHING_ELSE_ID) return '';
      return q.options.find((o) => o.id === id)?.label ?? '';
    })
    .filter(Boolean)
    .join(', ');
}
