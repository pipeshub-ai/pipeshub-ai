import { CanonicalRole, rank } from '../../../../authz/domain/ladder';
import { chatRole, isUnresolved } from '../../../../authz/domain/rules';
import {
  AccessPath,
  ChatFacts,
  CollaboratorFact,
  ProjectFacts,
  Subject,
} from '../../../../authz/domain/types';
import { toCollaborator } from '../domain/collaborator.mapper';
import { COLLAB_ERROR_CODES } from '../domain/errors';
import {
  AccessView,
  Caller,
  ConversationAccessFields,
  ConversationOperation,
  ConversationRole,
  GrantedRole,
} from '../domain/types';

export interface AccessPolicyOptions {
  /** The collaboration flag. Off collapses `write` to `read`. */
  readonly collab: boolean;
}

export interface OperationContext {
  /** Regenerate: the caller asked the question being regenerated. */
  readonly isAuthorOfAnsweredQuestion?: boolean;
  /** Resume: the pending `ask_user_question` was addressed to the caller. */
  readonly isRequester?: boolean;
  readonly editorsCanInvite?: boolean;
  /** Cached directory flag; unknown (undefined) is treated as active so a directory outage does not block every send. */
  readonly ownerActive?: boolean;
}

export type DenyCode = (typeof COLLAB_ERROR_CODES)[
  | 'NOT_FOUND'
  | 'READ_ONLY'
  | 'OWNER_ONLY'
  | 'REGENERATE_NOT_ALLOWED'
  | 'RESUME_NOT_ALLOWED'
  | 'OWNER_INACTIVE'
  | 'TEAM_RESOLUTION_UNAVAILABLE'];

export type AccessDecision =
  | { readonly allowed: true; readonly role: GrantedRole }
  | {
      readonly allowed: false;
      readonly status: 403 | 404 | 503;
      readonly code: DenyCode;
    };

export interface OperationRequirement {
  readonly min: 'read' | 'write' | 'owner';
  /** Code when a granted role falls short of `min`. */
  readonly belowMin: 'READ_ONLY' | 'OWNER_ONLY';
  readonly guard?: 'asker' | 'requester' | 'editorsCanInvite' | 'nonOwner';
  readonly requiresActiveOwner?: true;
}

const READ_OPS = {
  min: 'read',
  belowMin: 'READ_ONLY',
} as const satisfies OperationRequirement;
const SEND_OPS = {
  min: 'write',
  belowMin: 'READ_ONLY',
} as const satisfies OperationRequirement;
const OWNER_OPS = {
  min: 'owner',
  belowMin: 'OWNER_ONLY',
} as const satisfies OperationRequirement;

/** The 51 §2 operation table; the policy tests are generated from it. */
export const OPERATION_REQUIREMENTS: Readonly<
  Record<ConversationOperation, OperationRequirement>
> = {
  read: READ_OPS,
  feedback: READ_OPS,
  archiveSelf: READ_OPS,
  leave: { ...READ_OPS, guard: 'nonOwner' },
  send: { ...SEND_OPS, requiresActiveOwner: true },
  cancel: SEND_OPS,
  regenerate: { ...SEND_OPS, guard: 'asker', requiresActiveOwner: true },
  // No route authorizes `resume`: it rides on `send`, and `runLease()` enforces this row through resume-binding.ts.
  resume: { ...SEND_OPS, guard: 'requester', requiresActiveOwner: true },
  invite: { ...SEND_OPS, guard: 'editorsCanInvite' },
  manageCollaborators: OWNER_OPS,
  settings: OWNER_OPS,
  transfer: OWNER_OPS,
  rename: OWNER_OPS,
  linkProject: OWNER_OPS,
  delete: OWNER_OPS,
};

const ROLE_RANK: Readonly<Record<ConversationRole, number>> = {
  none: 0,
  read: 1,
  write: 2,
  owner: 3,
};

const NEEDED_CANONICAL: Readonly<
  Record<OperationRequirement['min'], CanonicalRole>
> = { read: 'viewer', write: 'editor', owner: 'owner' };

const deny = (status: 403 | 404 | 503, code: DenyCode): AccessDecision => ({
  allowed: false,
  status,
  code,
});

export function toConversationRole(role: CanonicalRole): ConversationRole {
  if (role === 'none') {
    return 'none';
  }
  if (role === 'owner') {
    return 'owner';
  }
  return rank(role) >= rank('editor') ? 'write' : 'read';
}

export interface RoleResolution {
  readonly role: ConversationRole;
  readonly via: readonly AccessPath[];
}

export type RoleResult = RoleResolution | { readonly unresolved: true };

export function toChatFacts(
  session: ConversationAccessFields,
  project: ProjectFacts | null,
): ChatFacts {
  const sharedWith: CollaboratorFact[] = [];
  for (const row of session.sharedWith ?? []) {
    const c = toCollaborator(row);
    if (c) {
      sharedWith.push(
        c.principal.type === 'user'
          ? { userId: c.principal.userId, accessLevel: c.accessLevel }
          : { teamId: c.principal.teamId, accessLevel: c.accessLevel },
      );
    }
  }
  const projectId = session.projectId?.toString() ?? null;
  return {
    orgId: session.orgId.toString(),
    ownerId: session.userId.toString(),
    sharedWith,
    projectId,
    projectVisibility: session.projectVisibility ?? 'private',
    project: projectId === null ? null : project,
  };
}

/** Delegates to the shared H1 rule. Pass the operation so teams are only consulted when earlier paths fall short of it. */
export function resolveRole(
  session: ConversationAccessFields,
  caller: Caller,
  options: AccessPolicyOptions & {
    op?: ConversationOperation;
    project?: ProjectFacts | null;
  },
): RoleResult {
  if (session.isDeleted === true) {
    return { role: 'none', via: [] };
  }
  const subject: Subject = {
    userId: caller.userId,
    orgId: caller.orgId,
    teamIds: caller.teamIds,
  };
  const needed =
    options.op !== undefined
      ? NEEDED_CANONICAL[OPERATION_REQUIREMENTS[options.op].min]
      : undefined;
  const result = chatRole(
    toChatFacts(session, options.project ?? null),
    subject,
    { collab: options.collab, ...(needed !== undefined && { needed }) },
  );
  if (isUnresolved(result)) {
    return { unresolved: true };
  }
  return { role: toConversationRole(result.role), via: result.via };
}

/** `'unresolved'`: only team rows could grant and the caller's teams could not be resolved. */
export function decide(
  op: ConversationOperation,
  role: ConversationRole | 'unresolved',
  ctx: OperationContext = {},
): AccessDecision {
  if (role === 'unresolved') {
    return deny(503, COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE);
  }
  if (role === 'none') {
    return deny(404, COLLAB_ERROR_CODES.NOT_FOUND);
  }
  const req = OPERATION_REQUIREMENTS[op];
  if (ROLE_RANK[role] < ROLE_RANK[req.min]) {
    return deny(403, COLLAB_ERROR_CODES[req.belowMin]);
  }
  if (req.guard === 'nonOwner' && role === 'owner') {
    return deny(403, COLLAB_ERROR_CODES.OWNER_ONLY);
  }
  if (
    req.guard === 'editorsCanInvite' &&
    role === 'write' &&
    ctx.editorsCanInvite !== true
  ) {
    return deny(403, COLLAB_ERROR_CODES.OWNER_ONLY);
  }
  if (req.guard === 'asker' && ctx.isAuthorOfAnsweredQuestion !== true) {
    return deny(403, COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED);
  }
  if (req.guard === 'requester' && ctx.isRequester !== true) {
    return deny(403, COLLAB_ERROR_CODES.RESUME_NOT_ALLOWED);
  }
  if (req.requiresActiveOwner === true && ctx.ownerActive === false) {
    return deny(403, COLLAB_ERROR_CODES.OWNER_INACTIVE);
  }
  return { allowed: true, role };
}

export function toAccessView(
  role: GrantedRole,
  session: ConversationAccessFields,
  caller: Caller,
  flags: AccessPolicyOptions & { ownerActive?: boolean },
): AccessView {
  const ctx: OperationContext = {
    editorsCanInvite: session.settings?.editorsCanInvite === true,
    ownerActive: flags.ownerActive,
  };
  const sharedNonEmpty = (session.sharedWith ?? []).length > 0;
  return {
    role,
    isOwner: session.userId.toString() === caller.userId,
    accessLevel: role,
    canSend: decide('send', role, ctx).allowed,
    canManage: decide('manageCollaborators', role, ctx).allowed,
    canInvite: decide('invite', role, ctx).allowed,
    isCollaborative:
      flags.collab &&
      (sharedNonEmpty || session.projectVisibility === 'project'),
  };
}
