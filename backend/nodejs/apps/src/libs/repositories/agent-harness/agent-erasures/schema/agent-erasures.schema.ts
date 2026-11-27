import { Model, Schema } from 'mongoose';
import { harnessModel } from '../../schema/harness-model.schema';
import type { AgentErasureDocument } from '../document';

export type { AgentErasureDocument } from '../document';

// No secondary index: an entry lives from an agent delete until its erasure is
// done (at once, unless the delete was interrupted or the erasure failed) or
// withdrawn (an hour), so the due query scans a handful of documents (and
// needs no migration).
const agentErasureSchema = new Schema<AgentErasureDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    agentKey: { type: String, required: true },
    confirmed: { type: Boolean },
    failures: { type: Number, required: true },
    requestedAt: { type: Number, required: true },
    nextAttemptAt: { type: Number, required: true },
    lastErrorType: { type: String, default: null },
  },
  {
    collection: 'agentErasures',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);

export const AgentErasureModel: Model<AgentErasureDocument> =
  harnessModel<AgentErasureDocument>('AgentErasure', agentErasureSchema);
