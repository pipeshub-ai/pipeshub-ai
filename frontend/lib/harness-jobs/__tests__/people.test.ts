import { describe, expect, it } from 'vitest';
import en from '@/lib/i18n/locales/en-US.json';
import { agentFromDirectory, ID_LIKE, personFromDirectory, personLabel } from '../people';

const t = (key: string) => key.split('.').reduce<unknown>((node, k) => (node as Record<string, unknown>)?.[k], en) as string;

describe('people: a person is named, never shown by id', () => {
  it('names a person by name, then email, then a word', () => {
    expect(personLabel({ name: 'Ada Lovelace', email: 'ada@x.io' }, t)).toBe('Ada Lovelace');
    expect(personLabel({ name: '', email: 'ada@x.io' }, t)).toBe('ada@x.io');
    expect(personLabel({ name: '65f1c0ffee0000000000abcd' }, t)).toBe('Someone in your org');
    expect(personLabel({}, t)).toBe('Someone in your org');
  });

  it('never shows the id when a directory lookup misses', () => {
    expect(personFromDirectory({ u1: 'Grace Hopper' }, 'u1', t)).toBe('Grace Hopper');
    expect(personFromDirectory({}, '65f1c0ffee0000000000abcd', t)).toBe('Someone in your org');
    expect(personFromDirectory({ x: '65f1c0ffee0000000000abcd' }, 'x', t)).toBe('Someone in your org');
    expect(agentFromDirectory({}, '1b2c3d4e-0000-4000-8000-000000000000', t)).toBe('An agent');
    expect(agentFromDirectory({ k: 'Sales helper' }, 'k', t)).toBe('Sales helper');
  });

  it('knows the id shapes the stores hand out', () => {
    for (const id of ['65f1c0ffee0000000000abcd', '1b2c3d4e-0000-4000-8000-000000000000', 'user:65f1c0ffee0000000000abcd']) expect(ID_LIKE.test(id)).toBe(true);
    for (const name of ['Ada', 'ada@x.io', 'cafe']) expect(ID_LIKE.test(name)).toBe(false);
  });
});
