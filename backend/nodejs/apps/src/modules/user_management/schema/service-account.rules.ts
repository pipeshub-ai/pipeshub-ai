import mongoose, { Document, Schema } from 'mongoose';
import {
  assertReservedEmailDomainBelongsToServiceAccount,
  assertServiceAccountRole,
  isServiceAccountEmail,
  reservedEmailDomainPattern,
  SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
  SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
} from '../constants/service-account.constants';
import type { UserRole } from './users.schema';

// Kept apart from users.schema so a module can apply them without registering the users model.

/**
 * What sort of principal this record represents. `human` is someone who signs
 * in; `service` is a machine identity that automation authenticates as and
 * that can never sign in interactively. Everything that already existed is a
 * human, which is why that is the default and why no migration is needed.
 */
export const userKinds = ['human', 'service'] as const;
export type UserKind = (typeof userKinds)[number];

/** What the service-account rules read from a user record. */
export interface ServiceAccountRuleFields {
  kind?: UserKind;
  role?: UserRole;
  email: string;
}

/** The service-account fields of a users schema. */
export const serviceAccountFields = {
  kind: {
    type: String,
    enum: userKinds,
    default: 'human',
    index: true,
  },
  description: { type: String, trim: true },
  isDisabled: { type: Boolean, default: false },
  restoreOpId: { type: String },
};
/**
 * The same rule for updates that do not load the document first.
 *
 * `findOneAndUpdate`, `updateOne` and `updateMany` bypass the save hook, and
 * the role-update endpoint and invite processor both reach users that way.
 * When the update does not itself set `kind`, the stored record has to be
 * consulted: promoting an existing service account is precisely the case
 * worth catching.
 */
async function refuseAdminRoleOnServiceAccount(
  this: mongoose.Query<unknown, ServiceAccountRuleFields>,
): Promise<void> {
  const update = this.getUpdate() as Record<string, unknown> | null;
  if (update === null) return;

  // Both shapes have to be read, not one or the other. `timestamps: true`
  // means Mongoose adds its own `$set` for `updatedAt`, so an update written
  // as `{ role: 'admin' }` arrives here as
  // `{ role: 'admin', $set: { updatedAt } }` — the field is at the top level
  // while `$set` exists but holds something else entirely.
  const set = (update.$set ?? {}) as Record<string, unknown>;
  const role = set.role ?? update.role;
  if (role !== 'admin') return;

  const kind = set.kind ?? update.kind;
  if (kind === 'service') {
    throw new Error(SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE);
  }
  if (kind !== undefined) return;

  // Ask whether the update reaches *any* service account, rather than
  // sampling one document and reading its kind. `updateMany` is the reason:
  // the invite processor promotes a batch with
  // `updateMany({ _id: { $in: ids } }, { role: 'admin' })`, and a sample that
  // happened to return a person would have let every service account in that
  // batch through. Narrowing the query instead means one match is enough to
  // refuse, whichever documents the update covers.
  const offending = await this.model
    .findOne({ ...this.getQuery(), kind: 'service' })
    .select('_id')
    .lean()
    .exec();
  if (offending) {
    throw new Error(SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE);
  }
}

/**
 * Keeps the reserved domain reserved across updates as well as creates.
 *
 * Two ways an update can break the rule, and both are checked: it can move a
 * record that is not a service account onto an address in the domain, or it
 * can take `kind` away from a record that already holds one. Either leaves a
 * human on addresses the rest of the product reads as machine identities.
 *
 * The stored record is consulted only when the update alone cannot settle it,
 * so an ordinary update that touches neither field costs nothing.
 */
async function refuseReservedEmailDomainOnPeople(
  this: mongoose.Query<unknown, ServiceAccountRuleFields>,
): Promise<void> {
  const update = this.getUpdate() as Record<string, unknown> | null;
  if (update === null) return;

  // Read both shapes, for the same reason the role guard does: `timestamps`
  // adds its own `$set`, so a field written at the top level stays there.
  const set = (update.$set ?? {}) as Record<string, unknown>;
  const email = set.email ?? update.email;
  const kind = set.kind ?? update.kind;

  if (typeof email === 'string' && isServiceAccountEmail(email)) {
    if (kind === 'service') return;
    if (kind !== undefined) {
      throw new Error(SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE);
    }
    // Refuse if the update reaches anything that is not a service account.
    // Asked as "is there an offender" rather than by sampling, because
    // `updateMany` can cover a mix.
    const offender = await this.model
      .findOne({ ...this.getQuery(), kind: { $ne: 'service' } })
      .select('_id')
      .lean()
      .exec();
    if (offender) {
      throw new Error(SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE);
    }
    return;
  }

  // Downgrading `kind` on a record that already holds a reserved address.
  //
  // Unless the same update also replaces the address with one outside the
  // domain, which leaves a record that breaks no rule — and is exactly how
  // someone would repair a row that should never have held one, so refusing it
  // would close the only door out.
  const replacesWithOrdinaryAddress =
    typeof email === 'string' && !isServiceAccountEmail(email);
  if (
    kind !== undefined &&
    kind !== 'service' &&
    !replacesWithOrdinaryAddress
  ) {
    const offender = await this.model
      .findOne({ ...this.getQuery(), email: reservedEmailDomainPattern() })
      .select('_id')
      .lean()
      .exec();
    if (offender) {
      throw new Error(SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE);
    }
  }
}

/** What a users schema holds a service account to; any schema for the collection applies them. */
export function applyServiceAccountRules<T extends ServiceAccountRuleFields>(
  schema: Schema<T>,
): void {
  schema.pre<Document & ServiceAccountRuleFields>('save', function () {
    assertServiceAccountRole(this.kind, this.role);
    // Only when this save is what puts the address there. Judging the
    // document's current state instead would refuse every later write to a
    // person who already holds a reserved address — and one can, because
    // nothing stopped it before this rule existed. `deleteUser` pulls group
    // memberships, revokes OAuth apps and knowledge-base permissions, removes
    // project access and unsets the password before it saves `isDeleted`, so a
    // throw at that save would leave the account stripped of everything and
    // still active, with no way back: changing an address is owner-only, and
    // deleting is the documented repair for a bad invite.
    if (this.isNew || this.isModified('email') || this.isModified('kind')) {
      assertReservedEmailDomainBelongsToServiceAccount(this.kind, this.email);
    }
  });
  schema.pre('findOneAndUpdate', refuseAdminRoleOnServiceAccount);
  schema.pre('updateOne', refuseAdminRoleOnServiceAccount);
  schema.pre('updateMany', refuseAdminRoleOnServiceAccount);
  schema.pre('findOneAndUpdate', refuseReservedEmailDomainOnPeople);
  schema.pre('updateOne', refuseReservedEmailDomainOnPeople);
  schema.pre('updateMany', refuseReservedEmailDomainOnPeople);
}
