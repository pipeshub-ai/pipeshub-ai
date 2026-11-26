/**
 * ANSWER-CONTRACT-FU-2 (ADR-RUN-03): what the person sees reads cleanly, in their own language.
 * - The not-found line is worded here from the answer's typed `notFound`, above the answer; the English line the
 *   answer text starts with (for surfaces with no UI) is left out, and the closest records are named only when the
 *   run could not ask.
 * - The grounding question card reads in the reader's language; its answer still sends the English it was asked in,
 *   which the worker reads it by.
 * - The row an answered question's reply lands on follows that reply's run, so its summary is the answer's, not
 *   "Waiting for your answer".
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';
import type { AskUserQuestionPayload, ConversationMessage, MessagePart } from '../types';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const { loadHistoricalMessages } = await import('../runtime');
const { messageRunId } = await import('../activity');
const { notFoundOf, withoutNotFoundLine } = await import('../subject-not-found');
const { NotFoundNotice } = await import('../components/message-area/not-found-notice');
const { AskUserQuestionCard, answerSummary } = await import('../components/message-area/ask-user-question-card');
const { cardWords } = await import('../components/message-area/grounding-card');
const { withSavedFinalFacts } = await import('../streaming');

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];
const LINE = "**I couldn't find anything about “SAAS launch” in your knowledge.** What follows draws on 2 related documents that do not mention it, so check it before you rely on it.";

function finalPart(notFound: Record<string, unknown>): MessagePart[] {
  return [{ type: 'text', content: '', isFinal: true, notFound }];
}

const USE_FOUND = 'Use what you can find';
const NAME_IT = 'Name a document or folder';
const QUESTION = "I couldn't find anything about “SAAS launch” in your knowledge. Did you mean one of these?";
const groundingPayload = (): AskUserQuestionPayload => ({
  name: 'ask_user_question',
  userIntent: 'Before starting the work, I looked for “SAAS launch” in what you can reach.',
  questions: [{
    uuid: 'q-ground',
    question: QUESTION,
    header: 'Not found',
    multiSelect: false,
    options: [
      { id: 'faq', label: 'product-faq', isUserInput: false },
      { id: 'use', label: USE_FOUND, isUserInput: false },
    ],
  }],
  grounding: { subject: 'SAAS launch', near: ['product-faq'], useFound: USE_FOUND, nameIt: NAME_IT, places: [] },
});

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(() => cleanup());

describe('FU-2: the not-found line, worded by the chat', () => {
  it('reads the fact off the final part and leaves the English line out of the answer', () => {
    const fact = notFoundOf(finalPart({ subject: 'SAAS launch', hits: 2, line: LINE }))!;
    expect(fact).toEqual({ subject: 'SAAS launch', hits: 2, line: LINE, near: [], proceeded: false, places: [] });
    expect(withoutNotFoundLine(`${LINE}\n\nThe report.`, fact)).toBe('The report.');
    // An answer that does not start with it (edited, or an older save) stays as it is.
    expect(withoutNotFoundLine('The report.', fact)).toBe('The report.');
    expect(notFoundOf(finalPart({ subject: 'SAAS launch', line: LINE }))).toBeNull();
    expect(notFoundOf(undefined)).toBeNull();
    // The saved message's own fact first: an answered card's row holds live parts, which never carry it.
    expect(notFoundOf([{ type: 'text', content: 'x', isFinal: true }], { subject: 'SAAS launch', hits: 0, line: LINE })?.hits).toBe(0);
  });

  it('says what the answer draws on, with the count in words, and names the closest records only when given', () => {
    const { rerender } = render(<Theme><NotFoundNotice fact={notFoundOf(finalPart({ subject: 'SAAS launch', hits: 2, line: LINE }))!} /></Theme>);
    const note = () => screen.getByTestId('not-found-notice').textContent ?? '';
    expect(note()).toContain("I couldn't find anything about “SAAS launch” in your knowledge.");
    expect(note()).toContain('2 related documents that do not mention it');
    expect(screen.queryByTestId('not-found-near')).toBeNull();
    rerender(<Theme><NotFoundNotice fact={notFoundOf(finalPart({ subject: 'SAAS launch', hits: 1, line: LINE, near: ['Launch checklist'] }))!} /></Theme>);
    expect(note()).toContain('1 related document that does not mention it');
    expect(screen.getByTestId('not-found-near').textContent).toBe('Closest matches in your knowledge: Launch checklist');
    rerender(<Theme><NotFoundNotice fact={notFoundOf(finalPart({ subject: 'SAAS launch', hits: 0, line: LINE }))!} /></Theme>);
    expect(note()).toContain('This answer is not based on any of your documents about it.');
  });

  it.each(LOCALES)('%s: every word of the line and the card is translated, with its plural forms', (locale) => {
    const keys = ['chat.notFound.title', 'chat.notFound.nothing', 'chat.notFound.near', 'askUserQuestion.grounding.header',
      'askUserQuestion.grounding.intent', 'askUserQuestion.grounding.questionNear', 'askUserQuestion.grounding.questionNone',
      'askUserQuestion.grounding.useFound', 'askUserQuestion.grounding.nameIt'];
    const vars = { lng: locale, subject: 'SAAS launch', names: 'A, B' };
    for (const key of keys) {
      expect(i18n.exists(key, { lng: locale, fallbackLng: false }), `${locale} ${key}`).toBe(true);
      if (!locale.startsWith('en')) expect(i18n.t(key, vars), `${locale} ${key}`).not.toBe(i18n.t(key, { ...vars, lng: 'en-US' }));
    }
    expect(i18n.t('chat.notFound.title', vars)).toContain('SAAS launch');
    for (const count of [1, 2, 5, 21]) {
      const words = i18n.t('chat.notFound.related', { lng: locale, count });
      expect(words, `${locale} ${count}`).toContain(String(count));
      expect(words).not.toContain('chat.notFound');
    }
  });
});

describe('ANSWER-CONTRACT-SOURCES: every place looked in is named', () => {
  it('the note and the card name the connected sources with the knowledge, never "in your knowledge" alone', () => {
    render(<Theme><NotFoundNotice fact={notFoundOf(finalPart({ subject: 'SAAS launch', hits: 0, line: LINE, places: ['Atlassian Rovo'] }))!} /></Theme>);
    const note = screen.getByTestId('not-found-notice').textContent ?? '';
    expect(note).toContain('I looked in your PipesHub knowledge and Atlassian Rovo and couldn\'t find anything about “SAAS launch”.');
    expect(note).not.toContain('in your knowledge.');
    const payload = { ...groundingPayload(), grounding: { ...groundingPayload().grounding!, places: ['Atlassian Rovo', 'e2esaas'] } };
    const words = cardWords(payload, i18n.t, 'en-US');
    expect(words.question(QUESTION)).toBe(
      'I looked in your PipesHub knowledge, Atlassian Rovo, and e2esaas and couldn\'t find anything about “SAAS launch”. Did you mean one of these?',
    );
    expect(words.intent('x')).toBe('Before starting the work, I looked for “SAAS launch” in your PipesHub knowledge, Atlassian Rovo, and e2esaas.');
  });

  it.each(LOCALES)('%s: the places wording is translated', (locale) => {
    for (const key of ['chat.notFound.knowledge', 'chat.notFound.titleIn', 'askUserQuestion.grounding.intentIn',
      'askUserQuestion.grounding.questionNearIn', 'askUserQuestion.grounding.questionNoneIn']) {
      expect(i18n.exists(key, { lng: locale, fallbackLng: false }), `${locale} ${key}`).toBe(true);
      if (!locale.startsWith('en')) expect(i18n.t(key, { lng: locale, places: 'P', subject: 'S' })).not.toBe(i18n.t(key, { lng: 'en-US', places: 'P', subject: 'S' }));
    }
  });
});

describe('FU-2: the grounding card in the reader’s language', () => {
  it('the card is worded from its typed fields; its answer sends the English it was asked in', () => {
    const onSubmit = vi.fn();
    render(<Theme><AskUserQuestionCard payload={groundingPayload()} initialAnswers={{}} status="pending" onSubmit={onSubmit} /></Theme>);
    expect(screen.getByRole('heading', { name: `1. ${QUESTION}` })).toBeTruthy();
    expect(screen.getByText('Not found')).toBeTruthy();
    fireEvent.click(screen.getByText(USE_FOUND));
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toBe(`User selections:\n1. "${QUESTION}" → ${USE_FOUND}`);
  });

  it.each(LOCALES)('%s: the words the card shows come from the typed fields, never the English it was sent with', async (locale) => {
    await i18n.changeLanguage(locale);
    const words = cardWords(groundingPayload(), i18n.t);
    expect(words.question(QUESTION)).toBe(i18n.t('askUserQuestion.grounding.questionNear', { subject: 'SAAS launch' }));
    expect(words.intent('ignored')).toContain('SAAS launch');
    expect(words.label(USE_FOUND)).toBe(i18n.t('askUserQuestion.grounding.useFound'));
    expect(words.label(NAME_IT)).toBe(i18n.t('askUserQuestion.grounding.nameIt'));
    // A near match is a record's own name: never translated.
    expect(words.label('product-faq')).toBe('product-faq');
    const none = { ...groundingPayload(), grounding: { subject: 'SAAS launch', near: [], useFound: USE_FOUND, nameIt: NAME_IT, places: [] } };
    expect(cardWords(none, i18n.t).question(QUESTION)).toBe(i18n.t('askUserQuestion.grounding.questionNone', { subject: 'SAAS launch' }));
    if (!locale.startsWith('en')) expect(words.label(USE_FOUND)).not.toBe(USE_FOUND);
  });

  it('the receipt names the choice in the reader’s language; a card without typed fields reads as sent', async () => {
    await i18n.changeLanguage('de-DE');
    const payload = groundingPayload();
    const answers = { 'q-ground': { questionUuid: 'q-ground', selectedOptionIds: ['use'], userInputs: {} } };
    expect(answerSummary(payload, answers, 'x', 'y', cardWords(payload, i18n.t).label)).toBe('Mit dem Gefundenen arbeiten');
    const plain = { ...payload, grounding: undefined };
    expect(answerSummary(plain, answers, 'x', 'y', cardWords(plain, i18n.t).label)).toBe(USE_FOUND);
    expect(cardWords(plain, i18n.t).question(QUESTION)).toBe(QUESTION);
  });
});

describe('FU-2: the summary under an answered question follows the answer', () => {
  const message = (overrides: Partial<ConversationMessage>): ConversationMessage => ({
    _id: 'm', messageType: 'user_query', content: '', contentFormat: 'MARKDOWN', citations: [], followUpQuestions: [],
    feedback: [], createdAt: '2026-11-11T00:00:00.000Z', updatedAt: '2026-11-11T00:00:00.000Z', ...overrides,
  } as ConversationMessage);

  it('the row the reply lands on names the run that wrote that reply', () => {
    const card = { name: 'ask_user_question', questions: [{ uuid: 'q', question: 'Which launch do you mean?', multiSelect: false,
      options: [{ id: 'q-o0', label: 'Orbit GA', isUserInput: false }, { id: 'q-o1', label: 'SAAS beta', isUserInput: false }] }] };
    const loaded = loadHistoricalMessages([
      message({ _id: 'u1', content: 'check the launch dates' }),
      message({ _id: 't1', messageType: 'tool_call', tools: [{ toolName: 'ask_user_question', toolResult: card }] } as Partial<ConversationMessage>),
      message({ _id: 'b1', messageType: 'bot_response', content: '', runId: 'run-asked' }),
      message({ _id: 'u2', content: 'User selections:\n1. "Which launch do you mean?" → Orbit GA' }),
      message({ _id: 'b2', messageType: 'bot_response', content: 'The launch dates are ready.', runId: 'run-answered', notFound: { subject: 'launch', hits: 0, line: 'L' } }),
    ]);
    const row = loaded.messages.find((m) => m.id === 'b1');
    expect(row).toBeTruthy();
    expect(loaded.messages.filter((m) => m.role === 'assistant')).toHaveLength(1);
    expect(messageRunId(row?.metadata?.custom)).toBe('run-answered');
    expect((row?.metadata?.custom as Record<string, unknown>).notFound).toEqual({ subject: 'launch', hits: 0, line: 'L' });
  });
});

describe('FU-2: an answered card keeps what the server saved on its answer', () => {
  it('the live parts that replace the saved ones keep the final part\'s typed facts', () => {
    const fact = { subject: 'SAAS launch', hits: 0, line: LINE };
    const live: MessagePart[] = [{ type: 'tool_call', toolName: 'code_task' } as MessagePart, { type: 'text', content: 'Report.', isFinal: true }];
    const saved: MessagePart[] = [{ type: 'text', content: 'Report.', isFinal: true, notFound: fact, truncatedBy: 'time' }];
    const merged = withSavedFinalFacts(live, saved);
    expect(notFoundOf(merged)?.subject).toBe('SAAS launch');
    expect(merged[1].truncatedBy).toBe('time');
    expect(merged[0]).toBe(live[0]);
    // Nothing saved, or nothing final live: as it was.
    expect(withSavedFinalFacts(live, [])).toBe(live);
    expect(withSavedFinalFacts([{ type: 'text', content: 'x' }], saved)).toEqual([{ type: 'text', content: 'x' }]);
  });
});
