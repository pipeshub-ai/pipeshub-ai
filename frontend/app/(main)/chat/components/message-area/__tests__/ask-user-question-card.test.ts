import { describe, it, expect } from 'vitest';
import {
  mergeAskUserQuestionPayloads,
  normalizeAskUserQuestionPayload,
  parseAnswerMessage,
} from '../ask-user-question-card';
import type { AskUserQuestionPayload } from '../../../types';

describe('normalizeAskUserQuestionPayload', () => {
  it('fills missing question uuids and option ids', () => {
    const normalized = normalizeAskUserQuestionPayload({
      name: 'ask_user_question',
      questions: [{
        question: 'Which region?',
        options: ['EU', 'US'],
      } as AskUserQuestionPayload['questions'][number]],
    });

    expect(normalized.questions[0].uuid).toBeTruthy();
    expect(normalized.questions[0].options.map((o) => o.label)).toEqual(['EU', 'US']);
    expect(normalized.questions[0].options.every((o) => o.id)).toBe(true);
  });
});

describe('parseAnswerMessage', () => {
  it('recovers selections when the stored payload has no uuids', () => {
    const payload = {
      name: 'ask_user_question' as const,
      questions: [{
        question: 'What should I analyze after you paste the data?',
        options: [
          { label: 'Summarize the dataset' },
          { label: 'Calculate totals and averages' },
        ],
      }],
    } as unknown as AskUserQuestionPayload;

    const answers = parseAnswerMessage(
      'User selections:\n1. "What should I analyze after you paste the data?" → Summarize the dataset',
      payload,
    );

    const q = normalizeAskUserQuestionPayload(payload).questions[0];
    expect(answers[q.uuid]?.selectedOptionIds).toEqual([q.options[0].id]);
  });

  it('accepts an ASCII arrow in the saved resume query', () => {
    const payload = {
      name: 'ask_user_question' as const,
      questions: [{
        uuid: 'q1',
        question: 'Which region?',
        options: [{ id: 'eu', label: 'EU' }, { id: 'us', label: 'US' }],
      }],
    } as AskUserQuestionPayload;

    const answers = parseAnswerMessage(
      'User selections:\n1. "Which region?" -> EU',
      payload,
    );
    expect(answers.q1.selectedOptionIds).toEqual(['eu']);
  });
});

describe('mergeAskUserQuestionPayloads', () => {
  it('appends a later one-question tool call instead of replacing', () => {
    const first: AskUserQuestionPayload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q1',
        question: 'What should I analyze?',
        options: [{ id: 'a', label: 'Summarize', isUserInput: false }],
        multiSelect: true,
      }],
    };
    const second: AskUserQuestionPayload = {
      name: 'ask_user_question',
      questions: [{
        uuid: 'q2',
        question: 'Which format?',
        options: [{ id: 'b', label: 'Table', isUserInput: false }],
        multiSelect: false,
      }],
    };

    const merged = mergeAskUserQuestionPayloads(first, second);
    expect(merged.questions.map((q) => q.question)).toEqual([
      'What should I analyze?',
      'Which format?',
    ]);
  });
});
