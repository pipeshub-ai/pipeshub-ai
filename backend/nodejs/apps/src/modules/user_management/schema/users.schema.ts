import { Document, Schema, Types, Model } from 'mongoose';
import mongoose from 'mongoose';
import { jurisdictions } from '../../../libs/utils/juridiction.utils';

import { Address } from '../../../libs/utils/address.utils';
import { generateUniqueSlug } from '../../../libs/repositories/slug/mongo';
import {
  applyServiceAccountRules,
  serviceAccountFields,
  UserKind,
} from './service-account.rules';

export {
  applyServiceAccountRules,
  serviceAccountFields,
  userKinds,
} from './service-account.rules';
export type {
  ServiceAccountRuleFields,
  UserKind,
} from './service-account.rules';

export const userRoles = ['admin', 'member'] as const;
export type UserRole = (typeof userRoles)[number];

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
  /**
   * Set while a deleted service account is being brought back, and cleared
   * when that finishes either way. It exists so the step that undoes a failed
   * restore can tell its own attempt from a later one: without it, a restore
   * that failed slowly could mark an account deleted that somebody had since
   * deleted, recreated and restored again.
   */
  restoreOpId?: string;
  /** Org privilege: admin | member (replaces membership in type=admin UserGroup) */
  role?: UserRole;
  address?: Address;
  isDeleted?: boolean;
  deletedBy?: string;
}

export const userSchema = new Schema<User>(
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
    ...serviceAccountFields,
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

userSchema.pre<User>('save', async function () {
  // Mongoose 9: async hooks signal failure by throwing, with no next callback.
  if (!this.slug) {
    this.slug = await generateUniqueSlug('User');
  }
});

applyServiceAccountRules(userSchema);

export const Users: Model<User> =
  (mongoose.models['users'] as Model<User>) ||
  mongoose.model<User>('users', userSchema, 'users');
