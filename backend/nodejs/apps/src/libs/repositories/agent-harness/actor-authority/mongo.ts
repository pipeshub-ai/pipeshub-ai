import mongoose from 'mongoose';
import { authJwtGenerator } from '../../../utils/createJwt';
import { ACTOR_CREDENTIAL_TTL } from '../../../../modules/agent-harness/domain/schedules';
import type {
  ActorAuthority,
  ActorRefusal,
} from '../../../../modules/agent-harness/ports/schedules';
import { Users } from '../../../../modules/user_management/schema/users.schema';
import {
  isUserOrgAdmin,
  normalizeUserRole,
} from '../../../../modules/user_management/services/user-admin.service';
import { findActiveOrgById } from '../../../../modules/user_management/utils/org.utils';

export interface ActorRecord {
  orgId?: unknown;
  email?: string;
  fullName?: string;
  role?: string;
  kind?: string;
  isDisabled?: boolean;
  isDeleted?: boolean;
}

/** The account behind a schedule's actor; the org it belongs to is checked against the record after. */
export type ActorReader = (
  orgId: string,
  actorId: string,
) => Promise<ActorRecord | null>;

export const mongoActorReader: ActorReader = (_orgId, actorId) =>
  Users.findOne({ _id: actorId })
    .select('orgId email fullName role kind isDisabled isDeleted')
    .lean<ActorRecord>()
    .exec();

/**
 * The user a schedule runs as, read at each firing (P8a): the same checks
 * `generateAuthToken` makes before any sign-in (not deleted, not disabled, a
 * person, an active org), read from the database, never from the schedule.
 * The credential it mints is the user's own, lives five minutes, and is only
 * presented to the agent authority, as an interactive run presents theirs.
 */
export class HarnessActorAuthority implements ActorAuthority {
  constructor(
    private readonly jwtSecret: () => string,
    private readonly readActor: ActorReader = mongoActorReader,
  ) {}

  async authorize(
    orgId: string,
    actorId: string,
  ): Promise<{ authorization: string } | { refused: ActorRefusal }> {
    if (
      !mongoose.isValidObjectId(actorId) ||
      !mongoose.isValidObjectId(orgId)
    ) {
      return { refused: 'actor_deleted' };
    }
    const user = await this.readActor(orgId, actorId);
    if (
      user === null ||
      user.isDeleted === true ||
      String(user.orgId) !== orgId
    ) {
      return { refused: 'actor_deleted' };
    }
    if (user.kind === 'service') return { refused: 'actor_not_person' };
    if (user.isDisabled === true) return { refused: 'actor_disabled' };
    const org = await findActiveOrgById(orgId);
    if (org === null) return { refused: 'org_inactive' };
    const token = authJwtGenerator(
      this.jwtSecret(),
      user.email ?? null,
      actorId,
      orgId,
      user.fullName ?? null,
      org.accountType,
      normalizeUserRole(user.role) ?? 'member',
      ACTOR_CREDENTIAL_TTL,
    );
    return { authorization: `Bearer ${token}` };
  }

  isOrgAdmin(orgId: string, userId: string): Promise<boolean> {
    return isUserOrgAdmin(userId, orgId);
  }
}
