import { TeamIds } from '../../../../authz/domain/types';

/** Stored value, unchanged. */
export type AccessLevel = 'read' | 'write';
export type ConversationRole = 'owner' | 'write' | 'read' | 'none';
export type GrantedRole = Exclude<ConversationRole, 'none'>;

/** A `sharedWith` row as persisted. */
export interface StoredShareRow {
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
  | 'manageSharing'
  | 'settings'
  | 'transfer'
  | 'rename'
  | 'linkProject'
  | 'delete';

/** What the API returns as `access` on detail and list, when the guard's policy describes it. */
export interface AccessView {
  readonly role: GrantedRole;
  readonly isOwner: boolean;
  readonly accessLevel: AccessLevel | 'owner';
}

/** Fields the access policy needs; satisfied by a hydrated doc and by `.lean()`. */
export interface ConversationAccessFields {
  readonly orgId: { toString(): string };
  readonly userId: { toString(): string };
  readonly isDeleted?: boolean;
  readonly isShared?: boolean;
  readonly sharedWith?: ReadonlyArray<StoredShareRow>;
  readonly projectId?: { toString(): string } | null;
  readonly projectVisibility?: 'private' | 'project';
}
