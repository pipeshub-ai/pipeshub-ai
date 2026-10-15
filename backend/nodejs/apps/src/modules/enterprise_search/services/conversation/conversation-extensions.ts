import { RequestHandler, Router } from 'express';
import { Container } from 'inversify';
import { ClientSession, UpdateQuery } from 'mongoose';
import { AnyZodObject, ZodRawShape } from 'zod';
import { AuthenticatedUserRequest } from '../../../../libs/middlewares/types';
import { Logger } from '../../../../libs/services/logger.service';
import { CallerIdentity } from '../../../../libs/types/caller-identity';
import { IUserDirectory } from '../../../user_management/services/user-directory.service';
import { AppConfig } from '../../../tokens_manager/config/config';
import {
  IChatSession,
  IMessagePart,
} from '../../types/conversation.interfaces';
import { AccessView } from './domain/types';
import {
  ConversationAccessGrant,
  ConversationRequestContext,
} from './http/conversation-context';
import {
  IChatContentAccess,
  RecordPermissionChatContentAccess,
} from './sharing/chat-content-access';
import { IConversationSharing } from './sharing/conversation-sharing';
import { TurnExtension } from './turn/turn-extension';
import { ProjectUpdateExtension } from '../../../projects/services/project.service';
import { defaultTurnExtension } from './turn/default-turn-extension';

/** What the AI backend is told about one earlier tool call. */
export interface PreviousToolResult {
  tool_id?: string;
  tool_name?: string;
  args?: Record<string, unknown>;
  result: string;
  result_summary?: string;
  status: 'success' | 'error';
  artifact_id?: string;
}

/** How stored conversations are shown. */
export interface ConversationViewExtension {
  /** The title a list row or detail shows for a stored title. */
  displayTitle(title: string | undefined): string | undefined;
  /** One stored message as `viewerId` may see it; left out, as anyone may. */
  viewMessage<T extends object>(message: T, viewerId?: string): T;
}

/** What a conversation list adds to, or takes from, its rows. */
export interface ConversationListExtension {
  /** The `select` of list queries. */
  select(ctx: ConversationRequestContext): string;
  /** The caller's access per row id, for rows the policy describes. */
  accessViews(
    ctx: ConversationRequestContext,
    rows: readonly object[],
  ): Promise<Map<string, AccessView>>;
  /** Applied to every processed row of one list page. */
  rowDecorator(
    ctx: ConversationRequestContext,
    rows: readonly object[],
  ): Promise<<T extends object>(row: T) => T>;
  /** A row as a caller who does not own it may see it. */
  redactRecipients<T extends object>(
    ctx: ConversationRequestContext,
    row: T,
  ): T;
  /** The state clause of an archive list. */
  archivedStateFilter(ctx: ConversationRequestContext): Record<string, unknown>;
  /** The clause archive lists that build their own owner filter add, if any. */
  ownArchivedClause(
    ctx: ConversationRequestContext,
  ): Record<string, unknown> | undefined;
}

export interface ArchiveDecision {
  archived: boolean;
  /** The update to apply instead of the conversation-wide archive flag, when there is one. */
  update?: UpdateQuery<IChatSession>;
}

/** Archive and unarchive of one conversation. */
export interface ConversationArchiveExtension {
  /** Fields the archive routes load beyond the defaults. */
  readonly projection: string | undefined;
  decide(
    ctx: ConversationRequestContext,
    session: IChatSession,
    userId: string,
    action: 'archive' | 'unarchive',
  ): ArchiveDecision;
}

/** What a conversation detail adds to its messages. */
export interface ConversationDetailExtension {
  decorate<R extends { messages: object[] }>(
    req: AuthenticatedUserRequest,
    response: R,
    rows: readonly object[],
    context: { users: IUserDirectory; ownerId: string },
  ): Promise<R>;
}

/** How earlier turns are retold to the AI backend. */
export interface HistoryExtension {
  /** Replaces what the AI backend is told about one earlier tool call; undefined keeps the default. */
  toolResult(part: IMessagePart): PreviousToolResult | undefined;
}

/** Things that happened to a conversation, for an extension that reacts to them. */
export interface ConversationEventsExtension {
  /** Fire and forget; never throws. */
  turnCompleted(event: {
    orgId: string;
    sessionId: string;
    senderUserId: string;
  }): Promise<void>;
  /** Inside the delete's transaction when there is one: a failure aborts it. */
  conversationDeleted(
    req: AuthenticatedUserRequest,
    grant: ConversationAccessGrant,
    session?: ClientSession,
  ): Promise<void>;
}

/** Writes that change who may read a conversation or a project's conversations. */
export interface ConversationAclExtension {
  /** Update fields every such write carries. */
  versionBump(): Record<string, unknown>;
  /** The same change, on a document about to be saved. */
  bumpDocument(doc: object): void;
}

/** What a project update needs besides the shared settings. */
export interface ProjectSettingsExtension {
  /** The `PATCH /projects/:projectId` request schema, when the extension accepts more fields. */
  readonly updateSchema?: AnyZodObject;
  /** Reads the extension's fields from a validated request; undefined when there are none. */
  forUpdate(
    req: AuthenticatedUserRequest,
  ): Promise<ProjectUpdateExtension | undefined>;
}

export interface ProjectExtension {
  /** Undefined: project updates have only the shared settings. */
  settings(container: Container): ProjectSettingsExtension | undefined;
}

/** Users and teams leaving the org. */
export interface PrincipalLifecycleExtension {
  /** Before the user is marked deleted, so a failure leaves the delete retryable. Returns fields for the delete response. */
  userDeleted(
    orgId: string,
    userId: string,
    context: { actorUserId: string; requestId?: string; logger: Logger },
  ): Promise<Record<string, unknown>>;
  /** After the team is deleted upstream. */
  teamDeleted(orgId: string, teamId: string): Promise<void>;
}

export interface AgentExtension {
  /** An agent was created, changed or removed: drop anything cached about it. */
  changed(identity: CallerIdentity, agentKey: string | undefined): void;
}

/** What the chat and agent routers mount besides their own routes. */
export interface ConversationRouteExtension {
  /** Routes mounted before the router's own (so `/:conversationId` does not shadow them). */
  mountFirst(
    router: Router,
    container: Container,
    kind: 'chat' | 'agent',
  ): void;
  /** Routes mounted after the router's own. */
  mountLast(router: Router, container: Container, kind: 'chat' | 'agent'): void;
  /** Middleware of the routes that start a conversation, before the guard. */
  firstSend(container: Container): RequestHandler[];
  /** Middleware of `/share` and `/unshare`, after the authorize guard. */
  share(container: Container): RequestHandler[];
  /** The write behind `/share` and `/unshare`; undefined, the default read-only sharing. */
  sharing(container: Container): IConversationSharing | undefined;
  /** Request body fields a route accepts besides its own. */
  requestFields(
    kind: 'firstSend' | 'followUp' | 'agentCreate' | 'agentUpdate',
  ): ZodRawShape;
  /** Routes of the notification router mounted before its own. */
  mountNotificationRoutes(router: Router, container: Container): void;
  /** Replaces the agent create handler; undefined keeps the default. */
  createAgent(
    container: Container,
    appConfig: AppConfig,
  ): RequestHandler | undefined;
}

/**
 * The conversation module's extension points, one object per area. The defaults are the
 * single-owner behaviour; `useConversationExtensions` replaces whole areas at startup.
 */
export interface ConversationExtensions {
  turns: TurnExtension;
  views: ConversationViewExtension;
  lists: ConversationListExtension;
  archives: ConversationArchiveExtension;
  details: ConversationDetailExtension;
  history: HistoryExtension;
  events: ConversationEventsExtension;
  acl: ConversationAclExtension;
  content: IChatContentAccess;
  agents: AgentExtension;
  routes: ConversationRouteExtension;
  projects: ProjectExtension;
  lifecycle: PrincipalLifecycleExtension;
}

const defaults = (): ConversationExtensions => ({
  turns: defaultTurnExtension,
  views: {
    displayTitle: (title) => title,
    viewMessage: (message) => message,
  },
  lists: {
    select: () => '-__v',
    accessViews: () => Promise.resolve(new Map()),
    rowDecorator: () => Promise.resolve((row) => row),
    redactRecipients: (_ctx, row) => row,
    archivedStateFilter: () => ({
      isArchived: true,
      archivedBy: { $exists: true },
    }),
    ownArchivedClause: () => undefined,
  },
  archives: {
    projection: undefined,
    decide: (_ctx, session) => ({ archived: session.isArchived === true }),
  },
  details: { decorate: (_req, response) => Promise.resolve(response) },
  history: { toolResult: () => undefined },
  events: {
    turnCompleted: () => Promise.resolve(),
    conversationDeleted: () => Promise.resolve(),
  },
  acl: { versionBump: () => ({}), bumpDocument: () => undefined },
  content: new RecordPermissionChatContentAccess(),
  agents: { changed: () => undefined },
  routes: {
    mountFirst: () => undefined,
    mountLast: () => undefined,
    firstSend: () => [],
    share: () => [],
    sharing: () => undefined,
    requestFields: () => ({}),
    mountNotificationRoutes: () => undefined,
    createAgent: () => undefined,
  },
  projects: { settings: () => undefined },
  lifecycle: {
    userDeleted: () => Promise.resolve({}),
    teamDeleted: () => Promise.resolve(),
  },
});

let current: ConversationExtensions | undefined;

export function conversationExtensions(): ConversationExtensions {
  current ??= defaults();
  return current;
}

/** Replaces the given areas; `undefined` restores every default. */
export function useConversationExtensions(
  next: Partial<ConversationExtensions> | undefined,
): void {
  current = next === undefined ? defaults() : { ...defaults(), ...next };
}

/** For a message list. */
export const viewMessages = <T extends object>(
  messages: readonly T[],
  viewerId?: string,
): T[] =>
  messages.map((m) => conversationExtensions().views.viewMessage(m, viewerId));
