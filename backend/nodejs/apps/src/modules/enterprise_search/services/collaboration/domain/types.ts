import { TeamIds } from '../../../../authz/domain/types';
import type { RespondMode } from '../mentions/mention.types';

/** Stored value, unchanged. */
export type AccessLevel = 'read' | 'write';
export type ConversationRole = 'owner' | 'write' | 'read' | 'none';
export type GrantedRole = Exclude<ConversationRole, 'none'>;

export type Principal =
  | { readonly type: 'user'; readonly userId: string }
  | { readonly type: 'team'; readonly teamId: string };
export type PrincipalKey = `user:${string}` | `team:${string}`;

export interface Collaborator {
  readonly principal: Principal;
  readonly accessLevel: AccessLevel;
  readonly addedBy?: string;
  readonly addedAt?: Date;
}

/** A `sharedWith` row as persisted; legacy rows have no `principalType`. */
export interface StoredCollaborator {
  principalType?: 'user' | 'team';
  userId?: unknown;
  teamId?: string;
  accessLevel?: string;
  addedBy?: unknown;
  addedAt?: Date;
}

export type ConversationRef =
  | { readonly kind: 'chat'; readonly conversationId: string }
  | {
      readonly kind: 'agent';
      readonly conversationId: string;
      readonly agentKey: string;
    };

export interface Caller {
  readonly userId: string;
  readonly orgId: string;
  readonly teamIds: TeamIds;
}

export type ConversationOperation =
  | 'read'
  | 'feedback'
  | 'leave'
  | 'archiveSelf'
  | 'send'
  | 'cancel'
  | 'regenerate'
  | 'resume'
  | 'invite'
  | 'manageCollaborators'
  | 'settings'
  | 'transfer'
  | 'rename'
  | 'linkProject'
  | 'delete';

export interface ConversationSettings {
  readonly editorsCanInvite?: boolean;
  readonly ownerContentShared?: boolean;
  /** Absent reads as `smart`. */
  readonly respondMode?: RespondMode;
}

/** What the API returns as `access` on detail and list. */
export interface AccessView {
  readonly role: GrantedRole;
  readonly isOwner: boolean;
  readonly accessLevel: AccessLevel | 'owner';
  readonly canSend: boolean;
  readonly canManage: boolean;
  readonly canInvite: boolean;
  readonly isCollaborative: boolean;
}

/** Fields the policy needs; satisfied by a hydrated doc and by `.lean()`. */
export interface ConversationAccessFields {
  readonly orgId: { toString(): string };
  readonly userId: { toString(): string };
  readonly isDeleted?: boolean;
  readonly isShared?: boolean;
  readonly sharedWith?: ReadonlyArray<StoredCollaborator>;
  readonly projectId?: { toString(): string } | null;
  readonly projectVisibility?: 'private' | 'project';
  readonly settings?: ConversationSettings;
  readonly aclVersion?: number;
}
