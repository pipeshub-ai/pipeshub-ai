import { describe, it, expect } from 'vitest';
import {
  askUserQuestionOwnsRow,
  buildAnswerMessage,
  hasUnansweredQuestions,
  mergeAskUserQuestionPayloads,
  normalizeAskUserQuestionPayload,
  parseAnswerMessage,
} from '../ask-user-question-card';
import type { AskUserQuestionPayload, PendingAskUserQuestion } from '../../../types';

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

/** Shape the backend sends: option ids derive from the label alone, so two
 *  questions offering the same label carry the same option id. */
function sharedOptionPayload(): AskUserQuestionPayload {
  return {
    name: 'ask_user_question',
    questions: [
      {
        uuid: 'q-project',
        question: 'Which project?',
        options: [
          { id: 'opt_apollo', label: 'Apollo', isUserInput: false },
          { id: 'opt_all', label: 'All', isUserInput: false },
        ],
        multiSelect: false,
      },
      {
        uuid: 'q-status',
        question: 'Which status?',
        options: [
          { id: 'opt_open', label: 'Open', isUserInput: false },
          { id: 'opt_all', label: 'All', isUserInput: false },
        ],
        multiSelect: false,
      },
    ],
  };
}

describe('ask_user_question answers', () => {
  it('does not treat one question as answered because a sibling shares an option id', () => {
    const answers = {
      'q-project': {
        questionUuid: 'q-project',
        selectedOptionIds: ['opt_all'],
        userInputs: {},
      },
    };

    expect(hasUnansweredQuestions(sharedOptionPayload(), answers)).toBe(true);
  });

  it('reports every question answered once each has its own selection', () => {
    const answers = {
      'q-project': {
        questionUuid: 'q-project',
        selectedOptionIds: ['opt_all'],
        userInputs: {},
      },
      'q-status': {
        questionUuid: 'q-status',
        selectedOptionIds: ['opt_open'],
        userInputs: {},
      },
    };

    expect(hasUnansweredQuestions(sharedOptionPayload(), answers)).toBe(false);
  });

  it('never reports a selection the user did not make for a shared option', () => {
    const answers = {
      'q-project': {
        questionUuid: 'q-project',
        selectedOptionIds: ['opt_all'],
        userInputs: {},
      },
    };

    const message = buildAnswerMessage(sharedOptionPayload(), answers);

    expect(message).toContain('1. "Which project?" → All');
    expect(message).toContain('2. "Which status?" → ');
    expect(message).not.toContain('"Which status?" → All');
  });

  it('still matches an answer whose uuid belongs to no question in the card', () => {
    // History-parsed answers can carry ids from an older payload; that fallback
    // is what the sibling guard must not break.
    const answers = {
      stale: {
        questionUuid: 'stale-uuid',
        selectedOptionIds: ['opt_open'],
        userInputs: {},
      },
    };

    const message = buildAnswerMessage(sharedOptionPayload(), answers);

    expect(message).toContain('2. "Which status?" → Open');
  });
});

describe('askUserQuestionOwnsRow', () => {
  const pending = (assistantMessageId: string): PendingAskUserQuestion => ({
    assistantMessageId,
    payload: sharedOptionPayload(),
    answers: {},
    status: 'pending',
  });

  it('matches the row the live send asked on, by thread row id', () => {
    expect(askUserQuestionOwnsRow(pending('row-2'), 'row-2', 'mongo-2')).toBe(true);
  });

  it('matches the regenerated row, whose card is keyed by the backend id', () => {
    expect(askUserQuestionOwnsRow(pending('mongo-2'), 'row-2', 'mongo-2')).toBe(true);
  });

  it('leaves every other row in the thread alone', () => {
    expect(askUserQuestionOwnsRow(pending('mongo-2'), 'row-1', 'mongo-1')).toBe(false);
  });

  it('claims no row when a card has ids the row does not carry', () => {
    expect(askUserQuestionOwnsRow(pending('mongo-2'), undefined, undefined)).toBe(false);
  });
});
