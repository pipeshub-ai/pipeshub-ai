import { describe, it, expect } from 'vitest';
import {
  askUserQuestionOwnsRow,
  buildAnswerMessage,
  buildResumeMessage,
  hasUnansweredQuestions,
  isAskUserQuestionExpired,
  isAskUserQuestionResumeQuery,
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

  it('keeps the header, descriptions and the expiry, and drops a recommended mark from an older card', () => {
    const normalized = normalizeAskUserQuestionPayload({
      name: 'ask_user_question',
      expiresAt: '2026-10-19T10:00:00Z',
      questions: [{
        uuid: 'q', question: 'Which region?', multiSelect: false, header: ' Region ',
        options: [
          { id: 'eu', label: 'EU', isUserInput: false, description: ' Frankfurt ', recommended: true },
          { id: 'us', label: 'US', isUserInput: false },
        ],
      }],
    });

    expect(normalized.expiresAt).toBe('2026-10-19T10:00:00Z');
    expect(normalized.questions[0].header).toBe('Region');
    expect(normalized.questions[0].options[0]).toEqual({
      id: 'eu', label: 'EU', isUserInput: false, description: 'Frankfurt',
    });
    expect(normalized.questions[0].options[1]).toEqual({ id: 'us', label: 'US', isUserInput: false });
  });

  it('leaves a card without the new fields in the shape older cards had', () => {
    const normalized = normalizeAskUserQuestionPayload({
      name: 'ask_user_question',
      questions: [{ uuid: 'q', question: 'Which region?', multiSelect: false, options: [{ id: 'eu', label: 'EU', isUserInput: false }] }],
    });

    expect(normalized).toEqual({
      name: 'ask_user_question',
      questions: [{ uuid: 'q', question: 'Which region?', multiSelect: false, options: [{ id: 'eu', label: 'EU', isUserInput: false }] }],
    });
  });
});

describe('isAskUserQuestionExpired', () => {
  const at = (expiresAt?: string): AskUserQuestionPayload => ({
    name: 'ask_user_question', questions: [], ...(expiresAt ? { expiresAt } : {}),
  });

  it('is true once the expiry has passed', () => {
    expect(isAskUserQuestionExpired(at('2026-10-18T10:00:00Z'), Date.parse('2026-10-18T10:00:01Z'))).toBe(true);
  });

  it('is false before the expiry, and for a card that has none', () => {
    expect(isAskUserQuestionExpired(at('2026-10-18T10:00:00Z'), Date.parse('2026-10-18T09:59:59Z'))).toBe(false);
    expect(isAskUserQuestionExpired(at())).toBe(false);
    expect(isAskUserQuestionExpired(at('not a date'))).toBe(false);
  });

  it('a merged card keeps the newer expiry', () => {
    const older = { ...at('2026-10-18T10:00:00Z'), questions: [{ uuid: 'a', question: 'A?', multiSelect: false, options: [] }] };
    const newer = { ...at('2026-10-19T10:00:00Z'), questions: [{ uuid: 'b', question: 'B?', multiSelect: false, options: [] }] };
    expect(mergeAskUserQuestionPayloads(older, newer).expiresAt).toBe('2026-10-19T10:00:00Z');
  });
});

describe('mergeAskUserQuestionPayloads', () => {
  const ask = (question: string): AskUserQuestionPayload => ({
    name: 'ask_user_question',
    questions: [{
      uuid: `q-${question.length}`,
      question,
      multiSelect: false,
      options: [{ id: 'a', label: 'A', isUserInput: false }],
    }],
  } as AskUserQuestionPayload);

  it('treats the same question as one even when case, spacing or the closing mark differ', () => {
    const merged = mergeAskUserQuestionPayloads(
      ask('Which fiscal year should I use?'),
      ask('  WHICH fiscal  year should I use '),
    );

    expect(merged.questions).toHaveLength(1);
    expect(merged.questions[0].question).toBe('Which fiscal year should I use?');
  });

  it('keeps a different question', () => {
    const merged = mergeAskUserQuestionPayloads(
      ask('Which fiscal year should I use?'),
      ask('Compare revenue or headcount?'),
    );

    expect(merged.questions).toHaveLength(2);
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

describe('answering and skipping, as the card sends them', () => {
  const SKIPPED = '__no_preference__';
  const ELSE = '__something_else__';
  const payload: AskUserQuestionPayload = {
    name: 'ask_user_question',
    questions: [
      {
        uuid: 'q-region',
        question: 'Which region?',
        multiSelect: false,
        options: [
          { id: 'eu', label: 'EU', isUserInput: false },
          { id: 'us', label: 'US', isUserInput: false },
        ],
      },
      {
        uuid: 'q-topics',
        question: 'What should I analyze?',
        multiSelect: true,
        options: [
          { id: 'sum', label: 'Summarize', isUserInput: false },
          { id: 'tot', label: 'Totals', isUserInput: false },
          { id: 'team', label: 'A team', isUserInput: true },
        ],
      },
    ],
  };
  const pick = (uuid: string, ids: string[], userInputs: Record<string, string> = {}) => ({
    [uuid]: { questionUuid: uuid, selectedOptionIds: ids, userInputs },
  });

  it('writes a pick as the label, not the id', () => {
    const text = buildAnswerMessage(payload, { ...pick('q-region', ['us']), ...pick('q-topics', ['sum', 'tot']) });
    expect(text).toBe('User selections:\n1. "Which region?" → US\n2. "What should I analyze?" → Summarize, Totals');
  });

  it('writes a skip as [No preference]', () => {
    const text = buildAnswerMessage(payload, { ...pick('q-region', [SKIPPED]), ...pick('q-topics', [SKIPPED]) });
    expect(text).toBe('User selections:\n1. "Which region?" → [No preference]\n2. "What should I analyze?" → [No preference]');
  });

  it('writes typed text for a something-else answer', () => {
    const text = buildAnswerMessage(payload, { ...pick('q-region', [ELSE], { [ELSE]: '  LATAM  ' }), ...pick('q-topics', ['tot']) });
    expect(text).toContain('1. "Which region?" → LATAM');
  });

  it('writes the typed text, not the label, for an option that takes input', () => {
    const text = buildAnswerMessage(payload, { ...pick('q-region', ['eu']), ...pick('q-topics', ['sum', 'team'], { team: 'Data Platform' }) });
    expect(text).toContain('2. "What should I analyze?" → Summarize, Data Platform');
  });

  it('leaves a question nobody answered empty instead of guessing', () => {
    const text = buildAnswerMessage(payload, pick('q-region', ['eu']));
    expect(text).toContain('2. "What should I analyze?" → ');
    expect(text.split('\n')[2].endsWith('→ ')).toBe(true);
  });

  it.each([
    ['a single pick', { ...pick('q-region', ['us']), ...pick('q-topics', ['tot']) }],
    ['a multi-select', { ...pick('q-region', ['eu']), ...pick('q-topics', ['sum', 'tot']) }],
    ['two skips', { ...pick('q-region', [SKIPPED]), ...pick('q-topics', [SKIPPED]) }],
    ['a skip and a pick', { ...pick('q-region', [SKIPPED]), ...pick('q-topics', ['tot']) }],
    ['typed text', { ...pick('q-region', [ELSE], { [ELSE]: 'Latin America' }), ...pick('q-topics', ['sum']) }],
  ])('parses back what it wrote for %s', (_name, answers) => {
    const parsed = parseAnswerMessage(buildAnswerMessage(payload, answers), payload);
    expect(parsed).toEqual(answers);
  });

  it('counts a skip as an answer for a single and a multi-select question', () => {
    expect(hasUnansweredQuestions(payload, { ...pick('q-region', [SKIPPED]), ...pick('q-topics', [SKIPPED]) })).toBe(false);
  });

  it('keeps asking until every question has an answer', () => {
    expect(hasUnansweredQuestions(payload, {})).toBe(true);
    expect(hasUnansweredQuestions(payload, pick('q-region', ['eu']))).toBe(true);
    expect(hasUnansweredQuestions(payload, { ...pick('q-region', ['eu']), ...pick('q-topics', ['tot']) })).toBe(false);
  });

  it('does not count a single-select with two picks as answered', () => {
    expect(hasUnansweredQuestions(payload, { ...pick('q-region', ['eu', 'us']), ...pick('q-topics', ['tot']) })).toBe(true);
  });

  it('does not count a multi-select with nothing ticked as answered', () => {
    expect(hasUnansweredQuestions(payload, { ...pick('q-region', ['eu']), ...pick('q-topics', []) })).toBe(true);
  });

  it('does not count an input option left empty as answered', () => {
    expect(hasUnansweredQuestions(payload, { ...pick('q-region', ['eu']), ...pick('q-topics', ['team'], { team: '   ' }) })).toBe(true);
    expect(hasUnansweredQuestions(payload, { ...pick('q-region', ['eu']), ...pick('q-topics', ['team'], { team: 'Data' }) })).toBe(false);
  });

  it('ignores lines that are not selections and keeps the rest', () => {
    const parsed = parseAnswerMessage('User selections:\nnoise\n1. "Which region?" → EU\n\nmore noise', payload);
    expect(Object.keys(parsed)).toEqual(['q-region']);
  });

  it('returns nothing for empty text', () => {
    expect(parseAnswerMessage('', payload)).toEqual({});
  });
});

describe('isAskUserQuestionResumeQuery', () => {
  it('recognises the card answer, with leading space too', () => {
    expect(isAskUserQuestionResumeQuery('User selections:\n1. "A?" → B')).toBe(true);
    expect(isAskUserQuestionResumeQuery('  \nUser selections:\n1. "A?" → B')).toBe(true);
  });

  it('does not take an ordinary message for one', () => {
    expect(isAskUserQuestionResumeQuery('what are the user selections: today')).toBe(false);
    expect(isAskUserQuestionResumeQuery('')).toBe(false);
    expect(isAskUserQuestionResumeQuery(undefined)).toBe(false);
    expect(isAskUserQuestionResumeQuery(null)).toBe(false);
  });
});

describe('answers that contain a comma', () => {
  const ELSE = '__something_else__';
  const options = (...labels: string[]) => labels.map((label, i) => ({ id: `o${i}`, label, isUserInput: false }));
  const payload = (multiSelect: boolean, ...labels: string[]): AskUserQuestionPayload => ({
    name: 'ask_user_question',
    questions: [{ uuid: 'q', question: 'Which metric?', multiSelect, options: options(...labels) }],
  });
  const line = (answer: string) => `User selections:\n1. "Which metric?" → ${answer}`;
  const parsed = (answer: string, p: AskUserQuestionPayload) => parseAnswerMessage(line(answer), p).q;
  const picked = (...ids: string[]) => ({ questionUuid: 'q', selectedOptionIds: ids, userInputs: {} });

  describe('single-select', () => {
    const p = payload(false, 'Revenue, net of tax', 'Headcount');

    it('keeps a label that contains a comma as that option', () => {
      expect(parsed('Revenue, net of tax', p)).toEqual(picked('o0'));
    });

    it('still reads a plain label', () => {
      expect(parsed('Headcount', p)).toEqual(picked('o1'));
    });

    it('keeps typed text that contains a comma whole', () => {
      expect(parsed('Pune, India', p)).toEqual({
        questionUuid: 'q',
        selectedOptionIds: [ELSE],
        userInputs: { [ELSE]: 'Pune, India' },
      });
    });

    it('keeps typed text that contains several commas whole', () => {
      expect(parsed('Pune, Maharashtra, India', p).userInputs[ELSE]).toBe('Pune, Maharashtra, India');
    });

    it('reads a label regardless of case', () => {
      expect(parsed('revenue, NET of tax', p)).toEqual(picked('o0'));
    });

    it('reads a skip', () => {
      expect(parsed('[No preference]', p)).toEqual(picked('__no_preference__'));
    });
  });

  describe('multi-select', () => {
    const p = payload(true, 'Revenue, net of tax', 'Headcount', 'Costs, before tax', 'Totals');

    it('keeps a comma label picked alone', () => {
      expect(parsed('Revenue, net of tax', p)).toEqual(picked('o0'));
    });

    it('keeps a comma label picked together with another option', () => {
      expect(parsed('Revenue, net of tax, Headcount', p)).toEqual(picked('o0', 'o1'));
      expect(parsed('Headcount, Revenue, net of tax', p)).toEqual(picked('o1', 'o0'));
    });

    it('keeps two comma labels picked together', () => {
      expect(parsed('Revenue, net of tax, Costs, before tax', p)).toEqual(picked('o0', 'o2'));
    });

    it('still splits plain labels', () => {
      expect(parsed('Headcount, Totals', p)).toEqual(picked('o1', 'o3'));
    });

    it('keeps typed text with a comma after an option as one piece', () => {
      expect(parsed('Totals, Pune, India', p)).toEqual({
        questionUuid: 'q',
        selectedOptionIds: ['o3', ELSE],
        userInputs: { [ELSE]: 'Pune, India' },
      });
    });

    it('keeps typed text with a comma before an option as one piece', () => {
      expect(parsed('Pune, India, Totals', p)).toEqual({
        questionUuid: 'q',
        selectedOptionIds: [ELSE, 'o3'],
        userInputs: { [ELSE]: 'Pune, India' },
      });
    });

    it('keeps typed text sitting between two options', () => {
      expect(parsed('Headcount, Pune, India, Totals', p)).toEqual({
        questionUuid: 'q',
        selectedOptionIds: ['o1', ELSE, 'o3'],
        userInputs: { [ELSE]: 'Pune, India' },
      });
    });

    it('reads typed text alone as typed text', () => {
      expect(parsed('Pune, India', p)).toEqual({
        questionUuid: 'q',
        selectedOptionIds: [ELSE],
        userInputs: { [ELSE]: 'Pune, India' },
      });
    });

    it('reads a skip', () => {
      expect(parsed('[No preference]', p)).toEqual(picked('__no_preference__'));
    });
  });

  it('prefers the longer label when one label is the start of another', () => {
    const p = payload(true, 'Revenue', 'Revenue, net of tax', 'Headcount');
    expect(parsed('Revenue, net of tax', p)).toEqual(picked('o1'));
    expect(parsed('Revenue, Headcount', p)).toEqual(picked('o0', 'o2'));
    expect(parsed('Revenue, net of tax, Headcount', p)).toEqual(picked('o1', 'o2'));
  });

  it('keeps each line to its own question', () => {
    const p: AskUserQuestionPayload = {
      name: 'ask_user_question',
      questions: [
        { uuid: 'q1', question: 'Which metric?', multiSelect: false, options: options('Revenue, net of tax', 'Headcount') },
        { uuid: 'q2', question: 'Which region?', multiSelect: true, options: options('EU, UK', 'US') },
      ],
    };
    const answers = parseAnswerMessage(
      'User selections:\n1. "Which metric?" → Revenue, net of tax\n2. "Which region?" → EU, UK, US',
      p,
    );

    expect(answers.q1.selectedOptionIds).toEqual(['o0']);
    expect(answers.q2.selectedOptionIds).toEqual(['o0', 'o1']);
  });

  it.each([
    ['a comma label', false, ['o0']],
    ['a plain label next to a comma label', true, ['o0', 'o1']],
    ['two comma labels', true, ['o0', 'o2']],
  ])('reads back what the card wrote for %s', (_name, multi, ids) => {
    const p = payload(multi, 'Revenue, net of tax', 'Headcount', 'Costs, before tax');
    const answers = { q: picked(...ids) };

    expect(parseAnswerMessage(buildAnswerMessage(p, answers), p)).toEqual(answers);
  });

  it('reads back typed text with a comma', () => {
    const p = payload(false, 'Revenue', 'Headcount');
    const answers = { q: { questionUuid: 'q', selectedOptionIds: [ELSE], userInputs: { [ELSE]: 'Pune, India' } } };

    expect(parseAnswerMessage(buildAnswerMessage(p, answers), p)).toEqual(answers);
  });
});
describe('what a submit sends when earlier answers already went', () => {
  const SKIPPED = '__no_preference__';
  const q = (n: number) => ({
    uuid: `q${n}`,
    question: `Question number ${n}?`,
    multiSelect: false,
    options: [
      { id: `q${n}-a`, label: 'Alpha', isUserInput: false },
      { id: `q${n}-b`, label: 'Beta', isUserInput: false },
    ],
  });
  const payloadOf = (...ns: number[]): AskUserQuestionPayload => ({
    name: 'ask_user_question',
    questions: ns.map(q),
  });
  const pick = (n: number, id = 'a') => ({
    [`q${n}`]: { questionUuid: `q${n}`, selectedOptionIds: [`q${n}-${id}`], userInputs: {} },
  });
  const lines = (text: string) => text.split('\n').slice(1);

  it('sends every answer when nothing went before', () => {
    const answers = { ...pick(1), ...pick(2, 'b') };

    const text = buildResumeMessage(payloadOf(1, 2), answers);

    expect(text).toBe(buildAnswerMessage(payloadOf(1, 2), answers));
  });

  it('treats an empty record of sent answers like nothing sent', () => {
    const answers = pick(1);

    expect(buildResumeMessage(payloadOf(1), answers, {})).toBe(buildAnswerMessage(payloadOf(1), answers));
  });

  it('sends only the new questions', () => {
    const sent = { ...pick(1), ...pick(2), ...pick(3) };
    const answers = { ...sent, ...pick(4, 'b'), ...pick(5), ...pick(6) };

    const text = buildResumeMessage(payloadOf(1, 2, 3, 4, 5, 6), answers, sent);

    expect(lines(text)).toEqual([
      '1. "Question number 4?" → Beta',
      '2. "Question number 5?" → Alpha',
      '3. "Question number 6?" → Alpha',
    ]);
  });

  it('adds an earlier question whose answer was changed', () => {
    const sent = { ...pick(1), ...pick(2), ...pick(3) };
    const answers = { ...sent, ...pick(1, 'b'), ...pick(4) };

    const text = buildResumeMessage(payloadOf(1, 2, 3, 4), answers, sent);

    expect(lines(text)).toEqual([
      '1. "Question number 1?" → Beta',
      '2. "Question number 4?" → Alpha',
    ]);
  });

  it('leaves out an earlier answer that was changed and changed back', () => {
    const sent = { ...pick(1), ...pick(2) };
    const answers = { ...pick(1, 'b'), ...pick(2), ...pick(3) };
    const changedBack = { ...answers, ...pick(1) };

    expect(lines(buildResumeMessage(payloadOf(1, 2, 3), changedBack, sent))).toEqual([
      '1. "Question number 3?" → Alpha',
    ]);
  });

  it('sends a skipped new question as no preference', () => {
    const sent = { ...pick(1) };
    const answers = {
      ...sent,
      q2: { questionUuid: 'q2', selectedOptionIds: [SKIPPED], userInputs: {} },
    };

    expect(lines(buildResumeMessage(payloadOf(1, 2), answers, sent))).toEqual([
      '1. "Question number 2?" → [No preference]',
    ]);
  });

  it('notices a changed typed answer', () => {
    const ELSE = '__something_else__';
    const typed = (text: string) => ({
      q1: { questionUuid: 'q1', selectedOptionIds: [ELSE], userInputs: { [ELSE]: text } },
    });

    expect(lines(buildResumeMessage(payloadOf(1), typed('Pune'), typed('Delhi')))).toEqual([
      '1. "Question number 1?" → Pune',
    ]);
    expect(lines(buildResumeMessage(payloadOf(1, 2), { ...typed(' Delhi '), ...pick(2) }, typed('Delhi')))).toEqual([
      '1. "Question number 2?" → Alpha',
    ]);
  });

  it('falls back to every question when nothing differs', () => {
    const sent = { ...pick(1), ...pick(2) };

    expect(buildResumeMessage(payloadOf(1, 2), sent, sent)).toBe(buildAnswerMessage(payloadOf(1, 2), sent));
  });

  it('can be read back by parseAnswerMessage against the full card', () => {
    const sent = { ...pick(1), ...pick(2) };
    const answers = { ...sent, ...pick(3, 'b') };
    const full = payloadOf(1, 2, 3);

    expect(parseAnswerMessage(buildResumeMessage(full, answers, sent), full)).toEqual(pick(3, 'b'));
  });
});