/**
 * QA14-11: the card masks a value under a secret-named key from the shared vector's list (QA11-8 moved onto it).
 */
import { describe, expect, it } from 'vitest';
import { redactedArgs } from '../approval-preview';
import { SECRET_KEY_NAMES, VECTOR_MASK } from './token-vector';

describe('QA14-11 secret key names come from the shared vector', () => {
  for (const key of SECRET_KEY_NAMES.matches) {
    it(`${key}: masked whole at any depth`, () => {
      const shown = JSON.parse(redactedArgs(JSON.stringify({ summary: 'deploy', nested: [{ [key]: 'hunter2' }] })));
      expect(shown).toEqual({ summary: 'deploy', nested: [{ [key]: VECTOR_MASK }] });
    });
  }

  it('ordinary names keep their values', () => {
    const plain = Object.fromEntries(SECRET_KEY_NAMES.ignores.map((k) => [k, 'hunter2']));
    expect(JSON.parse(redactedArgs(JSON.stringify(plain)))).toEqual(plain);
  });
});
