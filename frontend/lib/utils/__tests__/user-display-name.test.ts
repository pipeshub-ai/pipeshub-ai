import { describe, expect, it } from 'vitest';
import { shortUserName, userDisplayName } from '../formatters';

describe('shortUserName', () => {
  it.each([
    ['Ada Lovelace', 'Ada L'],
    ['  Ada   Byron   Lovelace ', 'Ada L'],
    ['Ada', 'Ada'],
    ['   ', ''],
    [null, ''],
  ])('%j becomes %j', (fullName, expected) => {
    expect(shortUserName(fullName)).toBe(expected);
  });
});

describe('userDisplayName', () => {
  it.each([
    [{ name: ' Ada Lovelace ', email: 'ada@acme.test' }, 'Ada Lovelace'],
    [{ name: '  ', email: ' ada@acme.test ' }, 'ada@acme.test'],
    [{ name: null, email: null }, null],
    [null, null],
  ])('%j shows as %j', (user, expected) => {
    expect(userDisplayName(user)).toBe(expected);
  });
});
