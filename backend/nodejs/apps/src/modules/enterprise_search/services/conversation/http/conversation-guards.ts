import { RequestHandler } from 'express';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { ConversationOperation, ConversationRef } from '../domain/types';

export type GuardKind = ConversationRef['kind'];
/** `'any'` is a list over both kinds. */
export type ListKind = GuardKind | 'any';

export type GuardMarkValue =
  | { readonly op: ConversationOperation; readonly kind: GuardKind }
  | { readonly lease: true; readonly kind: GuardKind }
  | { readonly list: true; readonly kind: ListKind }
  | { readonly caller: true };

/** Lets the route-table test read what a layer guards from `router.stack`. */
export const GUARD_MARK = Symbol.for('conversation.guard');

export function guardMarkOf(handler: unknown): GuardMarkValue | undefined {
  return (handler as { [GUARD_MARK]?: GuardMarkValue })[GUARD_MARK];
}

export const markGuard = <T extends RequestHandler>(
  handler: T,
  value: GuardMarkValue,
): T => Object.defineProperty(handler, GUARD_MARK, { value });

/** `extended` is whether the guard's policy is extended, for surfaces whose default output must not change. */
export type PerRequest =
  | boolean
  | ((req: AuthenticatedUserRequest, extended: boolean) => boolean);

/** `'exclude'` hides what the caller archived for themself; `'only'` lists it. */
export type ArchiveScope = 'exclude' | 'only';

export interface ListScopeOptions {
  includeOwned?: PerRequest;
  includeShared?: PerRequest;
  /** Whether chats shared to the caller's projects are listed. */
  includeProjects?: PerRequest;
  /** `'extendedOnly'` lists `sharedWith` rows only under an extended policy; `'never'` lists owned and project chats only. */
  shareRows?: 'always' | 'extendedOnly' | 'never';
  /** Per-user archive scope; the default policy archives conversation-wide (`isArchived`) and ignores it. */
  archived?: ArchiveScope;
  /** Also builds `sharedListFilter`: chats shared to the caller by `sharedWith` rows only. */
  sharedWithMeList?: boolean;
  /**
   * The kind listed when only conversations shared with the caller are listed; default the guard's
   * own. A recipient finds a shared agent chat in the shared list without first finding the agent.
   */
  sharedKind?: ListKind;
}

export const resolveOption = (
  option: PerRequest | undefined,
  req: AuthenticatedUserRequest,
  extended: boolean,
): boolean =>
  option === undefined
    ? true
    : typeof option === 'boolean'
      ? option
      : option(req, extended);

export type LeaseOperation = Extract<
  ConversationOperation,
  'send' | 'regenerate'
>;

export interface RunLeaseOptions {
  /** `regenerate` adds no message. Default `send`. */
  op?: LeaseOperation;
}

/**
 * Route middleware that authorizes a conversation request and stores a `ConversationRequestContext`
 * the handlers read. Errors go to `next(err)`, before any SSE header.
 */
export interface IConversationGuards {
  /** A per-conversation route: the caller must be allowed `op` on `:conversationId`. */
  authorize(op: ConversationOperation, kind: GuardKind): RequestHandler;
  /** Turn preconditions after `authorize('send'|'regenerate')`, before any SSE header. */
  runLease(kind: GuardKind, options?: RunLeaseOptions): RequestHandler;
  /** A list route: stores the filter of the conversations the caller may see. */
  listScope(kind: ListKind, options?: ListScopeOptions): RequestHandler;
  /** A route that creates a conversation: stores the caller. */
  caller(): RequestHandler;
}
