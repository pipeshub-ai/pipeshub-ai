import { describe, expect, it } from 'vitest';
import {
  EMPTY_SELECTION,
  MAX_KNOWLEDGE_LIMIT_IDS,
  isKnowledgeLimitValid,
  knowledgeLimitForApi,
  limitFromSelection,
  sameKnowledgeLimit,
  selectionFromLimit,
} from '../knowledge-limit';

describe('knowledge limit', () => {
  it('sends nothing for everything the user can see', () => {
    expect(knowledgeLimitForApi(null)).toBeUndefined();
    expect(isKnowledgeLimitValid(null)).toBe(true);
  });

  it('sends collections as apps and drops repeated ids', () => {
    expect(
      limitFromSelection({ apps: ['a'], kb: ['kb-1', 'a'], recordGroups: ['g'], records: ['f'], recordsExact: ['r'] })
    ).toEqual({ apps: ['a', 'kb-1'], recordGroups: ['g'], records: ['f', 'r'] });
  });

  it('will not send a pick that names nothing, which would hide everything', () => {
    expect(isKnowledgeLimitValid(EMPTY_SELECTION)).toBe(false);
  });

  it('will not send more ids than the API takes', () => {
    const records = Array.from({ length: MAX_KNOWLEDGE_LIMIT_IDS + 1 }, (_, n) => `r${n}`);
    expect(isKnowledgeLimitValid({ ...EMPTY_SELECTION, records })).toBe(false);
    expect(isKnowledgeLimitValid({ ...EMPTY_SELECTION, records: records.slice(1) })).toBe(true);
  });

  it("opens an app's stored limit in the picker and saves it back unchanged", () => {
    const limit = { apps: ['a', 'kb-1'], recordGroups: ['g'], records: ['f'] };
    expect(selectionFromLimit(undefined)).toBeNull();
    expect(knowledgeLimitForApi(selectionFromLimit(limit))).toEqual(limit);
  });

  it('treats two limits naming the same items in any order as the same; absent matches only absent', () => {
    const a = { apps: ['a', 'b'], recordGroups: ['g'], records: [] };
    const b = { apps: ['b', 'a'], recordGroups: ['g'], records: [] };
    expect(sameKnowledgeLimit(a, b)).toBe(true);
    expect(sameKnowledgeLimit(undefined, undefined)).toBe(true);
    expect(sameKnowledgeLimit(a, undefined)).toBe(false);
    expect(sameKnowledgeLimit(a, { ...a, records: ['r'] })).toBe(false);
    // An id in a different list reaches something else.
    expect(sameKnowledgeLimit(a, { apps: ['a'], recordGroups: ['g', 'b'], records: [] })).toBe(false);
  });
});
