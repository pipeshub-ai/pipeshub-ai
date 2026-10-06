import { FilterQuery } from 'mongoose';
import { InternalServerError } from '../../../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { AccessPath } from '../../../../authz/domain/types';
import { IProjectDocument } from '../../../../projects/types/project.interfaces';
import { ScopedSession } from '../../../../authz/ports';
import { IChatSession } from '../../../types/conversation.interfaces';
import { AccessView, Caller, GrantedRole } from '../domain/types';
import { LeaseHandle, LeaseLostError } from '../leases/lease.types';

export interface ConversationAccessGrant {
  readonly session: ScopedSession;
  readonly role: GrantedRole;
  readonly via: readonly AccessPath[];
  readonly caller: Caller;
  readonly view: AccessView;
}

export interface ConversationRequestContext {
  /** Per-conversation routes. */
  readonly grant?: ConversationAccessGrant;
  readonly caller: Caller;
  /** List routes: always `{ $and: [clause] }`; compose with `$and`, never a spread. */
  readonly listFilter?: FilterQuery<IChatSession>;
  /** List routes with `sharedWithMeList`: chats shared to the caller directly or through a team. */
  readonly sharedListFilter?: FilterQuery<IChatSession>;
  /** List routes: projects whose shared chats the caller can see. */
  readonly accessibleProjectIds?: readonly string[];
  /** The collaborative-chats flag as the guard read it. */
  readonly collab?: boolean;
  /** The conversation's project, loaded by `runLease()` when the conversation has one. */
  readonly project?: IProjectDocument;
  /**
   * Set by `runLease()` with the flag on: acquired, not heartbeating. The turn handler takes it with
   * `claimLease(req)`, starts the heartbeat, and settles it through `TurnLifecycle`. A lease nobody
   * claims is released when the response closes.
   */
  readonly lease?: LeaseHandle;
}

type LeaseState = 'held' | 'claimed' | 'released';
const leaseStates = new WeakMap<LeaseHandle, LeaseState>();

const contexts = new WeakMap<object, ConversationRequestContext>();

/** Throws when no guard ran, so a route that forgot its guard fails closed. */
export function conversationContextOf(
  req: AuthenticatedUserRequest,
): ConversationRequestContext {
  const ctx = contexts.get(req);
  if (!ctx) {
    throw new InternalServerError('conversation guard not mounted');
  }
  return ctx;
}

/** The flag as the guard read it; false when no guard ran, which keeps a handler mounted without one on the legacy path. */
export function collabEnabledFor(req: object): boolean {
  return contexts.get(req)?.collab === true;
}

export function setConversationContext(
  req: AuthenticatedUserRequest,
  ctx: ConversationRequestContext,
): void {
  contexts.set(req, ctx);
}

export function listFilterOf(
  req: AuthenticatedUserRequest,
): FilterQuery<IChatSession> {
  const { listFilter } = conversationContextOf(req);
  if (!listFilter) {
    throw new InternalServerError('list guard not mounted');
  }
  return listFilter;
}

/** For per-conversation handlers; a list or `caller()` route has no grant and fails closed. */
export function conversationGrantOf(
  req: AuthenticatedUserRequest,
): ConversationAccessGrant {
  const { grant } = conversationContextOf(req);
  if (!grant) {
    throw new InternalServerError('conversation guard not mounted');
  }
  return grant;
}

export function sharedListFilterOf(
  req: AuthenticatedUserRequest,
): FilterQuery<IChatSession> {
  const { sharedListFilter } = conversationContextOf(req);
  if (!sharedListFilter) {
    throw new InternalServerError('shared list guard not mounted');
  }
  return sharedListFilter;
}

/** The grant's access view with the flag on; undefined with it off, so responses keep the legacy `access` shape. */
export function accessViewOf(
  req: AuthenticatedUserRequest,
): AccessView | undefined {
  const ctx = conversationContextOf(req);
  return ctx.collab === true ? ctx.grant?.view : undefined;
}

/** Marks the lease as `held` until a handler claims it. */
export function trackLease(lease: LeaseHandle): void {
  leaseStates.set(lease, 'held');
}

/**
 * The turn handler takes ownership of the context's lease and becomes responsible for settling it.
 * Throws `LeaseLostError` when the response already closed and the lease was released unclaimed.
 */
export function claimLease(req: AuthenticatedUserRequest): LeaseHandle {
  const { lease } = conversationContextOf(req);
  if (!lease) {
    throw new InternalServerError('run lease not acquired');
  }
  if (leaseStates.get(lease) === 'released') {
    throw new LeaseLostError(lease.runId);
  }
  leaseStates.set(lease, 'claimed');
  return lease;
}

/** True when the lease was still unclaimed, which makes the caller responsible for releasing it. */
export function takeUnclaimedLease(lease: LeaseHandle): boolean {
  if (leaseStates.get(lease) !== 'held') {
    return false;
  }
  leaseStates.set(lease, 'released');
  return true;
}
