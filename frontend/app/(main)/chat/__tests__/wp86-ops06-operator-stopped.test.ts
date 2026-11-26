/**
 * OPS-06 (ADR-OPS-01 D5, WP-86): a run an admin's control stopped tells the person so in one plain sentence for
 * the scope that stopped it, in their language, live and after a reload; a turn refused by a control says the
 * same. No ids and no admin note reach the reader.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SSEEvent } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { createAGUIEventHandler } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';
import {
  admissionRefusalText,
  nextActionKind,
  oneClickStep,
  operatorStopDetails,
  operatorStoppedText,
  runErrorText,
} from '../harness-errors';
import type { ConversationMessage } from '../types';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
  streamSSEGet: vi.fn(),
}));

const { loadHistoricalMessages } = await import('../runtime');

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];

const BY_SCOPE: Array<[string, string, string?]> = [
  ['session', 'chatStream.runError.operatorStoppedSession'],
  ['agent', 'chatStream.runError.operatorStoppedAgent'],
  ['org', 'chatStream.runError.operatorStoppedOrg'],
  ['engine_version', 'chatStream.runError.operatorStoppedEngineVersionSecurity', 'security'],
  ['engine_version', 'chatStream.runError.operatorStoppedEngineVersion', 'cost'],
  ['instance', 'chatStream.runError.operatorStoppedInstance'],
  ['egress', 'chatStream.runError.operatorStoppedEgress'],
];

const live = (details: unknown): string => {
  const onError = vi.fn();
  createAGUIEventHandler({ onError } as StreamMessageCallbacks)({
    event: 'RUN_ERROR',
    data: { type: 'RUN_ERROR', code: 'operator_stopped', message: 'Run cancelled by an operator control', details },
  } as SSEEvent);
  return (onError.mock.calls[0]?.[0] as Error).message;
};

const saved = (overrides: Partial<ConversationMessage>): ConversationMessage =>
  ({
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: '2026-11-05T00:00:00.000Z',
    updatedAt: '2026-11-05T00:00:00.000Z',
    ...overrides,
  }) as ConversationMessage;

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(async () => {
  await i18n.changeLanguage('en-US');
});

describe('OPS-06: the stopped reason, per scope', () => {
  it.each(BY_SCOPE)('%s (%s): the reply is the scope’s sentence', (scope, key, reason) => {
    const text = live({ scope, reason: reason ?? 'incident' });
    expect(text).toBe(i18n.t(key));
    expect(text).toMatch(/admin/i);
    expect(runErrorText('operator_stopped', null, { scope, reason: reason ?? 'incident' })).toBe(text);
  });

  it('the approved plain English', () => {
    expect(live({ scope: 'session' })).toBe('Your admin stopped agent work in this conversation for now.');
    expect(live({ scope: 'agent' })).toBe('Your admin stopped this agent for now.');
    expect(live({ scope: 'org' })).toBe('Your admin stopped agent work for your organization for now.');
    expect(live({ scope: 'engine_version', reason: 'security' })).toBe(
      'This coding engine version was stopped by your admin for a security fix. Send your message again to continue on a newer version.',
    );
    expect(live({ scope: 'instance' })).toBe('Your admin stopped agent work on this server for now.');
    expect(live({ scope: 'egress' })).toBe(
      'Your admin turned off internet access for coding sandboxes, so this task stopped. Send your message again; the assistant will answer without the coding engine.',
    );
  });

  it('no scope, or one this client does not know, reads as the generic sentence', () => {
    const generic = i18n.t('chatStream.runError.operatorStopped');
    expect(live(undefined)).toBe(generic);
    expect(live({ scope: 'galaxy' })).toBe(generic);
    expect(runErrorText('operator_stopped')).toBe(generic);
  });

  it('the words live in one place: a parsed stop reads the same through operatorStoppedText', () => {
    for (const [scope, key, reason] of BY_SCOPE) {
      const details = operatorStopDetails({ scope, reason: reason ?? 'incident', note: 'INC-42' });
      expect(details).toEqual({ scope, reason: reason ?? 'incident' });
      expect(operatorStoppedText(details)).toBe(i18n.t(key));
    }
    expect(operatorStoppedText(null)).toBe(i18n.t('chatStream.runError.operatorStopped'));
  });

  it('the parser takes only a known scope; an unknown reason keeps the scope as other', () => {
    expect(operatorStopDetails(undefined)).toBeNull();
    expect(operatorStopDetails('engine_version')).toBeNull();
    expect(operatorStopDetails({ scope: 'galaxy', reason: 'security' })).toBeNull();
    expect(operatorStopDetails({ scope: 'agent' })).toEqual({ scope: 'agent', reason: 'other' });
    expect(operatorStopDetails({ scope: 'engine_version', reason: 'boredom' })).toEqual({
      scope: 'engine_version',
      reason: 'other',
    });
  });

  it('never shows the admin note or an id', () => {
    const text = live({ scope: 'agent', reason: 'incident', note: 'INC-42 runaway loop', agentKey: 'agent-7f3' });
    expect(text).not.toMatch(/INC-42|agent-7f3/);
  });

  it('the next step is to ask an admin, with no retry button', () => {
    expect(nextActionKind('run', 'operator_stopped')).toBe('ask_admin');
    expect(oneClickStep('run', 'operator_stopped')).toBeNull();
  });

  it('a turn a control refuses says an admin stopped agent work here', () => {
    const text = admissionRefusalText('HTTP_AGENT_HARNESS_OPERATOR_STOPPED');
    expect(text).toBe(i18n.t('chatStream.admission.operatorStopped'));
    expect(text).toMatch(/admin/i);
    expect(admissionRefusalText('OPERATOR_STOPPED')).toBe(text);
    expect(nextActionKind('admission', 'OPERATOR_STOPPED')).toBe('ask_admin');
  });

  it('after a reload, the saved failure reads the same as live', () => {
    const { messages } = loadHistoricalMessages([
      saved({ _id: 'q', messageType: 'user_query', content: 'Refactor the parser' }),
      saved({
        _id: 'e',
        messageType: 'error',
        content: 'Run cancelled by an operator control',
        errorCode: 'operator_stopped',
        errorStop: { scope: 'engine_version', reason: 'security' },
      }),
      saved({ _id: 'q2', messageType: 'user_query', content: 'And now?' }),
      saved({ _id: 'e2', messageType: 'error', content: 'Run cancelled', errorCode: 'operator_stopped' }),
    ]);
    const textOf = (i: number) => (messages[i]!.content[0] as { text: string }).text;
    expect(textOf(1)).toBe(i18n.t('chatStream.runError.operatorStoppedEngineVersionSecurity'));
    expect(textOf(3)).toBe(i18n.t('chatStream.runError.operatorStopped'));
    expect((messages[1]!.metadata?.custom as Record<string, unknown>).streamError).toMatchObject({
      code: 'operator_stopped',
      source: 'run',
    });
  });

  it.each(LOCALES)('%s carries every sentence, translated', async (locale) => {
    const keys = [
      'chatStream.runError.operatorStopped',
      'chatStream.admission.operatorStopped',
      ...BY_SCOPE.map(([, key]) => key),
    ];
    for (const key of keys) {
      expect(i18n.exists(key, { lng: locale, fallbackLng: false }), `${locale} ${key}`).toBe(true);
      if (!locale.startsWith('en')) {
        expect(i18n.t(key, { lng: locale }), `${locale} ${key}`).not.toBe(i18n.t(key, { lng: 'en-US' }));
      }
    }
  });
});
