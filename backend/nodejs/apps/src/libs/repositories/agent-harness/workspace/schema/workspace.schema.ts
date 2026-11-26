import { Model, Schema } from 'mongoose';
import { SealedContent } from '../../../../../modules/agent-harness/domain/data-protection';
import { CommitReason } from '../../../../../modules/agent-harness/domain/workspace';
import { harnessModel } from '../../schema/harness-model.schema';

export interface WorkspaceManifestDocument {
  orgId: string;
  workspaceId: string;
  revision: number;
  parentRevision: number | null;
  contentHash: string;
  fileCount: number;
  /** The file list, sealed with the org key (`workspace.manifest`); never queried. */
  files: SealedContent;
  reason: CommitReason | 'fork' | 'upload';
  runId: string;
  createdAt: number;
}

export interface WorkspaceKeyDocument {
  orgId: string;
  sessionId: string;
  keyId: string;
  /** The session key wrapped with the org key (`workspace.key`); null once shredded. */
  wrapped: SealedContent | null;
  createdAt: number;
  destroyedAt: number | null;
}

const sealedSchema = new Schema<SealedContent>(
  {
    v: { type: Number, required: true },
    kid: { type: String, required: true },
    kv: { type: Number, required: true },
    iv: { type: String, required: true },
    tag: { type: String, required: true },
    ct: { type: String, required: true },
  },
  { _id: false },
);

const manifestSchema = new Schema<WorkspaceManifestDocument>(
  {
    orgId: { type: String, required: true },
    workspaceId: { type: String, required: true },
    revision: { type: Number, required: true, min: 1 },
    parentRevision: { type: Number, default: null },
    contentHash: { type: String, required: true },
    fileCount: { type: Number, required: true, min: 0 },
    files: { type: sealedSchema, required: true },
    reason: { type: String, required: true },
    runId: { type: String, required: true },
    createdAt: { type: Number, required: true },
  },
  {
    collection: 'agentWorkspaceManifests',
    versionKey: false,
    autoIndex: false,
  },
);
manifestSchema.index(
  { orgId: 1, workspaceId: 1, revision: 1 },
  { unique: true, name: 'org_workspace_revision' },
);

const keySchema = new Schema<WorkspaceKeyDocument>(
  {
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    keyId: { type: String, required: true },
    wrapped: { type: sealedSchema, default: null },
    createdAt: { type: Number, required: true },
    destroyedAt: { type: Number, default: null },
  },
  { collection: 'agentWorkspaceKeys', versionKey: false, autoIndex: false },
);
keySchema.index(
  { orgId: 1, sessionId: 1 },
  { unique: true, name: 'org_session' },
);

/** P6.4: one workspace's write lease; `sessionId` lets erasure find it. */
export interface WorkspaceWriterDocument {
  orgId: string;
  sessionId: string;
  workspaceId: string;
  runId: string;
  expiresAt: number;
  updatedAt: number;
}

const writerSchema = new Schema<WorkspaceWriterDocument>(
  {
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    workspaceId: { type: String, required: true },
    runId: { type: String, required: true },
    expiresAt: { type: Number, required: true },
    updatedAt: { type: Number, required: true },
  },
  { collection: 'agentWorkspaceWriters', versionKey: false, autoIndex: false },
);
writerSchema.index(
  { orgId: 1, workspaceId: 1 },
  { unique: true, name: 'org_workspace' },
);

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const AgentWorkspaceManifestModel = model(
  'AgentWorkspaceManifest',
  manifestSchema,
);
export const AgentWorkspaceKeyModel = model('AgentWorkspaceKey', keySchema);
export const AgentWorkspaceWriterModel = model(
  'AgentWorkspaceWriter',
  writerSchema,
);
