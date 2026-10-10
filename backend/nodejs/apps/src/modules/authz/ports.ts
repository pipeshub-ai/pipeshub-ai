import { Types } from 'mongoose';
import { CanonicalRole } from './domain/ladder';
import { ChatExplanation } from './domain/explain';
import { AccessPath, ProjectFacts, Subject } from './domain/types';
import {
  ConversationAccessFields,
  ConversationRef,
} from '../enterprise_search/services/collaboration/domain/types';
import { OperationContext } from '../enterprise_search/services/collaboration/access/conversation-access.policy';

export type ResourceRef =
  | { type: 'chat'; id: string }
  | { type: 'project'; id: string }
  | { type: 'chatAttachment'; id: string; conversationId: string }
  | { type: 'chatArtifact'; id: string; conversationId: string };

export interface Decision {
  allow: boolean;
  role: CanonicalRole;
  via: readonly AccessPath[];
  /** Version of the resource ACL the decision was made against. */
  aclVersion?: number;
  /** Contract error code on a deny. */
  code?: string;
}

export interface CheckContext {
  /** Scopes the per-request decision memo; defaults to the subject object. */
  request?: object;
  requestId?: string;
  /** Per-operation facts (asker, requester, owner activity). A check with these is never cached. */
  operation?: OperationContext;
  /** The chat as the caller already loaded it, so a guard reads it once. Trusted; never from a client. */
  loaded?: LoadedChat;
  /** Set with the flag off: the decision follows LEGACY_REQUIREMENTS for this kind. */
  legacyKind?: ConversationRef['kind'];
}

export interface LoadedChat {
  session: ConversationAccessFields;
  /** Null when the chat has no project or the project no longer exists (H9). */
  project: ProjectFacts | null;
}

/** Identity and state fields a guard stores on its grant. */
export interface ScopedSession extends ConversationAccessFields {
  readonly _id: Types.ObjectId;
  readonly initiator?: { toString(): string };
  readonly isArchived?: boolean;
  readonly agentKey?: string;
}

export interface ScopedLoadedChat extends LoadedChat {
  session: ScopedSession;
}

export interface IChatAccessLoader {
  /** Null when the chat does not exist in the org. */
  load(orgId: string, chatId: string): Promise<LoadedChat | null>;
}

export interface IContentOwnershipLoader {
  /** Who created the attachment or artifact, and its kind. Null when it does not exist. */
  load(
    resource: Extract<ResourceRef, { type: 'chatAttachment' | 'chatArtifact' }>,
  ): Promise<{ creatorId: string; kind?: string } | null>;
}

/** A user turn that attached the record. */
export interface AttachmentTurnRow {
  sessionId: string;
  /** Absent on legacy rows. */
  authorUserId?: string;
  /** Absent on legacy rows, which carry no consent. */
  filesShared?: boolean;
}

/** The user turn of one run. */
export interface RunTurn {
  authorUserId?: string;
  shareToolResults?: boolean;
}

export interface IChatContentLoader {
  /** User turns that attached the record, newest first, at most 20; one chat only when `conversationId` is given. */
  loadAttachmentContext(
    orgId: string,
    recordId: string,
    conversationId?: string,
  ): Promise<AttachmentTurnRow[]>;
  /** The user turn of that run in that chat; null without a `runId` or when no such turn exists. */
  loadArtifactContext(
    orgId: string,
    conversationId: string,
    runId?: string,
  ): Promise<RunTurn | null>;
}

export interface IScopedChatLoader {
  /** One session read scoped by id, org, kind and agentKey; soft-deleted chats are absent. */
  loadScoped(
    orgId: string,
    target: { id: string; kind: ConversationRef['kind']; agentKey?: string },
  ): Promise<ScopedLoadedChat | null>;
}

/** Engine-neutral PDP contract (ADR-001). */
export interface IAuthorizationService {
  check(
    subject: Subject,
    action: string,
    resource: ResourceRef,
    context?: CheckContext,
  ): Promise<Decision>;
  explain(subject: Subject, resource: ResourceRef): Promise<ChatExplanation>;
}
