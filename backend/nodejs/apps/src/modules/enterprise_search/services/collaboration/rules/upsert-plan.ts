import { SHARED_WITH_MAX } from '../../../constants/constants';
import { principalKey, toCollaborator } from '../domain/collaborator.mapper';
import {
  CollaboratorLimitError,
  ConversationOwnerOnlyError,
  InvalidPrincipalError,
  OrgWideConfirmationRequiredError,
} from '../domain/errors';
import {
  AccessLevel,
  Collaborator,
  Principal,
  PrincipalKey,
  StoredCollaborator,
} from '../domain/types';
import { orgWideTeamId } from '../principals/principal-resolver';

export interface RequestedCollaborator {
  principal: Principal;
  accessLevel: AccessLevel;
}

export type PlannedOp =
  | { kind: 'add'; principal: Principal; accessLevel: AccessLevel }
  | {
      kind: 'changeLevel';
      principal: Principal;
      from: AccessLevel;
      to: AccessLevel;
    };

export interface OrgWideRules {
  orgId: string;
  confirmOrgWide: boolean;
  writeAllowed: boolean;
}

export const isOrgWide = (p: Principal, orgId: string): boolean =>
  p.type === 'team' && p.teamId === orgWideTeamId(orgId);

/** Sharing with everyone needs an explicit confirmation, and `write` a platform switch (LC-15). */
export function assertOrgWideAllowed(
  requested: readonly RequestedCollaborator[],
  rules: OrgWideRules,
): void {
  const orgWide = requested.filter((r) => isOrgWide(r.principal, rules.orgId));
  if (orgWide.length === 0) {
    return;
  }
  if (!rules.confirmOrgWide) {
    throw new OrgWideConfirmationRequiredError();
  }
  const blocked = orgWide.filter((r) => r.accessLevel === 'write');
  if (blocked.length > 0 && !rules.writeAllowed) {
    throw new InvalidPrincipalError(
      blocked.map((r) => ({
        key: principalKey(r.principal),
        reason: 'org_wide_write_disabled',
      })),
    );
  }
}

/**
 * Splits the request into adds and level changes against the rows as the guard saw them.
 * An editor (`isOwner` false) may only add: changing the level of an existing principal is owner-only (LC-09).
 * Rows already at the requested level are skipped. Total capacity is checked before anything is written.
 */
export function planUpsert(args: {
  requested: readonly RequestedCollaborator[];
  existing: readonly StoredCollaborator[];
  isOwner: boolean;
}): PlannedOp[] {
  const current = new Map<PrincipalKey, Collaborator>();
  for (const row of args.existing) {
    const c = toCollaborator(row);
    if (c) {
      current.set(principalKey(c.principal), c);
    }
  }
  const ops: PlannedOp[] = [];
  for (const { principal, accessLevel } of args.requested) {
    const found = current.get(principalKey(principal));
    if (!found) {
      ops.push({ kind: 'add', principal, accessLevel });
    } else if (found.accessLevel !== accessLevel) {
      if (!args.isOwner) {
        throw new ConversationOwnerOnlyError();
      }
      ops.push({
        kind: 'changeLevel',
        principal,
        from: found.accessLevel,
        to: accessLevel,
      });
    }
  }
  const adds = ops.filter((o) => o.kind === 'add').length;
  if (current.size + adds > SHARED_WITH_MAX) {
    throw new CollaboratorLimitError(SHARED_WITH_MAX);
  }
  return ops;
}
