import mongoose, { Document, Schema, Model, Types } from 'mongoose';

/**
 * One entry on a service account's allow-list: permission for that account to
 * act as one named person.
 *
 * The allow-list is the whole security story of delegation. A service account
 * that may act as anyone is a master key, so nothing is implied here — an
 * administrator names each person, one row at a time, and an account with no
 * rows can act as nobody.
 *
 * `subjectUserId` is deliberately a single user rather than a group. Group
 * membership changes without anyone revisiting the delegation, which is how an
 * allow-list silently widens; naming people keeps the grant and the review in
 * the same place. A future `subjectKind` discriminator can add groups without
 * disturbing rows written now.
 */
export interface IServiceAccountDelegation extends Document {
  orgId: Types.ObjectId;
  /** The service account being granted the right to act as someone. */
  serviceAccountId: Types.ObjectId;
  /** The person it may act as. Never a service account, never an admin. */
  subjectUserId: Types.ObjectId;
  /** The administrator who granted it, kept for the review conversation. */
  grantedBy: Types.ObjectId;
  /** Why the grant exists. Optional, but the field a reviewer reads first. */
  reason?: string;
  createdAt: Date;
  updatedAt: Date;
}

const ServiceAccountDelegationSchema = new Schema<IServiceAccountDelegation>(
  {
    orgId: {
      type: Schema.Types.ObjectId,
      ref: 'org',
      required: true,
      index: true,
    },
    serviceAccountId: {
      type: Schema.Types.ObjectId,
      ref: 'users',
      required: true,
      index: true,
    },
    subjectUserId: {
      type: Schema.Types.ObjectId,
      ref: 'users',
      required: true,
      index: true,
    },
    grantedBy: {
      type: Schema.Types.ObjectId,
      ref: 'users',
      required: true,
    },
    reason: { type: String, trim: true, maxlength: 280 },
  },
  { timestamps: true },
);

/**
 * Rows are removed rather than soft-deleted, and this index is why that is the
 * safe choice: revoking a delegation has to mean the pair cannot be granted
 * again by accident *and* cannot linger in a state some query forgets to
 * filter. A uniqueness rule over a soft-delete flag is the usual source of
 * both bugs. The grant and the revocation are each written to the delegation
 * log first, so withdrawing a permission does not take the record of it — who
 * granted it, to whom, and why — along with the row.
 */
ServiceAccountDelegationSchema.index(
  { orgId: 1, serviceAccountId: 1, subjectUserId: 1 },
  { unique: true },
);

export const ServiceAccountDelegation: Model<IServiceAccountDelegation> =
  mongoose.model<IServiceAccountDelegation>(
    'serviceAccountDelegation',
    ServiceAccountDelegationSchema,
    'serviceAccountDelegations',
  );
