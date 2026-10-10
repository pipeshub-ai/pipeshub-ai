import {
  AccessLevel,
  Collaborator,
  Principal,
  PrincipalKey,
  StoredCollaborator,
} from './types';

const idString = (value: unknown): string | undefined => {
  if (value === null || value === undefined) {
    return undefined;
  }
  const text =
    typeof value === 'string'
      ? value
      : (value as { toString(): string }).toString();
  return text.length > 0 ? text : undefined;
};

const level = (value: unknown): AccessLevel =>
  value === 'write' ? 'write' : 'read';

/** Presence of an id decides the principal, never `principalType` (74 §1). A row with neither id is `null`. */
export function toCollaborator(row: StoredCollaborator): Collaborator | null {
  const userId = idString(row.userId);
  const teamId = userId === undefined ? idString(row.teamId) : undefined;
  let principal: Principal;
  if (userId !== undefined) {
    principal = { type: 'user', userId };
  } else if (teamId !== undefined) {
    principal = { type: 'team', teamId };
  } else {
    return null;
  }
  return {
    principal,
    accessLevel: level(row.accessLevel),
    ...(row.addedBy !== undefined &&
      row.addedBy !== null && { addedBy: idString(row.addedBy) }),
    ...(row.addedAt && { addedAt: row.addedAt }),
  };
}

export function toStoredCollaborator(c: Collaborator): StoredCollaborator {
  return {
    principalType: c.principal.type,
    ...(c.principal.type === 'user'
      ? { userId: c.principal.userId }
      : { teamId: c.principal.teamId }),
    accessLevel: c.accessLevel,
    ...(c.addedBy !== undefined && { addedBy: c.addedBy }),
    ...(c.addedAt && { addedAt: c.addedAt }),
  };
}

export function principalKey(p: Principal): PrincipalKey {
  return p.type === 'user' ? `user:${p.userId}` : `team:${p.teamId}`;
}

export function parsePrincipalKey(key: string): Principal | null {
  const sep = key.indexOf(':');
  if (sep < 0) {
    return null;
  }
  const kind = key.slice(0, sep);
  const id = key.slice(sep + 1);
  if (id.length === 0) {
    return null;
  }
  if (kind === 'user') {
    return { type: 'user', userId: id };
  }
  return kind === 'team' ? { type: 'team', teamId: id } : null;
}
