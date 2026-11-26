import { Model, Schema } from 'mongoose';
import type { ForkResult } from '../../../../../modules/agent-harness/domain/session-forks';
import { harnessModel } from '../../schema/harness-model.schema';

export const SESSION_FORKS_BY_SOURCE_INDEX = 'org_source_session';
export const SESSION_FORKS_BY_FORK_INDEX = 'org_fork_session';

/**
 * One "continue privately" request (SS3), `_id = forkRecordId(key)`: the new
 * session's id chosen on the first ask, and the fork's result once its
 * transaction committed. Ids and counts only; erased with either session.
 */
export interface SessionForkDocument {
  _id: string;
  orgId: string;
  sourceSessionId: string;
  actorId: string;
  forkSessionId: string;
  result: ForkResult | null;
  createdAt: number;
  completedAt: number | null;
}

const sessionForkSchema = new Schema<SessionForkDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    sourceSessionId: { type: String, required: true },
    actorId: { type: String, required: true },
    forkSessionId: { type: String, required: true },
    result: { type: Schema.Types.Mixed, default: null },
    createdAt: { type: Number, required: true },
    completedAt: { type: Number, default: null },
  },
  {
    collection: 'agentSessionForks',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
sessionForkSchema.index(
  { orgId: 1, sourceSessionId: 1 },
  { name: SESSION_FORKS_BY_SOURCE_INDEX },
);
sessionForkSchema.index(
  { orgId: 1, forkSessionId: 1 },
  { name: SESSION_FORKS_BY_FORK_INDEX },
);

export const AgentSessionForkModel: Model<SessionForkDocument> =
  harnessModel<SessionForkDocument>('AgentSessionFork', sessionForkSchema);
