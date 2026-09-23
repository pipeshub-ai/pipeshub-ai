import { Document, Schema, Types, Model } from 'mongoose';
import mongoose from 'mongoose';
import { jurisdictions } from '../../../libs/utils/juridiction.utils';

import { Address } from '../../../libs/utils/address.utils';
import { generateUniqueSlug } from '../../../libs/utils/counter';
import {
  assertServiceAccountRole,
  SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
} from '../constants/service-account.constants';

export const userRoles = ['admin', 'member'] as const;
export type UserRole = (typeof userRoles)[number];

/**
 * What sort of principal this record represents. `human` is someone who signs
 * in; `service` is a machine identity that automation authenticates as and
 * that can never sign in interactively. Everything that already existed is a
 * human, which is why that is the default and why no migration is needed.
 */
export const userKinds = ['human', 'service'] as const;
export type UserKind = (typeof userKinds)[number];

export interface User extends Document, Address {
  slug?: string;
  orgId: Types.ObjectId;
  fullName?: string;
  firstName?: string;
  lastName?: string;
  middleName?: string;
  email: string;
  mobile?: string;
  hasLoggedIn?: boolean;
  designation?: string;
  kind?: UserKind;
  /** Free text explaining what a service account is for. Unused for humans. */
  description?: string;
  /**
   * Suspends the account without deleting it: tokens stop working and no
   * session can be issued, but the record, its group memberships and its
   * permission-graph node all survive so it can be switched back on.
   */
  isDisabled?: boolean;
  /** Org privilege: admin | member (replaces membership in type=admin UserGroup) */
  role?: UserRole;
  address?: Address;
  isDeleted?: boolean;
  deletedBy?: string;
}

const userSchema = new Schema<User>(
  {
    slug: { type: String, unique: true },
    orgId: { type: Schema.Types.ObjectId, ref: 'orgs', required: true },
    fullName: { type: String, trim: true },
    firstName: { type: String, trim: true },
    lastName: { type: String, trim: true },
    middleName: { type: String, trim: true },
    email: {
      type: String,
      required: [true, 'Email required'],
      lowercase: true,
      unique: true,
    },
    mobile: { type: String },
    hasLoggedIn: { type: Boolean, default: false },
    designation: { type: String, trim: true },
    kind: {
      type: String,
      enum: userKinds,
      default: 'human',
      index: true,
    },
    description: { type: String, trim: true },
    isDisabled: { type: Boolean, default: false },
    role: {
      type: String,
      enum: userRoles,
      default: 'member',
    },
    address: {
      type: {
        addressLine1: { type: String },
        city: { type: String },
        state: { type: String },
        postCode: { type: String },
        country: { type: String, enum: Object.values(jurisdictions) },
      },
    },
    isDeleted: { type: Boolean, default: false },
    deletedBy: { type: String },
  },
  { timestamps: true },
);

userSchema.pre<User>('save', async function (next) {
  try {
    if (!this.slug) {
      this.slug = await generateUniqueSlug('User');
    }
    assertServiceAccountRole(this.kind, this.role);
    next();
  } catch (error) {
    next(error as Error);
  }
});

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
  this: mongoose.Query<unknown, User>,
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

userSchema.pre('findOneAndUpdate', refuseAdminRoleOnServiceAccount);
userSchema.pre('updateOne', refuseAdminRoleOnServiceAccount);
userSchema.pre('updateMany', refuseAdminRoleOnServiceAccount);

export const Users: Model<User> =
  (mongoose.models['users'] as Model<User>) ||
  mongoose.model<User>('users', userSchema, 'users');
