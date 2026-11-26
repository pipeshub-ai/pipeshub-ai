/** A learned skill candidate's approval target id (`catalog-item`), until it is a catalog item. */
export const SKILL_CANDIDATE_PREFIX = 'skill-candidate:';
export const candidateTargetId = (candidateId: string): string =>
  `${SKILL_CANDIDATE_PREFIX}${candidateId}`;
