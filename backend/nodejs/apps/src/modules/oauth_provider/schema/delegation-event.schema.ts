import mongoose, { Document, Schema, Model, Types } from 'mongoose';

/**
 * How long a delegation record is kept.
 *
 * Delegation without a log is the genuinely dangerous combination: a
 * credential that can be anyone, with no record of who it was. So these rows
 * are the point of the feature as much as the token is, and they are kept well
 * past the longest service-token lifetime (365 days) — long enough that the
 * credential which produced a record has expired before the record has, and
 * an annual review can still see the full year it is reviewing.
 */
export const DELEGATION_EVENT_RETENTION_DAYS = 400;

/**
 * What happened.
 *
 * Two kinds of record share this log on purpose. `issued` and `refused` are
 * the exchange: refusals are kept as deliberately as successes, because a
 * service account repeatedly asking for someone it may not act as is the
 * clearest signal a credential has been taken.
 *
 * `granted` and `revoked` are the allow-list itself. Without them the log
 * could say that an exchange was permitted but never why — the row that
 * permitted it is deleted on revocation, taking the administrator who granted
 * it and their reason with it. "Who allowed this, and when" is the first
 * question anyone reviewing a delegation asks, and it should still have an
 * answer after the permission is withdrawn.
 */
export type DelegationOutcome = 'issued' | 'refused' | 'granted' | 'revoked';

export interface IDelegationEvent extends Document {
  orgId: Types.ObjectId;
  /** The service account the record is about. */
  serviceAccountId: Types.ObjectId;
  /** Who it asked to act as. Absent when the request named nobody resolvable. */
  subjectUserId?: Types.ObjectId;
  /**
   * What it asked for, verbatim, even when that resolved to nobody — and even
   * when it asked for nobody at all.
   */
  requestedSubject: string;
  outcome: DelegationOutcome;
  /** Machine-readable refusal cause, e.g. `not_on_allow_list`. */
  refusedReason?: string;
  /** The administrator who granted or withdrew the permission. */
  actedBy?: Types.ObjectId;
  /** The reason they gave, on a `granted` record. */
  note?: string;
  /** Scopes on the issued token. Empty on a refusal. */
  scopes: string[];
  /** The issued token's id, so a reader can tie a record to a revocation. */
  accessTokenId?: Types.ObjectId;
  /** The issued token's `jti`, which is what appears in downstream logs. */
  jti?: string;
  expiresAt?: Date;
  createdAt: Date;
  updatedAt: Date;
}

const DelegationEventSchema = new Schema<IDelegationEvent>(
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
    subjectUserId: { type: Schema.Types.ObjectId, ref: 'users', index: true },
    // Deliberately not `required`. Mongoose counts an empty string as absent,
    // and an empty string is a real thing to record here: a refusal can be
    // written before the request is known to name anyone, because whether the
    // service account itself is still usable is decided first. Requiring a
    // value would throw inside the audit write, which `recordEvent` swallows,
    // so the only visible effect would be a missing record — the one case
    // where the log is most worth having.
    requestedSubject: { type: String, maxlength: 320 },
    outcome: {
      type: String,
      enum: ['issued', 'refused', 'granted', 'revoked'],
      required: true,
      index: true,
    },
    refusedReason: { type: String, maxlength: 120 },
    actedBy: { type: Schema.Types.ObjectId, ref: 'users' },
    note: { type: String, maxlength: 280 },
    scopes: { type: [String], default: [] },
    accessTokenId: { type: Schema.Types.ObjectId, ref: 'oauthAccessToken' },
    jti: { type: String, index: true },
    expiresAt: { type: Date },
  },
  { timestamps: true },
);

// The reading order of the audit view: one organisation, newest first.
DelegationEventSchema.index({ orgId: 1, createdAt: -1 });
DelegationEventSchema.index({ orgId: 1, serviceAccountId: 1, createdAt: -1 });

// Expiry is on createdAt rather than on `expiresAt`, which is the token's
// lifetime and only a few minutes long — indexing the TTL there would delete
// the record almost as soon as it was written.
DelegationEventSchema.index(
  { createdAt: 1 },
  { expireAfterSeconds: DELEGATION_EVENT_RETENTION_DAYS * 24 * 60 * 60 },
);

export const DelegationEvent: Model<IDelegationEvent> =
  mongoose.model<IDelegationEvent>(
    'delegationEvent',
    DelegationEventSchema,
    'delegationEvents',
  );
