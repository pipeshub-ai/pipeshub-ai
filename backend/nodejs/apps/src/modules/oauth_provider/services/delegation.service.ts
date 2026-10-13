import { injectable, inject } from 'inversify';
import { Types } from 'mongoose';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { Users } from '../../user_management/schema/users.schema';
import {
  ServiceAccountDelegation,
  IServiceAccountDelegation,
} from '../schema/service-account-delegation.schema';
import {
  DelegationEvent,
  DelegationOutcome,
} from '../schema/delegation-event.schema';

/** Mongo's duplicate-key code, which is what the unique index raises. */
const DUPLICATE_KEY = 11000;

function isDuplicateKeyError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { code?: unknown }).code === DUPLICATE_KEY
  );
}

/**
 * The most people one service account may be allowed to act as.
 *
 * Not a technical limit — Mongo would hold far more. It is the feature's
 * intent written down: delegation is meant to be a named, reviewable list, and
 * an account approaching this number has stopped being a list and become a
 * master key. Hitting the cap should prompt the question "should this
 * integration hold its own access instead", which is a different and usually
 * better answer.
 */
export const MAX_DELEGATIONS_PER_SERVICE_ACCOUNT = 200;

/** How many audit records the delegation log returns in one page. */
export const DELEGATION_EVENT_PAGE_SIZE = 100;
export const DELEGATION_EVENT_MAX_PAGE_SIZE = 500;

export interface DelegationListItem {
  id: string;
  serviceAccountId: string;
  subjectUserId: string;
  subjectEmail?: string;
  subjectFullName?: string;
  /** True when the person can no longer be acted as, with `reason` saying why. */
  inactive: boolean;
  inactiveReason?: string;
  grantedBy: string;
  reason?: string;
  createdAt: Date;
}

export interface DelegationEventListItem {
  id: string;
  serviceAccountId: string;
  serviceAccountEmail?: string;
  subjectUserId?: string;
  subjectEmail?: string;
  requestedSubject: string;
  outcome: DelegationOutcome;
  refusedReason?: string;
  /** The administrator behind a `granted` or `revoked` record. */
  actedBy?: string;
  actedByEmail?: string;
  /** The reason given when the permission was granted. */
  note?: string;
  scopes: string[];
  jti?: string;
  expiresAt?: Date;
  createdAt: Date;
}

/**
 * Resolved details of a person a service account wants to act as, together
 * with the verdict on whether it may.
 *
 * A union rather than one shape with optional fields, so that a caller who
 * has checked `ok` is handed a `userId` the compiler knows is there. The
 * alternative asks every call site to assert it, which is exactly the place a
 * refusal could be read as an approval.
 */
export type SubjectResolution =
  | { ok: true; userId: string; email: string; fullName?: string }
  | {
      ok: false;
      /** Machine-readable cause, written to the audit log. */
      refusedReason: SubjectRefusalReason;
      userId?: string;
      email?: string;
      fullName?: string;
    };

export type SubjectRefusalReason =
  | 'subject_missing'
  | 'subject_not_found'
  | 'subject_is_service_account'
  | 'subject_is_admin'
  | 'subject_disabled';

/**
 * Administers the allow-list that makes delegation safe, and reads the log
 * that makes it accountable.
 *
 * Deliberately separate from the exchange itself. This half is a CRUD surface
 * an administrator drives from a browser; the other half runs unattended on
 * the token endpoint. Keeping them apart means the rules this class enforces
 * at grant time can be re-asserted independently at exchange time, which is
 * not duplication — a person's role, and whether they still work here, can
 * change between the two.
 */
@injectable()
export class DelegationService {
  constructor(@inject('Logger') private logger: Logger) {}

  /**
   * Confirms the target is a service account in this organisation.
   *
   * `kind` is part of the query rather than checked afterwards, so an
   * administrator cannot build an allow-list against a colleague's user id and
   * turn a person into a delegating principal.
   */
  private async requireServiceAccount(
    orgId: string,
    serviceAccountId: string,
  ): Promise<{ id: string; email: string }> {
    if (!Types.ObjectId.isValid(serviceAccountId)) {
      throw new NotFoundError('Service account not found');
    }
    const account = await Users.findOne({
      _id: { $eq: serviceAccountId },
      orgId: { $eq: orgId },
      kind: 'service',
      isDeleted: false,
    })
      .select('email')
      .lean()
      .exec();
    if (!account) {
      throw new NotFoundError('Service account not found');
    }
    return { id: serviceAccountId, email: account.email };
  }

  /**
   * Resolves who a request wants to act as and decides whether that is
   * allowed, by id or by email address.
   *
   * Returns a verdict rather than throwing, because the caller on the token
   * endpoint has to write an audit record for the refusal before answering,
   * and because every refusal must look identical from outside: a service
   * account probing for valid addresses should not be able to tell "no such
   * person" from "not on your list". The distinction is kept here, in the log.
   */
  async resolveSubject(
    orgId: string,
    requestedSubject: unknown,
  ): Promise<SubjectResolution> {
    // `unknown` rather than `string` because this is reachable from the token
    // endpoint, where the value is whatever was in the request body. The
    // validators refuse a non-string, but a request that somehow carried one
    // should be refused here rather than crash on `.trim()` and surface as a
    // server error.
    if (typeof requestedSubject !== 'string') {
      return { ok: false, refusedReason: 'subject_missing' };
    }
    const trimmed = requestedSubject.trim();
    if (trimmed === '') {
      return { ok: false, refusedReason: 'subject_missing' };
    }

    // Each value is wrapped in `$eq` so it can only ever be compared as a
    // value. Mongo reads a bare object as a set of operators, so an input that
    // arrived as `{ $ne: null }` rather than a string would otherwise become a
    // query that matches somebody. The route validators already refuse that,
    // but a query should not depend on a check three frames up to be safe —
    // and this one is reachable from the token endpoint, where the subject is
    // whatever the caller typed. Matches `oauth_token.service.ts:200`.
    const query = Types.ObjectId.isValid(trimmed)
      ? { _id: { $eq: trimmed }, orgId: { $eq: orgId } }
      : { email: { $eq: trimmed.toLowerCase() }, orgId: { $eq: orgId } };

    const user = await Users.findOne(query)
      .select('email fullName role kind isDeleted isDisabled')
      .lean()
      .exec();

    if (!user || user.isDeleted === true) {
      return { ok: false, refusedReason: 'subject_not_found' };
    }

    const resolved = {
      userId: String(user._id),
      email: user.email,
      fullName: user.fullName,
    };

    // A service account acting as another service account chains machine
    // identities together, so the question "whose access is this" stops having
    // an answer a person can give.
    if (user.kind === 'service') {
      return {
        ...resolved,
        ok: false,
        refusedReason: 'subject_is_service_account',
      };
    }

    // Version one refuses to delegate to an administrator, and this is the
    // load-bearing rule of the whole feature. Service accounts are clamped to
    // `member` precisely so no machine credential carries admin rights; a
    // delegated admin token would walk around that clamp by construction,
    // since the issued token is an ordinary user token and every check
    // downstream would correctly treat it as the administrator it names.
    if (user.role === 'admin') {
      return { ...resolved, ok: false, refusedReason: 'subject_is_admin' };
    }

    if (user.isDisabled === true) {
      return { ...resolved, ok: false, refusedReason: 'subject_disabled' };
    }

    return { ...resolved, ok: true };
  }

  /** Whether this service account is currently allowed to act as this person. */
  async isDelegationAllowed(
    orgId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<boolean> {
    const row = await ServiceAccountDelegation.findOne({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
      subjectUserId: { $eq: subjectUserId },
    })
      .select('_id')
      .lean()
      .exec();
    return row !== null;
  }

  /** Grant a service account the right to act as one named person. */
  async grant(
    orgId: string,
    actingUserId: string,
    serviceAccountId: string,
    requestedSubject: unknown,
    reason?: string,
  ): Promise<DelegationListItem> {
    const account = await this.requireServiceAccount(orgId, serviceAccountId);

    const subject = await this.resolveSubject(orgId, requestedSubject);
    if (!subject.ok) {
      throw new BadRequestError(
        this.grantRefusalMessage(subject.refusedReason),
      );
    }

    const existing = await ServiceAccountDelegation.countDocuments({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
    });
    if (existing >= MAX_DELEGATIONS_PER_SERVICE_ACCOUNT) {
      throw new BadRequestError(
        `This service account may already act as ${String(MAX_DELEGATIONS_PER_SERVICE_ACCOUNT)} people, which is the limit. ` +
          'An integration that needs more than this should hold its own access instead of borrowing everyone’s.',
      );
    }

    let row: IServiceAccountDelegation;
    try {
      row = await ServiceAccountDelegation.create({
        orgId: new Types.ObjectId(orgId),
        serviceAccountId: new Types.ObjectId(serviceAccountId),
        subjectUserId: new Types.ObjectId(subject.userId),
        grantedBy: new Types.ObjectId(actingUserId),
        reason,
      });
    } catch (error) {
      // Only a duplicate is forgiven, and only because the unique index is
      // what raised it: granting a right the account already holds is not
      // worth failing a script over. Anything else — a dropped connection, a
      // validation failure — has to surface. Catching it here and then
      // finding a row written by some earlier request would report a grant
      // that this call did not make, and skip the audit record for it.
      if (!isDuplicateKeyError(error)) {
        throw error;
      }
      const already = await ServiceAccountDelegation.findOne({
        orgId: { $eq: orgId },
        serviceAccountId: { $eq: serviceAccountId },
        subjectUserId: { $eq: subject.userId },
      }).exec();
      if (already) {
        return this.toListItem(already, subject);
      }
      throw error;
    }

    // Written before the method returns so the log holds the grant even if
    // nothing ever uses it. This is the record that survives revocation.
    await this.recordEvent({
      orgId,
      serviceAccountId: account.id,
      subjectUserId: subject.userId,
      requestedSubject: subject.email,
      outcome: 'granted',
      actedBy: actingUserId,
      note: reason,
    });

    this.logger.info('Delegation granted', {
      orgId,
      serviceAccountId: account.id,
      subjectUserId: subject.userId,
      grantedBy: actingUserId,
    });

    return this.toListItem(row, subject);
  }

  private grantRefusalMessage(refusedReason: SubjectRefusalReason): string {
    switch (refusedReason) {
      case 'subject_is_admin':
        return (
          'A service account cannot be allowed to act as an administrator. ' +
          'Service accounts are held to member rights, and delegating to an ' +
          'admin would hand a machine credential the admin rights that rule exists to prevent.'
        );
      case 'subject_is_service_account':
        return 'A service account cannot act as another service account.';
      case 'subject_disabled':
        return 'That account is disabled.';
      case 'subject_missing':
        return 'Name the person this service account may act as.';
      case 'subject_not_found':
        return 'No such person in this organisation.';
    }
  }

  private toListItem(
    row: IServiceAccountDelegation,
    subject: Pick<SubjectResolution, 'email' | 'fullName'>,
  ): DelegationListItem {
    return {
      id: String(row._id),
      serviceAccountId: String(row.serviceAccountId),
      subjectUserId: String(row.subjectUserId),
      subjectEmail: subject.email,
      subjectFullName: subject.fullName,
      inactive: false,
      grantedBy: String(row.grantedBy),
      reason: row.reason,
      createdAt: row.createdAt,
    };
  }

  /**
   * Everyone this service account may act as.
   *
   * Rows whose person has since been disabled, deleted or promoted to
   * administrator are returned and marked `inactive` rather than hidden. The
   * exchange already refuses them, so hiding them would leave an administrator
   * reviewing a list that quietly disagreed with what the system does.
   */
  async listDelegations(
    orgId: string,
    serviceAccountId: string,
  ): Promise<DelegationListItem[]> {
    await this.requireServiceAccount(orgId, serviceAccountId);

    const rows = await ServiceAccountDelegation.find({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
    })
      .sort({ createdAt: -1 })
      .lean()
      .exec();
    if (rows.length === 0) {
      return [];
    }

    const subjects = await Users.find({
      _id: { $in: rows.map((r) => r.subjectUserId) },
    })
      .select('email fullName role kind isDeleted isDisabled')
      .lean()
      .exec();
    const byId = new Map(subjects.map((s) => [String(s._id), s]));

    return rows.map((row) => {
      const subject = byId.get(String(row.subjectUserId));
      let inactiveReason: string | undefined;
      if (!subject || subject.isDeleted === true) {
        inactiveReason = 'This person no longer has an account';
      } else if (subject.role === 'admin') {
        inactiveReason = 'This person is now an administrator';
      } else if (subject.isDisabled === true) {
        inactiveReason = 'This person’s account is disabled';
      }

      return {
        id: String(row._id),
        serviceAccountId: String(row.serviceAccountId),
        subjectUserId: String(row.subjectUserId),
        subjectEmail: subject?.email,
        subjectFullName: subject?.fullName,
        inactive: inactiveReason !== undefined,
        inactiveReason,
        grantedBy: String(row.grantedBy),
        reason: row.reason,
        createdAt: row.createdAt,
      };
    });
  }

  /**
   * Withdraw one delegation.
   *
   * Tokens already issued under it are not revoked here, and do not need to
   * be: the auth middleware reads this row on every request a delegated token
   * makes, so deleting it stops those tokens at once. Revoking them instead
   * would also be a race the deletion could lose — an exchange already past
   * its allow-list read can insert a token after any sweep has run.
   */
  async revoke(
    orgId: string,
    actingUserId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<void> {
    await this.requireServiceAccount(orgId, serviceAccountId);
    if (!Types.ObjectId.isValid(subjectUserId)) {
      throw new NotFoundError('Delegation not found');
    }

    const removed = await ServiceAccountDelegation.findOneAndDelete({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
      subjectUserId: { $eq: subjectUserId },
    }).exec();
    if (!removed) {
      throw new NotFoundError('Delegation not found');
    }

    await this.recordEvent({
      orgId,
      serviceAccountId,
      subjectUserId,
      requestedSubject: subjectUserId,
      outcome: 'revoked',
      actedBy: actingUserId,
    });

    this.logger.info('Delegation revoked', {
      orgId,
      serviceAccountId,
      subjectUserId,
      revokedBy: actingUserId,
    });
  }

  /**
   * Remove every delegation a service account holds.
   *
   * Called when the account is deleted, and again if it is later restored,
   * mirroring token revocation: restoring reuses the original record, so
   * without this an allow-list built for one purpose would come back to life
   * under whoever reused the name.
   */
  async revokeAllForServiceAccount(
    orgId: string,
    serviceAccountId: string,
  ): Promise<void> {
    const result = await ServiceAccountDelegation.deleteMany({
      orgId: { $eq: orgId },
      serviceAccountId: { $eq: serviceAccountId },
    }).exec();
    if (result.deletedCount > 0) {
      this.logger.info('Removed every delegation held by a service account', {
        orgId,
        serviceAccountId,
        removed: result.deletedCount,
      });
    }
  }

  /** Record one exchange attempt, successful or refused. */
  async recordEvent(event: {
    orgId: string;
    serviceAccountId: string;
    subjectUserId?: string;
    requestedSubject: string;
    outcome: DelegationOutcome;
    refusedReason?: string;
    actedBy?: string;
    note?: string;
    scopes?: string[];
    accessTokenId?: string;
    jti?: string;
    expiresAt?: Date;
  }): Promise<void> {
    try {
      await DelegationEvent.create({
        orgId: new Types.ObjectId(event.orgId),
        serviceAccountId: new Types.ObjectId(event.serviceAccountId),
        subjectUserId:
          event.subjectUserId === undefined
            ? undefined
            : new Types.ObjectId(event.subjectUserId),
        requestedSubject: event.requestedSubject.slice(0, 320),
        outcome: event.outcome,
        refusedReason: event.refusedReason,
        actedBy:
          event.actedBy === undefined
            ? undefined
            : new Types.ObjectId(event.actedBy),
        note: event.note,
        scopes: event.scopes ?? [],
        accessTokenId:
          event.accessTokenId === undefined
            ? undefined
            : new Types.ObjectId(event.accessTokenId),
        jti: event.jti,
        expiresAt: event.expiresAt,
      });
    } catch (error) {
      // An audit write must never be the reason a request fails, but it is
      // also the thing this feature promises, so a failure is logged loudly
      // rather than swallowed.
      this.logger.error('Failed to write a delegation audit record', {
        orgId: event.orgId,
        serviceAccountId: event.serviceAccountId,
        outcome: event.outcome,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  /** The delegation log, newest first, for the whole org or one account. */
  async listEvents(
    orgId: string,
    options: { serviceAccountId?: string; limit?: number } = {},
  ): Promise<DelegationEventListItem[]> {
    const limit = Math.min(
      Math.max(options.limit ?? DELEGATION_EVENT_PAGE_SIZE, 1),
      DELEGATION_EVENT_MAX_PAGE_SIZE,
    );

    const query: Record<string, unknown> = { orgId: { $eq: orgId } };
    if (options.serviceAccountId !== undefined) {
      if (!Types.ObjectId.isValid(options.serviceAccountId)) {
        throw new NotFoundError('Service account not found');
      }
      await this.requireServiceAccount(orgId, options.serviceAccountId);
      query.serviceAccountId = { $eq: options.serviceAccountId };
    }

    const events = await DelegationEvent.find(query)
      .sort({ createdAt: -1 })
      .limit(limit)
      .lean()
      .exec();
    if (events.length === 0) {
      return [];
    }

    const userIds = new Set<string>();
    for (const event of events) {
      userIds.add(String(event.serviceAccountId));
      if (event.subjectUserId) {
        userIds.add(String(event.subjectUserId));
      }
      if (event.actedBy) {
        userIds.add(String(event.actedBy));
      }
    }
    const users = await Users.find({ _id: { $in: [...userIds] } })
      .select('email')
      .lean()
      .exec();
    const emailById = new Map(users.map((u) => [String(u._id), u.email]));

    return events.map((event) => ({
      id: String(event._id),
      serviceAccountId: String(event.serviceAccountId),
      serviceAccountEmail: emailById.get(String(event.serviceAccountId)),
      subjectUserId: event.subjectUserId
        ? String(event.subjectUserId)
        : undefined,
      subjectEmail: event.subjectUserId
        ? emailById.get(String(event.subjectUserId))
        : undefined,
      requestedSubject: event.requestedSubject,
      outcome: event.outcome,
      refusedReason: event.refusedReason,
      actedBy: event.actedBy ? String(event.actedBy) : undefined,
      actedByEmail: event.actedBy
        ? emailById.get(String(event.actedBy))
        : undefined,
      note: event.note,
      scopes: event.scopes,
      jti: event.jti,
      expiresAt: event.expiresAt,
      createdAt: event.createdAt,
    }));
  }
}
