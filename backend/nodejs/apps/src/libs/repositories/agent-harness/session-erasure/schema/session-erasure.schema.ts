import { Model, Schema } from 'mongoose';
import { ERASURE_STEPS } from '../../../../../modules/agent-harness/domain/session-erasure';
import { harnessModel } from '../../schema/harness-model.schema';
import type { SessionErasureDocument } from '../document';

export type { SessionErasureDocument } from '../document';

export const ERASURES_DUE_INDEX = 'pending_due';
export const ERASURES_BY_SESSION_INDEX = 'org_session';

const sessionErasureSchema = new Schema<SessionErasureDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    reason: {
      type: String,
      enum: ['user_delete', 'retention'],
      required: true,
    },
    by: { type: String, default: null },
    status: { type: String, enum: ['pending', 'completed'], required: true },
    step: { type: String, enum: [...ERASURE_STEPS, null], default: null },
    counts: { type: Schema.Types.Mixed, default: {} },
    attempt: { type: Number, required: true },
    failures: { type: Number, required: true },
    requestedAt: { type: Number, required: true },
    nextAttemptAt: { type: Number, required: true },
    completedAt: { type: Number, default: null },
    lastErrorType: { type: String, default: null },
    holder: { type: String, default: null },
    leaseExpiresAt: { type: Number, default: null },
  },
  {
    collection: 'agentSessionErasures',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
sessionErasureSchema.index(
  { status: 1, nextAttemptAt: 1 },
  { name: ERASURES_DUE_INDEX },
);
sessionErasureSchema.index(
  { orgId: 1, sessionId: 1 },
  { name: ERASURES_BY_SESSION_INDEX, unique: true },
);

export const AgentSessionErasureModel: Model<SessionErasureDocument> =
  harnessModel<SessionErasureDocument>(
    'AgentSessionErasure',
    sessionErasureSchema,
  );
