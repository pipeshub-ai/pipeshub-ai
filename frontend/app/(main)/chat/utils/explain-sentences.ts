import type { ExplainPath, ExplainRole } from '../collaboration-types';

export interface ExplainSentence {
  key: string;
  vars: Record<string, string>;
}

export interface ExplainSentenceContext {
  /** Display name of the explained person; absent when the caller explains themselves. */
  subjectName?: string;
  /** Team id to name, for the teams the caller can see. */
  teamNames?: ReadonlyMap<string, string>;
}

export const EXPLAIN_ACTION_KEYS: Record<ExplainRole, string> = {
  viewer: 'chat.collab.access.action.viewer',
  editor: 'chat.collab.access.action.editor',
  owner: 'chat.collab.access.action.owner',
};

/**
 * One path of `GET /authz/explain` as a translatable sentence. A team whose `ref` is null is one
 * the server redacted because the explained person is in it and the caller is not.
 */
export function describeExplainPath(
  path: ExplainPath,
  t: (key: string) => string,
  ctx: ExplainSentenceContext = {},
): ExplainSentence {
  const who = ctx.subjectName ? 'other' : 'self';
  const vars: Record<string, string> = {
    action: t(EXPLAIN_ACTION_KEYS[path.role]),
    name: ctx.subjectName ?? '',
  };
  switch (path.type) {
    case 'owner':
      return { key: `chat.collab.access.via.owner.${who}`, vars };
    case 'direct':
      return { key: `chat.collab.access.via.direct.${who}`, vars };
    case 'project':
      return { key: `chat.collab.access.via.project.${who}`, vars };
    case 'team': {
      if (path.ref === null) return { key: `chat.collab.access.via.teamRedacted.${who}`, vars };
      const team = ctx.teamNames?.get(path.ref);
      if (!team) return { key: `chat.collab.access.via.teamUnnamed.${who}`, vars };
      return { key: `chat.collab.access.via.team.${who}`, vars: { ...vars, team } };
    }
  }
}
