import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { StreamError } from '@/lib/api/stream-errors';
import { CONVERSATION_ERROR_CODES } from '../../collaboration-types';
import {
  GENERIC_CONVERSATION_ERROR_KEY,
  conversationErrorKey,
  conversationErrorMessage,
  conversationErrorParams,
  describeConversationError,
  isAccessLostError,
  retryAfterSecondsOf,
} from '../conversation-errors';

const enUS = JSON.parse(
  readFileSync(resolve(process.cwd(), 'lib/i18n/locales/en-US.json'), 'utf8'),
) as Record<string, unknown>;

function lookup(key: string): unknown {
  return key.split('.').reduce<unknown>((n, p) => (n as Record<string, unknown> | undefined)?.[p], enUS);
}

describe('describeConversationError', () => {
  it('PH09-07: every code maps to an existing en-US message', () => {
    for (const code of CONVERSATION_ERROR_CODES) {
      const info = describeConversationError({ code });
      expect(info.code).toBe(code);
      expect(info.i18nKey).toBe(conversationErrorKey(code));
      expect(typeof lookup(info.i18nKey)).toBe('string');
    }
    expect(typeof lookup(GENERIC_CONVERSATION_ERROR_KEY)).toBe('string');
  });

  it('PH10-14: the mention codes have a message in every locale and a UI action', () => {
    const dir = resolve(process.cwd(), 'lib/i18n/locales');
    const locales = readdirSync(dir).filter((f) => f.endsWith('.json'));
    expect(locales).toHaveLength(9);
    const codes = [
      'MENTION_NOT_ALLOWED',
      'MENTION_SA_AGENT_SHARED',
      'MENTION_DIRECTORY_UNAVAILABLE',
      'MESSAGE_IS_NOTE',
      'MESSAGE_NOT_NOTE',
    ] as const;
    for (const file of locales) {
      const json = JSON.parse(readFileSync(resolve(dir, file), 'utf8')) as Record<string, unknown>;
      const at = (key: string) => key.split('.').reduce<unknown>((n, p) => (n as Record<string, unknown> | undefined)?.[p], json);
      for (const code of codes) {
        expect(typeof at(conversationErrorKey(code)), `${file} ${code}`).toBe('string');
      }
      expect(typeof at('notifications.mention.title'), file).toBe('string');
      expect(typeof at('notifications.mention.body'), file).toBe('string');
    }
    for (const code of codes) {
      expect(describeConversationError({ code })).toMatchObject({ code, action: 'inline' });
    }
    expect(describeConversationError('MENTION_DIRECTORY_UNAVAILABLE').retryable).toBe(true);
    expect(describeConversationError('MESSAGE_IS_NOTE').retryable).toBe(false);
  });

  it('falls back to the generic message for an unknown or missing code', () => {
    for (const e of [{ code: 'SOMETHING_NEW' }, new Error('x'), null, undefined, 'NOPE']) {
      expect(describeConversationError(e)).toMatchObject({
        code: null,
        i18nKey: GENERIC_CONVERSATION_ERROR_KEY,
        action: 'toast',
        retryable: false,
      });
    }
  });

  it('accepts a bare code and a StreamError', () => {
    expect(describeConversationError('CONVERSATION_BUSY')).toMatchObject({ action: 'silent' });
    expect(describeConversationError(new StreamError('x', 403, 'RESUME_NOT_ALLOWED'))).toMatchObject({
      code: 'RESUME_NOT_ALLOWED',
      action: 'inline',
    });
  });

  it('marks transient failures retryable', () => {
    for (const code of ['TEAM_RESOLUTION_UNAVAILABLE', 'OWNER_STATUS_UNAVAILABLE', 'RATE_LIMITED']) {
      expect(describeConversationError(code).retryable).toBe(true);
    }
    expect(describeConversationError('CONVERSATION_NOT_FOUND').retryable).toBe(false);
  });
});

describe('conversationErrorParams / conversationErrorMessage', () => {
  it('reads interpolation values from details', () => {
    expect(
      conversationErrorParams({
        details: { max: 200, retryAfter: 7, newerCount: 2, activeRun: { displayName: 'Bob' }, toolsets: ['jira', 'slack'] },
      }),
    ).toEqual({ max: 200, retryAfter: 7, count: 2, name: 'Bob', toolsets: 'jira, slack' });
    expect(conversationErrorParams(new Error('x'))).toEqual({});
    expect(conversationErrorParams({ details: { max: 'x' } })).toEqual({});
  });

  it('translates through the supplied t', () => {
    const t = ((key: string) => `T:${key}`) as unknown as Parameters<typeof conversationErrorMessage>[0];
    expect(conversationErrorMessage(t, { code: 'RUN_LOST' })).toBe('T:chat.collab.errors.RUN_LOST');
    expect(conversationErrorMessage(t, {})).toBe(`T:${GENERIC_CONVERSATION_ERROR_KEY}`);
  });
});

describe('isAccessLostError', () => {
  it('is true for 404 and 403 only', () => {
    expect(isAccessLostError({ statusCode: 404 })).toBe(true);
    expect(isAccessLostError({ statusCode: 403 })).toBe(true);
    expect(isAccessLostError(new StreamError('x', 404))).toBe(true);
    expect(isAccessLostError({ statusCode: 503 })).toBe(false);
    expect(isAccessLostError({ statusCode: 429 })).toBe(false);
    expect(isAccessLostError(new Error('network'))).toBe(false);
  });
});

describe('retryAfterSecondsOf', () => {
  it('returns a positive retryAfter only', () => {
    expect(retryAfterSecondsOf({ details: { retryAfter: 12 } })).toBe(12);
    expect(retryAfterSecondsOf({ details: { retryAfter: 0 } })).toBeUndefined();
    expect(retryAfterSecondsOf({ details: { retryAfter: '5' } })).toBeUndefined();
    expect(retryAfterSecondsOf({})).toBeUndefined();
  });
});
