import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  handleServerError,
  normalizeHandleInput,
  slugifyAgentName,
  validateAgentHandle,
} from '../agent-handle-utils';

/** The same table `handles.slugify` is tested against in Python. */
const SLUG_CASES = (
  JSON.parse(
    readFileSync(resolve(process.cwd(), '../backend/python/tests/unit/modules/agents/slug_cases.json'), 'utf8'),
  ) as { cases: [string, string][] }
).cases;

describe('slugifyAgentName (mirrors app/modules/agents/handles.py)', () => {
  it.each(SLUG_CASES)('%j -> %s', (name, expected) => {
    expect(slugifyAgentName(name)).toBe(expected);
  });

  it('leaves room for a numeric suffix and never ends in a hyphen', () => {
    expect(slugifyAgentName('a'.repeat(200)).length).toBeLessThanOrEqual(37);
    expect(slugifyAgentName('word '.repeat(30)).endsWith('-')).toBe(false);
  });

  it('always yields a valid, unreserved handle', () => {
    for (const name of ['Sales Bot', '🚀', 'Assistant', 'x', 'Ünï', 'a'.repeat(90), '!!', '9 lives']) {
      expect(validateAgentHandle(slugifyAgentName(name))).toBeNull();
    }
  });
});

describe('validateAgentHandle', () => {
  it.each(['ab', 'a-b', 'x'.repeat(40), '007', '@my-bot'])('accepts %s', (handle) => {
    expect(validateAgentHandle(handle)).toBeNull();
  });

  it.each(['', 'a', 'Bad Handle', 'UPPER', 'a_b', 'x'.repeat(41), 'é-bot', '@'])('rejects %j as invalid', (handle) => {
    expect(validateAgentHandle(handle)).toBe('invalid');
  });

  it.each(['assistant', 'pipeshub', 'ai', 'bot', 'agent', 'everyone', 'here', 'all', '@assistant'])(
    'rejects %s as reserved',
    (handle) => {
      expect(validateAgentHandle(handle)).toBe('reserved');
    },
  );
});

describe('normalizeHandleInput', () => {
  it('drops a leading @ and surrounding spaces', () => {
    expect(normalizeHandleInput('  @sales-bot ')).toBe('sales-bot');
    expect(normalizeHandleInput('sales-bot')).toBe('sales-bot');
  });
});

describe('handleServerError', () => {
  it('reads the code and suggestion off a processed error', () => {
    expect(handleServerError({ code: 'HANDLE_TAKEN', details: { suggestion: 'sales-bot-2' } }, 'sales-bot')).toEqual({
      code: 'HANDLE_TAKEN',
      handle: 'sales-bot',
      suggestion: 'sales-bot-2',
    });
  });

  it('ignores other failures', () => {
    expect(handleServerError({ code: 'CONFLICT' }, 'x')).toBeNull();
    expect(handleServerError(new Error('boom'), 'x')).toBeNull();
    expect(handleServerError(null, 'x')).toBeNull();
  });
});
