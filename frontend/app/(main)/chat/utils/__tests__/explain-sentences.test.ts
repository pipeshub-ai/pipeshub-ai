import { describe, it, expect } from 'vitest';
import { describeExplainPath } from '../explain-sentences';

const t = (key: string) => key;
const teams = new Map([['t1', 'Sales']]);

describe('describeExplainPath', () => {
  it('maps each path type to its own sentence key, for self and for another person', () => {
    const cases = [
      ['owner', null],
      ['direct', null],
      ['project', 'p1'],
    ] as const;
    for (const [type, ref] of cases) {
      expect(describeExplainPath({ type, ref, role: 'editor' }, t).key).toBe(`chat.collab.access.via.${type}.self`);
      expect(describeExplainPath({ type, ref, role: 'editor' }, t, { subjectName: 'Bob' }).key).toBe(
        `chat.collab.access.via.${type}.other`,
      );
    }
  });

  it('names a visible team, falls back when it has no name, and redacts a null ref', () => {
    const named = describeExplainPath({ type: 'team', ref: 't1', role: 'viewer' }, t, { teamNames: teams });
    expect(named).toEqual({
      key: 'chat.collab.access.via.team.self',
      vars: { action: 'chat.collab.access.action.viewer', name: '', team: 'Sales' },
    });
    expect(describeExplainPath({ type: 'team', ref: 'tX', role: 'viewer' }, t, { teamNames: teams }).key).toBe(
      'chat.collab.access.via.teamUnnamed.self',
    );
    expect(describeExplainPath({ type: 'team', ref: null, role: 'viewer' }, t, { teamNames: teams }).key).toBe(
      'chat.collab.access.via.teamRedacted.self',
    );
  });
});
