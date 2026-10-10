import mongoose, { Schema, Model, Document, Types } from 'mongoose';

export const AUDIT_RETENTION_DAYS = 400;
export const AUDIT_RETENTION_SECONDS = AUDIT_RETENTION_DAYS * 24 * 60 * 60;

export interface IAuditEvent {
  orgId: Types.ObjectId;
  actorUserId: Types.ObjectId;
  /** Dotted verb, e.g. `chat.share`, `chat.unshare`, `chat.accessChange`. */
  action: string;
  targetType: string;
  targetId: string;
  principal?: { principalType: string; principalId: string };
  before?: unknown;
  after?: unknown;
  requestId?: string;
  schemaVersion?: number;
  createdAt?: Date;
}

export interface IAuditEventDocument extends Document, IAuditEvent {}

// Append-only; no update or delete API. Generic on purpose: chats are the first
// producer, not the only one.
const auditEventSchema = new Schema<IAuditEventDocument>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true },
    actorUserId: { type: Schema.Types.ObjectId, required: true },
    action: { type: String, required: true },
    targetType: { type: String, required: true },
    targetId: { type: String, required: true },
    principal: {
      type: new Schema(
        { principalType: { type: String }, principalId: { type: String } },
        { _id: false },
      ),
    },
    before: { type: Schema.Types.Mixed },
    after: { type: Schema.Types.Mixed },
    requestId: { type: String },
    schemaVersion: { type: Number, default: 1 },
  },
  {
    timestamps: { createdAt: true, updatedAt: false },
    versionKey: false,
    collection: 'auditEvents',
  },
);

auditEventSchema.index({ orgId: 1, targetType: 1, targetId: 1, createdAt: -1 });
auditEventSchema.index({ orgId: 1, actorUserId: 1, createdAt: -1 });
auditEventSchema.index(
  { createdAt: 1 },
  { expireAfterSeconds: AUDIT_RETENTION_SECONDS },
);

export const AuditEvent: Model<IAuditEventDocument> =
  mongoose.model<IAuditEventDocument>('AuditEvent', auditEventSchema);
