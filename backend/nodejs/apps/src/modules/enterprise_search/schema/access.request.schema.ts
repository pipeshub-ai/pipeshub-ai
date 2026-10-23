import mongoose, { Document, Schema } from 'mongoose';

export const ACCESS_REQUEST_STATUSES = [
  'pending',
  'approved',
  'denied',
  'forwarded',
  // Internal: a request the server accepted without a record behind it (see createAccessRequest).
  // Never listed, never decided, and shown to the requester as `pending`.
  'void',
] as const;
export type AccessRequestStatus = (typeof ACCESS_REQUEST_STATUSES)[number];

/**
 * A viewer's request for a record redacted from a shared chat or search.
 * native: an owner approves in PipesHub (`pending` -> `approved`/`denied`). A record
 * of a collection (knowledge base) is reachable only through the collection, so the
 * request is addressed to the collection's owners (`collectionId`) and approving makes
 * the requester a reader of the whole collection.
 * connector: nothing is ever granted here; the record's owner is told and the
 * viewer is sent to the source (`forwarded`), the grant arrives via permission sync.
 */
export interface IAccessRequest extends Document {
  orgId: mongoose.Types.ObjectId;
  requesterId: mongoose.Types.ObjectId;
  recordId: string;
  recordName?: string;
  kind: 'native' | 'connector';
  /** Collection records: the collection this request is for. */
  collectionId?: string;
  collectionName?: string;
  /** Who may decide: the collection's owners, or the record's owner. */
  ownerUserIds: mongoose.Types.ObjectId[];
  status: AccessRequestStatus;
  decidedBy?: mongoose.Types.ObjectId;
  decidedAt?: Date;
  createdAt: Date;
}

const accessRequestSchema = new Schema<IAccessRequest>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true },
    requesterId: { type: Schema.Types.ObjectId, required: true },
    recordId: { type: String, required: true },
    recordName: { type: String },
    kind: { type: String, enum: ['native', 'connector'], required: true },
    collectionId: { type: String },
    collectionName: { type: String },
    ownerUserIds: [{ type: Schema.Types.ObjectId }],
    status: { type: String, enum: ACCESS_REQUEST_STATUSES, default: 'pending' },
    decidedBy: { type: Schema.Types.ObjectId },
    decidedAt: { type: Date },
  },
  { timestamps: true },
);

// Rate limit and duplicate lookups; owner inbox.
accessRequestSchema.index({ orgId: 1, requesterId: 1, createdAt: -1 });
accessRequestSchema.index({ orgId: 1, ownerUserIds: 1, status: 1 });

export const AccessRequest = mongoose.model<IAccessRequest>(
  'accessRequest',
  accessRequestSchema,
);
