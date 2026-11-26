import { Model, Schema } from 'mongoose';
import { harnessModel } from '../../schema/harness-model.schema';

/**
 * One row per stored engine sandbox snapshot, written with the
 * `engine.snapshotted` event that names it. `_id` is `orgId:objectId`.
 */
export interface SandboxSnapshotDocument {
  _id: string;
  orgId: string;
  sessionId: string;
  runId: string | null;
  objectId: string;
  takenAt: Date;
  region: string;
}

export const SNAPSHOTS_BY_AGE_INDEX = 'org_taken';
export const SNAPSHOTS_BY_SESSION_INDEX = 'org_session';

const sandboxSnapshotSchema = new Schema<SandboxSnapshotDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    runId: { type: String, default: null },
    objectId: { type: String, required: true },
    takenAt: { type: Date, required: true },
    region: { type: String, required: true },
  },
  {
    collection: 'agentSandboxSnapshots',
    versionKey: false,
    autoIndex: false,
  },
);
sandboxSnapshotSchema.index(
  { orgId: 1, takenAt: 1 },
  { name: SNAPSHOTS_BY_AGE_INDEX },
);
sandboxSnapshotSchema.index(
  { orgId: 1, sessionId: 1 },
  { name: SNAPSHOTS_BY_SESSION_INDEX },
);

export const AgentSandboxSnapshotModel: Model<SandboxSnapshotDocument> =
  harnessModel<SandboxSnapshotDocument>(
    'AgentSandboxSnapshot',
    sandboxSnapshotSchema,
  );
