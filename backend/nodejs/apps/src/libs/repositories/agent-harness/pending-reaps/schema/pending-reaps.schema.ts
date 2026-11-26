import { Model, Schema } from 'mongoose';
import { harnessModel } from '../../schema/harness-model.schema';

/**
 * A sandbox reap the broker refused (CLEANUP-G6), asked again until it
 * answers. `_id` is the label set's key; ids only, deleted once the reap
 * succeeds. Only refused reaps are written, so the collection stays small and
 * needs no index.
 */
export interface PendingReapDocument {
  _id: string;
  orgId: string;
  labels: Record<string, string>;
  failures: number;
  nextAttemptAt: number;
  createdAt: number;
  lastErrorType: string | null;
}

const pendingReapSchema = new Schema<PendingReapDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    labels: { type: Schema.Types.Mixed, required: true },
    failures: { type: Number, required: true },
    nextAttemptAt: { type: Number, required: true },
    createdAt: { type: Number, required: true },
    lastErrorType: { type: String, default: null },
  },
  {
    collection: 'agentSandboxReaps',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);

export const AgentSandboxReapModel: Model<PendingReapDocument> =
  harnessModel<PendingReapDocument>('AgentSandboxReap', pendingReapSchema);
