import { Model, Schema } from 'mongoose';
import { harnessModel } from '../../schema/harness-model.schema';

/** A person's Inbox settings (ADR-INBOX-01), `_id` `<orgId>:<userId>`. */
export interface InboxPreferencesDocument {
  _id: string;
  orgId: string;
  userId: string;
  /** Oldest first; at most `MAX_MUTED_SCHEDULES`. */
  mutedSchedules: string[];
  /** Epoch ms the pause ends; null when not paused or paused until resumed. */
  pausedUntil: number | null;
  pausedUntilResumed: boolean;
  runNoticeEmail: boolean;
  runNoticeSlack: boolean;
  updatedAt: number;
}

/** That a run's finished notice was decided (`_id` `<orgId>:<runId>`): the first insert wins. */
export interface RunEndNoticeClaimDocument {
  _id: string;
  orgId: string;
  runId: string;
  userId: string;
  createdAt: Date;
}

/** One open stream of a person's on a conversation (`_id` `<orgId>:<watcherId>`), renewed while it is open. */
export interface RunWatchLeaseDocument {
  _id: string;
  orgId: string;
  userId: string;
  sessionId: string;
  until: number;
  /** For the TTL index: a lease no replica renewed is removed by the server. */
  expiresAt: Date;
}

export const RUN_END_NOTICE_CLAIMS_EXPIRY_INDEX = 'createdAt_ttl';
export const RUN_WATCH_LEASES_BY_PERSON_INDEX =
  'orgId_1_userId_1_sessionId_1_until_1';
export const RUN_WATCH_LEASES_EXPIRY_INDEX = 'expiresAt_ttl';
/** A claim outlives the sweeps that could end its run a second time by far. */
export const RUN_END_NOTICE_CLAIM_TTL_SECONDS = 30 * 24 * 3600;

const options = {
  versionKey: false,
  autoIndex: false,
  minimize: false,
} as const;

const preferencesSchema = new Schema<InboxPreferencesDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    userId: { type: String, required: true },
    mutedSchedules: { type: [String], default: [] },
    pausedUntil: { type: Number, default: null },
    pausedUntilResumed: { type: Boolean, default: false },
    runNoticeEmail: { type: Boolean, default: false },
    runNoticeSlack: { type: Boolean, default: false },
    updatedAt: { type: Number, required: true },
  },
  { ...options, collection: 'agentHarnessInboxPreferences' },
);

const claimSchema = new Schema<RunEndNoticeClaimDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    runId: { type: String, required: true },
    userId: { type: String, required: true },
    createdAt: { type: Date, required: true },
  },
  { ...options, collection: 'agentHarnessRunEndNotices' },
);
claimSchema.index(
  { createdAt: 1 },
  {
    name: RUN_END_NOTICE_CLAIMS_EXPIRY_INDEX,
    expireAfterSeconds: RUN_END_NOTICE_CLAIM_TTL_SECONDS,
  },
);

const leaseSchema = new Schema<RunWatchLeaseDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    userId: { type: String, required: true },
    sessionId: { type: String, required: true },
    until: { type: Number, required: true },
    expiresAt: { type: Date, required: true },
  },
  { ...options, collection: 'agentHarnessRunWatchers' },
);
leaseSchema.index(
  { orgId: 1, userId: 1, sessionId: 1, until: 1 },
  { name: RUN_WATCH_LEASES_BY_PERSON_INDEX },
);
leaseSchema.index(
  { expiresAt: 1 },
  { name: RUN_WATCH_LEASES_EXPIRY_INDEX, expireAfterSeconds: 0 },
);

export const InboxPreferencesModel: Model<InboxPreferencesDocument> =
  harnessModel<InboxPreferencesDocument>(
    'AgentHarnessInboxPreferences',
    preferencesSchema,
  );
export const RunEndNoticeClaimModel: Model<RunEndNoticeClaimDocument> =
  harnessModel<RunEndNoticeClaimDocument>(
    'AgentHarnessRunEndNotice',
    claimSchema,
  );
export const RunWatchLeaseModel: Model<RunWatchLeaseDocument> =
  harnessModel<RunWatchLeaseDocument>('AgentHarnessRunWatcher', leaseSchema);
