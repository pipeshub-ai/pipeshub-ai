import { Model, Schema } from 'mongoose';
import {
  ORG_ERASURE_STEPS,
  USER_ERASURE_STEPS,
} from '../../../../../modules/agent-harness/domain/principal-erasure';
import { harnessModel } from '../../schema/harness-model.schema';
import type { PrincipalErasureDocument } from '../document';

export type { PrincipalErasureDocument } from '../document';

const principalErasureSchema = new Schema<PrincipalErasureDocument>(
  {
    _id: { type: String, required: true },
    kind: { type: String, enum: ['org', 'user'], required: true },
    orgId: { type: String, required: true },
    userId: { type: String, default: null },
    by: { type: String, required: true },
    step: {
      type: String,
      enum: [...ORG_ERASURE_STEPS, ...USER_ERASURE_STEPS],
      required: true,
    },
    requestedAt: { type: Number, required: true },
    auditRetentionDays: { type: Number, required: true },
    auditRemoveAfter: { type: Number, default: null },
    cursor: { type: String, default: null },
    transfers: { type: Schema.Types.Mixed, default: [] },
    journaled: { type: Number, default: 0 },
    counts: { type: Schema.Types.Mixed, default: {} },
    extension: { type: Schema.Types.Mixed, default: null },
    failures: { type: Number, default: 0 },
    nextAttemptAt: { type: Number, required: true },
    lastErrorType: { type: String, default: null },
  },
  {
    collection: 'agentPrincipalErasures',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);

export const AgentPrincipalErasureModel: Model<PrincipalErasureDocument> =
  harnessModel<PrincipalErasureDocument>(
    'AgentPrincipalErasure',
    principalErasureSchema,
  );
