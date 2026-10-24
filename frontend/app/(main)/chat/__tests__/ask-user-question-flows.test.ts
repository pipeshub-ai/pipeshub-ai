/**
 * What a reloaded conversation shows for every way a person can deal with an
 * ask_user_question card: answer it, skip it, ignore it and keep chatting,
 * regenerate it. Each test names the row the card and the reply must land on.
 */
import { describe, it, expect, vi } from 'vitest';
import type { ConversationMessage } from '../types';

// `../runtime` pulls in `@/lib/api`, whose auth store reads localStorage at
// import time; nothing here makes a request.
vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const {
  loadHistoricalMessages,
  getThreadMessagePlainText,
  prependOlderRows,
  reconcilePendingAfterPrepend,
  threadMatchesRows,
} = await import('../runtime');
const { buildResumeMessage } = await import('../components/message-area/ask-user-question-card');

const SKIPPED = '__no_preference__';
const SOMETHING_ELSE = '__something_else__';

function message(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-09-18T00:00:00.000Z',
    updatedAt: '2026-09-18T00:00:00.000Z',
    ...overrides,
  } as ConversationMessage;
}

type Option = { id: string; label: string; isUserInput?: boolean };

const ask = (uuid: string, question: string, options: Array<string | Option>, multiSelect = false) => ({
  uuid,
  question,
  multiSelect,
  options: options.map((o, i) =>
    typeof o === 'string'
      ? { id: `${uuid}-o${i}`, label: o, isUserInput: false }
      : { isUserInput: false, ...o },
  ),
});

const card = (...questions: ReturnType<typeof ask>[]) => ({ name: 'ask_user_question', questions });

const user = (id: string, content: string) => message({ _id: id, messageType: 'user_query', content });
const bot = (id: string, content = '') => message({ _id: id, messageType: 'bot_response', content });
const savedCard = (id: string, payload: ReturnType<typeof card>) =>
  message({
    _id: id,
    messageType: 'tool_call',
    tools: [{ toolName: 'ask_user_question', toolResult: payload }],
  } as Partial<ConversationMessage>);
const answerQuery = (id: string, ...lines: string[]) =>
  user(id, `User selections:\n${lines.map((l, i) => `${i + 1}. ${l}`).join('\n')}`);

const region = ask('q-region', 'Which region?', ['EU', 'US', 'APAC']);
const format = ask('q-format', 'Which format?', ['Table', 'Chart']);
const topics = ask('q-topics', 'What should I analyze?', ['Summarize', 'Totals', 'Trends'], true);

type Loaded = ReturnType<typeof loadHistoricalMessages>;
const row = (loaded: Loaded, id: string) => loaded.messages.find((m) => m.id === id);
const custom = (loaded: Loaded, id: string) =>
  (row(loaded, id)?.metadata?.custom ?? {}) as Record<string, unknown>;
const answersOn = (loaded: Loaded, id: string) =>
  custom(loaded, id).persistedAskUserQuestionAnswers as Record<string, { selectedOptionIds: string[]; userInputs: Record<string, string> }>;
const questionsOn = (loaded: Loaded, id: string) =>
  (custom(loaded, id).persistedAskUserQuestion as { questions: Array<{ question: string }> }).questions.map((q) => q.question);
const shape = (loaded: Loaded) => loaded.messages.map((m) => [m.role, m.id]);
const textOf = (loaded: Loaded, id: string) => {
  const found = row(loaded, id);
  return found ? getThreadMessagePlainText(found) : undefined;
};
const noResumeBubble = (loaded: Loaded) =>
  loaded.messages.every((m) => !getThreadMessagePlainText(m).startsWith('User selections:'));

describe('answering a card', () => {
  it('one question: the answer and the reply sit on the row that asked', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'), bot('b2', 'Planning for EU.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('Planning for EU.');
    expect(answersOn(loaded, 'b1')).toEqual({
      'q-region': { questionUuid: 'q-region', selectedOptionIds: ['q-region-o0'], userInputs: {} },
    });
    expect(loaded.unansweredAskUserQuestion).toBeNull();
    expect(noResumeBubble(loaded)).toBe(true);
  });

  it('two questions in one card: each answer goes to its own question', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'report please'), savedCard('t1', card(region, format)), bot('b1'),
      answerQuery('sel', '"Which region?" → US', '"Which format?" → Chart'), bot('b2', 'Here is the chart.'),
    ]);

    expect(questionsOn(loaded, 'b1')).toEqual(['Which region?', 'Which format?']);
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o1']);
    expect(answersOn(loaded, 'b1')['q-format'].selectedOptionIds).toEqual(['q-format-o1']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('three questions asked as separate tool calls end up on one card, all answered', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'report please'),
      savedCard('t1', card(region)), bot('b1'),
      answerQuery('s1', '"Which region?" → EU'),
      savedCard('t2', card(format)), bot('b2'),
      answerQuery('s2', '"Which format?" → Table'),
      savedCard('t3', card(topics)), bot('b3'),
      answerQuery('s3', '"What should I analyze?" → Totals'),
      bot('b4', 'Done.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(questionsOn(loaded, 'b1')).toEqual(['Which region?', 'Which format?', 'What should I analyze?']);
    expect(Object.keys(answersOn(loaded, 'b1'))).toEqual(['q-region', 'q-format', 'q-topics']);
    expect(textOf(loaded, 'b1')).toBe('Done.');
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('multi-select: every ticked option is kept', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'analyze'), savedCard('t1', card(topics)), bot('b1'),
      answerQuery('sel', '"What should I analyze?" → Summarize, Trends'), bot('b2', 'On it.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-topics'].selectedOptionIds).toEqual(['q-topics-o0', 'q-topics-o2']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('free text typed into "something else" is kept as typed', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → Latin America'), bot('b2', 'Planning for LATAM.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-region']).toEqual({
      questionUuid: 'q-region',
      selectedOptionIds: [SOMETHING_ELSE],
      userInputs: { [SOMETHING_ELSE]: 'Latin America' },
    });
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('an option that takes typed input keeps that text', () => {
    const other = ask('q-team', 'Which team?', [
      { id: 'eng', label: 'Engineering' },
      { id: 'other-team', label: 'Another team', isUserInput: true },
    ]);
    const loaded = loadHistoricalMessages([
      user('u1', 'who owns it'), savedCard('t1', card(other)), bot('b1'),
      answerQuery('sel', '"Which team?" → Data Platform'), bot('b2', 'Data Platform owns it.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-team'].selectedOptionIds).toEqual([SOMETHING_ELSE]);
    expect(answersOn(loaded, 'b1')['q-team'].userInputs[SOMETHING_ELSE]).toBe('Data Platform');
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('questions that offer the same option label do not share an answer', () => {
    const project = ask('q-project', 'Which project?', ['Apollo', 'All']);
    const status = ask('q-status', 'Which status?', ['Open', 'All']);
    const loaded = loadHistoricalMessages([
      user('u1', 'list tickets'), savedCard('t1', card(project, status)), bot('b1'),
      answerQuery('sel', '"Which project?" → Apollo', '"Which status?" → All'), bot('b2', 'Listing.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-project'].selectedOptionIds).toEqual(['q-project-o0']);
    expect(answersOn(loaded, 'b1')['q-status'].selectedOptionIds).toEqual(['q-status-o1']);
  });

  it('two cards in two different turns each keep their own answer and reply', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'first task'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('s1', '"Which region?" → EU'), bot('b2', 'First done.'),
      user('u2', 'second task'), savedCard('t2', card(format)), bot('b3'),
      answerQuery('s2', '"Which format?" → Chart'), bot('b4', 'Second done.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1'], ['user', 'u2'], ['assistant', 'b3']]);
    expect(textOf(loaded, 'b1')).toBe('First done.');
    expect(textOf(loaded, 'b3')).toBe('Second done.');
    expect(Object.keys(answersOn(loaded, 'b1'))).toEqual(['q-region']);
    expect(Object.keys(answersOn(loaded, 'b3'))).toEqual(['q-format']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('keeps ordinary chat after an answered card as separate turns', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'), bot('b2', 'Planning for EU.'),
      user('u2', 'thanks, and the budget?'), bot('b3', 'Budget is 10k.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1'], ['user', 'u2'], ['assistant', 'b3']]);
    expect(textOf(loaded, 'b3')).toBe('Budget is 10k.');
    expect(custom(loaded, 'b3').persistedAskUserQuestion).toBeUndefined();
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('a card asked in a later turn is a new card, the old one stays answered', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('s1', '"Which region?" → EU'), bot('b2', 'Planning for EU.'),
      user('u2', 'now the report'), savedCard('t2', card(format)), bot('b3'),
    ]);

    expect(loaded.unansweredAskUserQuestion?.assistantMessageId).toBe('b3');
    expect(Object.keys(answersOn(loaded, 'b1'))).toEqual(['q-region']);
  });

  it('other tools in the same turn do not split the questions onto another card', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'search then ask'),
      message({
        _id: 't-search',
        messageType: 'tool_call',
        tools: [{ toolName: 'search', toolResult: { hits: 3 } }],
      } as Partial<ConversationMessage>),
      message({
        _id: 't-slack',
        messageType: 'tool_call',
        tools: [{ toolName: 'slack', toolResult: { ok: true } }],
      } as Partial<ConversationMessage>),
      savedCard('t-ask', card(region, format)),
      bot('b1'),
      answerQuery('sel', '"Which region?" → EU', '"Which format?" → Table'),
      bot('b2', 'Here is the table.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(questionsOn(loaded, 'b1')).toEqual(['Which region?', 'Which format?']);
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('keeps tool activity from before and after the question on the same row', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)),
      message({
        _id: 'b1',
        messageType: 'bot_response',
        content: '',
        parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'search' }],
      } as Partial<ConversationMessage>),
      answerQuery('sel', '"Which region?" → EU'),
      message({
        _id: 'b2',
        messageType: 'bot_response',
        content: 'Planning for EU.',
        parts: [{ type: 'tool_call', toolCallId: 'c2', toolName: 'write' }],
      } as Partial<ConversationMessage>),
    ]);

    expect(custom(loaded, 'b1').persistedParts).toEqual([
      { type: 'tool_call', toolCallId: 'c1', toolName: 'search' },
      { type: 'tool_call', toolCallId: 'c2', toolName: 'write' },
    ]);
  });
});

describe('answers that contain a comma, after a reload', () => {
  const metric = ask('q-metric', 'Which metric?', ['Revenue, net of tax', 'Headcount']);
  const metrics = ask('q-metrics', 'Which metrics?', ['Revenue, net of tax', 'Headcount', 'Totals'], true);

  it('shows the option that was picked when its label has a comma', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'compare'), savedCard('t1', card(metric)), bot('b1'),
      answerQuery('sel', '"Which metric?" → Revenue, net of tax'), bot('b2', 'Comparing revenue.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-metric']).toEqual({
      questionUuid: 'q-metric', selectedOptionIds: ['q-metric-o0'], userInputs: {},
    });
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('shows typed text with a comma in full', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'compare'), savedCard('t1', card(metric)), bot('b1'),
      answerQuery('sel', '"Which metric?" → Pune, India'), bot('b2', 'Comparing Pune.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-metric']).toEqual({
      questionUuid: 'q-metric', selectedOptionIds: [SOMETHING_ELSE], userInputs: { [SOMETHING_ELSE]: 'Pune, India' },
    });
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('shows every ticked option when one label has a comma', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'compare'), savedCard('t1', card(metrics)), bot('b1'),
      answerQuery('sel', '"Which metrics?" → Revenue, net of tax, Totals'), bot('b2', 'Comparing.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-metrics'].selectedOptionIds).toEqual(['q-metrics-o0', 'q-metrics-o2']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('keeps a comma answer closed when the agent asks the same question again', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'compare'), savedCard('t1', card(metric)), bot('b1'),
      answerQuery('sel', '"Which metric?" → Revenue, net of tax'),
      savedCard('t2', card(metric)), bot('b2'),
    ]);

    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });
});
describe('an agent that asks many questions in a row', () => {
  const COUNT = 17;
  const yesNo = (i: number, yes = 'Yes') => ask(`q${i}`, `Random question number ${i}?`, [yes, 'No']);
  const label = (i: number, commaAt: number[]) => (commaAt.includes(i) ? 'Yes, definitely' : 'Yes');

  function conversation(options: {
    answered: number;
    commaAt?: number[];
    skipAt?: number[];
    finalReply?: string;
    askAgain?: number;
  }) {
    const commaAt = options.commaAt ?? [];
    const skipAt = options.skipAt ?? [];
    const rows = [user('u0', 'ask me random questions')];
    for (let i = 1; i <= COUNT; i += 1) {
      rows.push(savedCard(`t${i}`, card(yesNo(i, label(i, commaAt)))), bot(`b${i}`));
      if (i > options.answered) break;
      const answer = skipAt.includes(i) ? '[No preference]' : label(i, commaAt);
      rows.push(answerQuery(`s${i}`, `"Random question number ${i}?" → ${answer}`));
    }
    if (options.askAgain) {
      rows.push(savedCard('t-again', card(yesNo(options.askAgain))), bot('b-again'));
    }
    if (options.finalReply) rows.push(bot('b-final', options.finalReply));
    return loadHistoricalMessages(rows);
  }

  const questionNumbers = (loaded: Loaded, id = 'b1') =>
    questionsOn(loaded, id).map((q) => Number(q.replace(/\D/g, '')));
  const everyNumber = Array.from({ length: COUNT }, (_, i) => i + 1);

  it('keeps all of them on one card, in the order they were asked', () => {
    const loaded = conversation({ answered: COUNT - 1 });

    expect(shape(loaded)).toEqual([['user', 'u0'], ['assistant', 'b1']]);
    expect(questionNumbers(loaded)).toEqual(everyNumber);
  });

  it('leaves only the last one open when the others were answered', () => {
    const loaded = conversation({ answered: COUNT - 1 });

    expect(loaded.unansweredAskUserQuestion?.assistantMessageId).toBe('b1');
    expect(Object.keys(loaded.unansweredAskUserQuestion?.answers ?? {})).toEqual(
      everyNumber.slice(0, -1).map((i) => `q${i}`),
    );
    expect(loaded.unansweredAskUserQuestion?.payload.questions).toHaveLength(COUNT);
  });

  it('closes the whole card once the last one is answered and the agent replies', () => {
    const loaded = conversation({ answered: COUNT, finalReply: 'That was fun.' });

    expect(shape(loaded)).toEqual([['user', 'u0'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('That was fun.');
    expect(Object.keys(answersOn(loaded, 'b1'))).toHaveLength(COUNT);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('keeps the answer of every question when some answers contain a comma', () => {
    const commaAt = [2, 5, 9, 16];
    const loaded = conversation({ answered: COUNT, commaAt, finalReply: 'Done.' });

    for (const i of commaAt) {
      expect(answersOn(loaded, 'b1')[`q${i}`].selectedOptionIds).toEqual([`q${i}-o0`]);
    }
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('does not reopen a question in the middle when a later one is still waiting', () => {
    const commaAt = [5, 9];
    const loaded = conversation({ answered: COUNT - 1, commaAt });

    const answers = loaded.unansweredAskUserQuestion?.answers ?? {};
    expect(Object.values(answers).every((a) => a.selectedOptionIds.length === 1)).toBe(true);
    expect(Object.keys(answers)).toHaveLength(COUNT - 1);
  });

  it('keeps skipped questions answered among the others', () => {
    const skipAt = [1, 8, 17];
    const loaded = conversation({ answered: COUNT, skipAt, finalReply: 'Done.' });

    for (const i of skipAt) {
      expect(answersOn(loaded, 'b1')[`q${i}`].selectedOptionIds).toEqual([SKIPPED]);
    }
    expect(answersOn(loaded, 'b1').q2.selectedOptionIds).toEqual(['q2-o0']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('shows no User selections bubble for any of the answers', () => {
    expect(noResumeBubble(conversation({ answered: COUNT, finalReply: 'Done.' }))).toBe(true);
    expect(noResumeBubble(conversation({ answered: COUNT - 1 }))).toBe(true);
  });

  it('keeps the card closed when the agent re-asks one of the answered questions at the end', () => {
    const loaded = conversation({ answered: COUNT, askAgain: 3, finalReply: 'Done.' });

    expect(loaded.unansweredAskUserQuestion).toBeNull();
    expect(questionNumbers(loaded)).toEqual(everyNumber);
  });

  it('counts a repeated question once even though the agent asked it twice', () => {
    const rows = [
      user('u0', 'ask me random questions'),
      savedCard('t1', card(yesNo(1))), bot('b1'),
      answerQuery('s1', '"Random question number 1?" → Yes'),
      savedCard('t2', card(ask('q2-again', '  random QUESTION number 1 ', ['Yes', 'No']))), bot('b2'),
      answerQuery('s2', '"Random question number 1?" → Yes'),
      bot('b3', 'Done.'),
    ];
    const loaded = loadHistoricalMessages(rows);

    expect(questionsOn(loaded, 'b1')).toEqual(['Random question number 1?']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('keeps ordinary chat after the long question run as its own turn', () => {
    const rows = [
      user('u0', 'ask me random questions'),
      ...everyNumber.flatMap((i) => [
        savedCard(`t${i}`, card(yesNo(i))), bot(`b${i}`),
        answerQuery(`s${i}`, `"Random question number ${i}?" → Yes`),
      ]),
      bot('b-final', 'That was fun.'),
      user('u1', 'thanks'), bot('b-thanks', 'Anytime.'),
    ];
    const loaded = loadHistoricalMessages(rows);

    expect(shape(loaded)).toEqual([['user', 'u0'], ['assistant', 'b1'], ['user', 'u1'], ['assistant', 'b-thanks']]);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });
});

describe('skipping a card', () => {
  it('skipping the only question closes the card as answered', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → [No preference]'), bot('b2', 'Going with a global plan.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual([SKIPPED]);
    expect(textOf(loaded, 'b1')).toBe('Going with a global plan.');
    expect(loaded.unansweredAskUserQuestion).toBeNull();
    expect(noResumeBubble(loaded)).toBe(true);
  });

  it('skipping one of two questions keeps the other answer', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'report please'), savedCard('t1', card(region, format)), bot('b1'),
      answerQuery('sel', '"Which region?" → [No preference]', '"Which format?" → Table'), bot('b2', 'Here you go.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual([SKIPPED]);
    expect(answersOn(loaded, 'b1')['q-format'].selectedOptionIds).toEqual(['q-format-o0']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('skipping every question closes the card', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'report please'), savedCard('t1', card(region, format)), bot('b1'),
      answerQuery('sel', '"Which region?" → [No preference]', '"Which format?" → [No preference]'), bot('b2', 'Defaults it is.'),
    ]);

    expect(Object.values(answersOn(loaded, 'b1')).map((a) => a.selectedOptionIds)).toEqual([[SKIPPED], [SKIPPED]]);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('skipping a multi-select question closes it too', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'analyze'), savedCard('t1', card(topics)), bot('b1'),
      answerQuery('sel', '"What should I analyze?" → [No preference]'), bot('b2', 'Everything then.'),
    ]);

    expect(answersOn(loaded, 'b1')['q-topics'].selectedOptionIds).toEqual([SKIPPED]);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('a skipped question that the agent asks again stays closed', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → [No preference]'),
      savedCard('t2', card(region)), bot('b2'),
    ]);

    expect(loaded.unansweredAskUserQuestion).toBeNull();
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual([SKIPPED]);
  });
});

describe('leaving a card unanswered and chatting on', () => {
  it('keeps the card pending on its own row while the chat carries on', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      user('u2', 'hello there'), bot('b2', 'Hi!'),
      user('u3', 'what is 2+2'), bot('b3', '4.'),
    ]);

    expect(shape(loaded)).toEqual([
      ['user', 'u1'], ['assistant', 'b1'], ['user', 'u2'], ['assistant', 'b2'], ['user', 'u3'], ['assistant', 'b3'],
    ]);
    expect(loaded.unansweredAskUserQuestion).toMatchObject({ assistantMessageId: 'b1', status: 'pending' });
    expect(textOf(loaded, 'b2')).toBe('Hi!');
    expect(textOf(loaded, 'b3')).toBe('4.');
    expect(noResumeBubble(loaded)).toBe(true);
  });

  it('puts a late answer under the card, and keeps the later chat on its own rows', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      user('u2', 'hello there'), bot('b2', 'Hi!'),
      user('u3', 'what is 2+2'), bot('b3', '4.'),
      answerQuery('sel', '"Which region?" → EU'), bot('b4', 'Planning for EU.'),
    ]);

    expect(shape(loaded)).toEqual([
      ['user', 'u1'], ['assistant', 'b1'],
      ['user', 'u2'], ['assistant', 'b2'],
      ['user', 'u3'], ['assistant', 'b3'],
    ]);
    expect(textOf(loaded, 'b1')).toBe('Planning for EU.');
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
    expect(textOf(loaded, 'b2')).toBe('Hi!');
    expect(textOf(loaded, 'b3')).toBe('4.');
    expect(custom(loaded, 'b1').status).toBeUndefined();
    expect(loaded.unansweredAskUserQuestion).toBeNull();
    expect(noResumeBubble(loaded)).toBe(true);
  });

  it('after Stop on the first card, a later chat, then answering, puts the reply under the card', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'helpme'), savedCard('t1', card(region)),
      message({ _id: 'b1', messageType: 'bot_response', content: '', status: 'stopped' } as Partial<ConversationMessage>),
      user('u2', 'hiii'), bot('b2', 'Hi! How can I help you today?'),
      answerQuery('sel', '"Which region?" → EU'), bot('b3', 'Planning the EU launch.'),
    ]);

    expect(shape(loaded)).toEqual([
      ['user', 'u1'], ['assistant', 'b1'],
      ['user', 'u2'], ['assistant', 'b2'],
    ]);
    expect(textOf(loaded, 'b1')).toBe('Planning the EU launch.');
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
    expect(textOf(loaded, 'b2')).toBe('Hi! How can I help you today?');
    expect(custom(loaded, 'b1').status).toBeUndefined();
    expect(custom(loaded, 'b2').status).toBeUndefined();
    expect(loaded.unansweredAskUserQuestion).toBeNull();
    expect(noResumeBubble(loaded)).toBe(true);
  });

  it('does not copy a later chat onto the card when User selections was saved before that chat', () => {
    const later = 'You asked me to: send an email saying Hello from Pipeshub.';
    const loaded = loadHistoricalMessages([
      user('u1', 'ask me one question'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'),
      user('u2', 'what are the things i asked u to do'), bot('b2', later),
    ]);

    expect(shape(loaded)).toEqual([
      ['user', 'u1'], ['assistant', 'b1'],
      ['user', 'u2'], ['assistant', 'b2'],
    ]);
    expect(textOf(loaded, 'b1')).not.toBe(later);
    expect(textOf(loaded, 'b1')).not.toContain('You asked me to');
    expect(textOf(loaded, 'b2')).toBe(later);
    expect(loaded.unansweredAskUserQuestion?.answers['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
  });

  it('a follow-up after User selections does not pull a later chat onto the card', () => {
    const later = 'You asked me to: send an email saying Hello from Pipeshub.';
    const reply = 'Nice, work or study it is.';
    const loaded = loadHistoricalMessages([
      user('u1', 'ask me one question'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'), bot('b3', reply),
      user('u2', 'what are the things i asked u to do'), bot('b2', later),
    ]);

    expect(textOf(loaded, 'b1')).toBe(reply);
    expect(textOf(loaded, 'b1')).not.toContain('You asked me to');
    expect(textOf(loaded, 'b2')).toBe(later);
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('answering after a later chat keeps that chat off the card', () => {
    const later = 'You asked me to: send an email saying Hello from Pipeshub.';
    const reply = 'Planning for EU.';
    const loaded = loadHistoricalMessages([
      user('u1', 'ask me one question'), savedCard('t1', card(region)), bot('b1'),
      user('u2', 'what are the things i asked u to do'), bot('b2', later),
      answerQuery('sel', '"Which region?" → EU'), bot('b3', reply),
    ]);

    expect(textOf(loaded, 'b1')).toBe(reply);
    expect(textOf(loaded, 'b1')).not.toContain('You asked me to');
    expect(textOf(loaded, 'b2')).toBe(later);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('two later chats stay off the card when it is answered last', () => {
    const later1 = 'I can help you find information.';
    const later2 = 'You asked me to: send an email saying Hello from Pipeshub.';
    const reply = 'Nice, work or study it is.';
    const loaded = loadHistoricalMessages([
      user('u1', 'ask me one question'), savedCard('t1', card(region)), bot('b1'),
      user('u2', 'help me find information'), bot('b2', later1),
      user('u3', 'what are the things i asked u to do'), bot('b3', later2),
      answerQuery('sel', '"Which region?" → EU'), bot('b4', reply),
    ]);

    expect(textOf(loaded, 'b1')).toBe(reply);
    expect(textOf(loaded, 'b1')).not.toContain('You asked me to');
    expect(textOf(loaded, 'b1')).not.toContain('find information');
    expect(textOf(loaded, 'b2')).toBe(later1);
    expect(textOf(loaded, 'b3')).toBe(later2);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('User selections before two later chats does not copy either onto the card', () => {
    const later1 = 'I can help you find information.';
    const later2 = 'You asked me to: send an email saying Hello from Pipeshub.';
    const loaded = loadHistoricalMessages([
      user('u1', 'ask me one question'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'),
      user('u2', 'help me find information'), bot('b2', later1),
      user('u3', 'what are the things i asked u to do'), bot('b3', later2),
    ]);

    expect(shape(loaded)).toEqual([
      ['user', 'u1'], ['assistant', 'b1'],
      ['user', 'u2'], ['assistant', 'b2'],
      ['user', 'u3'], ['assistant', 'b3'],
    ]);
    expect(textOf(loaded, 'b1')).not.toContain('You asked me to');
    expect(textOf(loaded, 'b1')).not.toContain('find information');
    expect(textOf(loaded, 'b2')).toBe(later1);
    expect(textOf(loaded, 'b3')).toBe(later2);
  });

  it('a late answer never lands on the newest turn', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      user('u2', 'hello there'), bot('b2', 'Hi!'),
      answerQuery('sel', '"Which region?" → US'), bot('b3', 'Planning for US.'),
    ]);

    expect(custom(loaded, 'b2').persistedAskUserQuestionAnswers).toBeUndefined();
    expect(custom(loaded, 'b2').persistedAskUserQuestion).toBeUndefined();
    expect(textOf(loaded, 'b2')).toBe('Hi!');
  });

  it('the newest unanswered card wins, the older one stays visible without answers', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      user('u2', 'now the report'), savedCard('t2', card(format)), bot('b2'),
    ]);

    expect(loaded.unansweredAskUserQuestion?.assistantMessageId).toBe('b2');
    expect(custom(loaded, 'b1').persistedAskUserQuestion).toBeDefined();
    expect(answersOn(loaded, 'b1')).toBeUndefined();
  });

  it('a greeting after Stop and a later hi sits under that hi, not on Stopped', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'ask me random questions'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('s1', '"Which region?" → EU'),
      message({ _id: 'stop1', messageType: 'bot_response', content: '', status: 'stopped' } as Partial<ConversationMessage>),
      user('u2', 'hi'),
      message({ _id: 'stop2', messageType: 'bot_response', content: '', status: 'stopped' } as Partial<ConversationMessage>),
      bot('hello', 'Hi, Pranjal!'),
    ]);

    expect(shape(loaded)).toEqual([
      ['user', 'u1'], ['assistant', 'b1'],
      ['user', 'u2'], ['assistant', 'hello'],
    ]);
    expect(textOf(loaded, 'hello')).toBe('Hi, Pranjal!');
    expect(custom(loaded, 'hello').status).toBeUndefined();
    expect(custom(loaded, 'b1').status).toBeUndefined();
    expect(loaded.unansweredAskUserQuestion).toMatchObject({
      assistantMessageId: 'b1',
      status: 'pending',
      answers: { 'q-region': { selectedOptionIds: ['q-region-o0'] } },
    });
  });

  it('clears Stopped when a later greeting lands on the same card row', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'ask me random questions'), savedCard('t1', card(region)),
      message({ _id: 'b1', messageType: 'bot_response', content: 'Working', status: 'stopped' } as Partial<ConversationMessage>),
      answerQuery('s1', '"Which region?" → EU'),
      bot('hello', 'Hi, Pranjal!'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('Hi, Pranjal!');
    expect(custom(loaded, 'b1').status).toBeUndefined();
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
  });

  it('a reply that streamed text before the question keeps that text visible once answered', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1', 'Let me ask first.'),
      answerQuery('sel', '"Which region?" → EU'), bot('b2', 'Planning for EU.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('Planning for EU.');
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });
});

describe('regenerating a turn that has a card', () => {
  it('shows the regenerated card as pending when it has not been answered', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'),
      message({
        _id: 'b1', messageType: 'bot_response', content: '',
        tools: [{ toolName: 'ask_user_question', toolResult: card(region) }],
      } as Partial<ConversationMessage>),
    ]);

    expect(loaded.unansweredAskUserQuestion).toMatchObject({ assistantMessageId: 'b1', status: 'pending' });
  });

  it('closes a regenerated card once its answer is saved', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'),
      message({
        _id: 'b1', messageType: 'bot_response', content: '',
        tools: [{ toolName: 'ask_user_question', toolResult: card(region) }],
      } as Partial<ConversationMessage>),
      answerQuery('sel', '"Which region?" → EU'), bot('b2', 'Planning for EU.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('Planning for EU.');
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('closes a regenerated card whose question row was saved after the answer row', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), bot('b1'),
      savedCard('t1', card(region)),
      answerQuery('sel', '"Which region?" → US'), bot('b2', 'Planning for US.'),
    ]);

    expect(textOf(loaded, 'b1')).toBe('Planning for US.');
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('a regenerate that no longer asks anything shows just the new answer', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), bot('b1', 'A plain answer.'),
    ]);

    expect(textOf(loaded, 'b1')).toBe('A plain answer.');
    expect(custom(loaded, 'b1').persistedAskUserQuestion).toBeUndefined();
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });
});

describe('a resume that does not go through', () => {
  it('keeps a failed resume invisible and the card retryable', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'),
      message({ _id: 'err', messageType: 'error', content: 'Error Generating Response, Please try again' }),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(loaded.unansweredAskUserQuestion).toMatchObject({ assistantMessageId: 'b1', status: 'pending' });
  });

  it('a retry after a failed resume closes the card on the first row', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'),
      message({ _id: 'err', messageType: 'error', content: 'Error Generating Response, Please try again' }),
      answerQuery('sel2', '"Which region?" → EU'), bot('b2', 'Planning for EU.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('Planning for EU.');
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('an answer for a question the card never had is ignored, the card stays pending', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"A question nobody asked?" → Yes'),
    ]);

    expect(loaded.unansweredAskUserQuestion?.assistantMessageId).toBe('b1');
  });

  it('a stopped resume keeps the saved answers on the card', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'),
      message({ _id: 'b2', messageType: 'bot_response', content: 'Working on EU', status: 'stopped' } as Partial<ConversationMessage>),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('Working on EU');
    expect(answersOn(loaded, 'b1')['q-region'].selectedOptionIds).toEqual(['q-region-o0']);
    expect(noResumeBubble(loaded)).toBe(true);
  });

  it('a retry after a stopped resume closes the card on the first row', () => {
    const loaded = loadHistoricalMessages([
      user('u1', 'plan the launch'), savedCard('t1', card(region)), bot('b1'),
      answerQuery('sel', '"Which region?" → EU'),
      message({ _id: 'b2', messageType: 'bot_response', content: 'Working on EU', status: 'stopped' } as Partial<ConversationMessage>),
      answerQuery('sel2', '"Which region?" → EU'), bot('b3', 'Planning for EU.'),
    ]);

    expect(shape(loaded)).toEqual([['user', 'u1'], ['assistant', 'b1']]);
    expect(textOf(loaded, 'b1')).toBe('Planning for EU.');
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });
});

describe('joining an older page onto a loaded thread', () => {
  const rows = [
    user('u0', 'ask me random questions'),
    savedCard('t1', card(region)), bot('b1'),
    answerQuery('s1', '"Which region?" → EU'),
    savedCard('t2', card(format)), bot('b2'),
  ];

  describe('prependOlderRows', () => {
    it('puts the older rows first and keeps the loaded ones in order', () => {
      expect(prependOlderRows(rows.slice(3), rows.slice(0, 3)).map((r) => r._id)).toEqual(rows.map((r) => r._id));
    });

    it('skips an older row the thread already has', () => {
      expect(prependOlderRows(rows.slice(2), rows.slice(0, 4)).map((r) => r._id)).toEqual(rows.slice(0, 4).concat(rows.slice(4)).map((r) => r._id));
    });

    it('returns the loaded rows untouched when the page adds nothing', () => {
      const loaded = rows.slice(2);
      expect(prependOlderRows(loaded, rows.slice(2, 4))).toBe(loaded);
    });
  });

  describe('threadMatchesRows', () => {
    it('is true for a thread built from exactly those rows', () => {
      expect(threadMatchesRows(loadHistoricalMessages(rows).messages, rows)).toBe(true);
    });

    it('is false once the thread has a row the rows do not build', () => {
      const messages = [...loadHistoricalMessages(rows).messages, { id: 'live', role: 'assistant' as const, content: [] }];
      expect(threadMatchesRows(messages, rows)).toBe(false);
    });

    it('is false once the thread lost a row', () => {
      expect(threadMatchesRows(loadHistoricalMessages(rows).messages.slice(1), rows)).toBe(false);
    });

    it('is false once Stop put a partial reply on a row the rows built', () => {
      const messages = loadHistoricalMessages(rows).messages.map((m) =>
        m.role === 'assistant'
          ? {
              ...m,
              content: [{ type: 'text' as const, text: 'Working on EU' }],
              metadata: { ...m.metadata, custom: { ...m.metadata?.custom, status: 'stopped' as const } },
            }
          : m,
      );
      expect(threadMatchesRows(messages, rows)).toBe(false);
    });
  });

  describe('reconcilePendingAfterPrepend', () => {
    const rebuilt = loadHistoricalMessages(rows);
    const open = rebuilt.unansweredAskUserQuestion!;
    const typed = { 'q-format': { questionUuid: 'q-format', selectedOptionIds: ['q-format-o1'], userInputs: {} } };

    it('takes what the joined rows show when nothing was open before', () => {
      expect(reconcilePendingAfterPrepend(null, open, rebuilt.messages)).toBe(open);
    });

    it('keeps answers picked but not yet sent', () => {
      const current = { ...open, assistantMessageId: 'old-row', answers: typed };
      const result = reconcilePendingAfterPrepend(current, open, rebuilt.messages);

      expect(result?.assistantMessageId).toBe(open.assistantMessageId);
      expect(result?.status).toBe('pending');
      expect(Object.keys(result?.answers ?? {})).toEqual(['q-region', 'q-format']);
    });

    it('lets a picked answer win over the saved one for the same question', () => {
      const picked = { 'q-region': { questionUuid: 'q-region', selectedOptionIds: ['q-region-o2'], userInputs: {} } };
      const result = reconcilePendingAfterPrepend({ ...open, answers: picked }, open, rebuilt.messages);

      expect(result?.answers['q-region'].selectedOptionIds).toEqual(['q-region-o2']);
    });

    it('takes the joined rows over a card that was already answered', () => {
      const current = { ...open, status: 'submitted' as const, answers: typed };
      expect(reconcilePendingAfterPrepend(current, open, rebuilt.messages)).toBe(open);
    });

    it('points an answered card at the row it was merged into', () => {
      const current = { ...open, assistantMessageId: 'merged-away', status: 'submitted' as const };
      const result = reconcilePendingAfterPrepend(current, null, rebuilt.messages);

      expect(result?.assistantMessageId).toBe('b1');
      expect(result?.status).toBe('submitted');
    });

    it('leaves a card alone when its row is still there', () => {
      const current = { ...open, assistantMessageId: 'b1', status: 'submitted' as const };
      expect(reconcilePendingAfterPrepend(current, null, rebuilt.messages)).toBe(current);
    });

    it('leaves a card alone when no row carries it', () => {
      const stray = { ...open, assistantMessageId: 'nowhere', payload: card(ask('q-other', 'Something else?', ['A'])) };
      expect(reconcilePendingAfterPrepend(stray, null, rebuilt.messages)).toBe(stray);
    });
  });
});
describe('a long run of rounds where each submit sends only its own answers', () => {
  const ROUNDS = 16;
  const PER_ROUND = 3;
  const ids = (round: number) => Array.from({ length: PER_ROUND }, (_, k) => (round - 1) * PER_ROUND + k + 1);
  const questionOf = (n: number) => ask(`q${n}`, `Random question number ${n}?`, ['Alpha', 'Beta']);

  type Answers = Record<string, { questionUuid: string; selectedOptionIds: string[]; userInputs: Record<string, string> }>;
  const choose = (n: number, optionIndex = 0): Answers => ({
    [`q${n}`]: { questionUuid: `q${n}`, selectedOptionIds: [`q${n}-o${optionIndex}`], userInputs: {} },
  });

  /** Plays the card the way the UI does: each round's submit goes through
   *  buildResumeMessage with whatever the reloaded history says was sent. */
  function play(options: { rounds: number; change?: { atRound: number; question: number; optionIndex: number } }) {
    const rows: ConversationMessage[] = [user('u0', 'ask me random questions')];
    const sentTexts: string[] = [];
    let answers: Answers = {};
    let loaded = loadHistoricalMessages(rows);
    for (let round = 1; round <= options.rounds; round += 1) {
      rows.push(savedCard(`t${round}`, card(...ids(round).map(questionOf))), bot(`b${round}`));
      loaded = loadHistoricalMessages(rows);
      const pending = loaded.unansweredAskUserQuestion;
      answers = { ...(pending?.answers ?? {}) };
      for (const n of ids(round)) Object.assign(answers, choose(n));
      if (options.change?.atRound === round) {
        Object.assign(answers, choose(options.change.question, options.change.optionIndex));
      }
      const text = buildResumeMessage(
        pending?.payload ?? card(...ids(round).map(questionOf)),
        answers,
        pending?.sentAnswers,
      );
      sentTexts.push(text);
      rows.push(user(`s${round}`, text));
    }
    rows.push(bot('b-final', 'Thanks for playing.'));
    return { loaded: loadHistoricalMessages(rows), rows, sentTexts, lastPending: loaded.unansweredAskUserQuestion };
  }

  const countLines = (text: string) => text.split('\n').filter((l) => /^\d+\./.test(l)).length;

  it('sends three answers every round, never the growing pile', () => {
    const { sentTexts } = play({ rounds: ROUNDS });

    expect(sentTexts).toHaveLength(ROUNDS);
    expect(sentTexts.map(countLines)).toEqual(Array(ROUNDS).fill(PER_ROUND));
  });

  it('numbers each round from 1 and only names that round\'s questions', () => {
    const { sentTexts } = play({ rounds: ROUNDS });

    expect(sentTexts[0]).toContain('1. "Random question number 1?"');
    expect(sentTexts[ROUNDS - 1]).toContain('1. "Random question number 46?"');
    expect(sentTexts[ROUNDS - 1]).not.toContain('number 45?');
  });

  it('shows all 48 answers on one closed card after a reload', () => {
    const { loaded } = play({ rounds: ROUNDS });

    expect(shape(loaded)).toEqual([['user', 'u0'], ['assistant', 'b1']]);
    expect(questionsOn(loaded, 'b1')).toHaveLength(ROUNDS * PER_ROUND);
    expect(Object.keys(answersOn(loaded, 'b1'))).toHaveLength(ROUNDS * PER_ROUND);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
    expect(noResumeBubble(loaded)).toBe(true);
  });

  it('keeps each answer on its own question', () => {
    const { loaded } = play({ rounds: ROUNDS });

    for (let n = 1; n <= ROUNDS * PER_ROUND; n += 1) {
      expect(answersOn(loaded, 'b1')[`q${n}`].selectedOptionIds).toEqual([`q${n}-o0`]);
    }
  });

  it('marks everything answered so far as already sent when a card reopens', () => {
    const { rows } = play({ rounds: 5 });
    const reopened = loadHistoricalMessages([
      ...rows.slice(0, -1),
      savedCard('t6', card(...ids(6).map(questionOf))),
      bot('b6'),
    ]);

    expect(Object.keys(reopened.unansweredAskUserQuestion?.sentAnswers ?? {})).toHaveLength(5 * PER_ROUND);
    expect(Object.keys(reopened.unansweredAskUserQuestion?.answers ?? {})).toHaveLength(5 * PER_ROUND);
  });

  it('carries a changed old answer in the round that changed it, and reloads to it', () => {
    const { sentTexts, loaded } = play({ rounds: 8, change: { atRound: 6, question: 1, optionIndex: 1 } });

    expect(countLines(sentTexts[5])).toBe(PER_ROUND + 1);
    expect(sentTexts[5]).toContain('"Random question number 1?" → Beta');
    expect(sentTexts[4]).not.toContain('number 1?');
    expect(answersOn(loaded, 'b1').q1.selectedOptionIds).toEqual(['q1-o1']);
    expect(answersOn(loaded, 'b1').q2.selectedOptionIds).toEqual(['q2-o0']);
    expect(Object.keys(answersOn(loaded, 'b1'))).toHaveLength(8 * PER_ROUND);
  });

  it('still reloads old chats whose every submit repeated all earlier answers', () => {
    const rows: ConversationMessage[] = [user('u0', 'ask me random questions')];
    for (let round = 1; round <= 6; round += 1) {
      rows.push(savedCard(`t${round}`, card(...ids(round).map(questionOf))), bot(`b${round}`));
      const every = Array.from({ length: round * PER_ROUND }, (_, i) => i + 1);
      rows.push(answerQuery(`s${round}`, ...every.map((n) => `"Random question number ${n}?" → Alpha`)));
    }
    rows.push(bot('b-final', 'Done.'));

    const loaded = loadHistoricalMessages(rows);

    expect(shape(loaded)).toEqual([['user', 'u0'], ['assistant', 'b1']]);
    expect(Object.keys(answersOn(loaded, 'b1'))).toHaveLength(6 * PER_ROUND);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });

  it('shows all 48 answers when the history is joined one page at a time', () => {
    const { rows } = play({ rounds: ROUNDS });
    const pages: ConversationMessage[][] = [];
    for (let end = rows.length; end > 0; end -= 20) pages.unshift(rows.slice(Math.max(0, end - 20), end));

    let loadedRows = pages[pages.length - 1];
    for (let p = pages.length - 2; p >= 0; p -= 1) loadedRows = prependOlderRows(loadedRows, pages[p]);
    const loaded = loadHistoricalMessages(loadedRows);

    expect(Object.keys(answersOn(loaded, 'b1'))).toHaveLength(ROUNDS * PER_ROUND);
    expect(loaded.unansweredAskUserQuestion).toBeNull();
  });
});