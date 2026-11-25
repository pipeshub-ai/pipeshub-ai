import { Model, Schema } from 'mongoose';
import type { MemoryEntry } from '../../../../../modules/agent-harness/domain/memory';
import { harnessModel } from '../../schema/harness-model.schema';

/** `_id` is `<orgId>:<memoryId>`: a proposal's id is its idempotency key. */
export type MemoryDocument = MemoryEntry & { _id: string };

export const MEMORIES_BY_OWNER_INDEX = 'org_owner_created';
export const MEMORIES_BY_AGENT_INDEX = 'org_agent_created';
export const MEMORIES_BY_SESSION_INDEX = 'org_session';
export const MEMORIES_BY_RUN_INDEX = 'org_run';
export const PENDING_MEMORIES_INDEX = 'pending_created';
export const SUPERSEDED_MEMORIES_INDEX = 'org_superseded_by';

const memorySchema = new Schema<MemoryDocument>(
  {
    _id: { type: String, required: true },
    memoryId: { type: String, required: true },
    orgId: { type: String, required: true },
    scope: { type: String, required: true },
    ownerId: { type: String, required: true },
    agentKey: { type: String, default: null },
    sessionId: { type: String, default: null },
    runId: { type: String, default: null },
    origin: { type: String, required: true },
    kind: { type: String, required: true },
    content: { type: String, required: true },
    status: { type: String, required: true },
    label: { type: Schema.Types.Mixed, default: null },
    createdAt: { type: Number, required: true },
    updatedAt: { type: Number, required: true },
    reviewedBy: { type: String, default: null },
    reviewedAt: { type: Number, default: null },
    replaces: { type: String, default: null },
    supersededBy: { type: String, default: null },
  },
  { collection: 'agentMemories', versionKey: false, autoIndex: false },
);
// A user's list, their recall candidates and user erasure.
memorySchema.index(
  { orgId: 1, ownerId: 1, createdAt: -1 },
  { name: MEMORIES_BY_OWNER_INDEX },
);
// An agent's recall candidates and the admins' review list.
memorySchema.index(
  { orgId: 1, agentKey: 1, createdAt: -1 },
  { name: MEMORIES_BY_AGENT_INDEX },
);
// Session erasure.
memorySchema.index(
  { orgId: 1, sessionId: 1 },
  { name: MEMORIES_BY_SESSION_INDEX },
);
// A run's proposals: the per-run cap and settlement at its end.
memorySchema.index({ orgId: 1, runId: 1 }, { name: MEMORIES_BY_RUN_INDEX });
// The sweeper's backstop: proposals a run's ending did not settle.
memorySchema.index(
  { createdAt: 1 },
  {
    name: PENDING_MEMORIES_INDEX,
    partialFilterExpression: { status: 'pending' },
  },
);

// A deleted or erased correction takes the memories it superseded with it (FIX-R2 C2).
memorySchema.index(
  { orgId: 1, supersededBy: 1 },
  {
    name: SUPERSEDED_MEMORIES_INDEX,
    partialFilterExpression: { supersededBy: { $type: 'string' } },
  },
);

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const AgentMemoryModel = model('AgentHarnessMemory', memorySchema);
