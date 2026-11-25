import { Model, Schema } from 'mongoose';
import { PayloadRef } from '../../../../../modules/agent-harness/domain/journal';
import {
  VISIBILITIES,
  WRITERS,
} from '../../../../../modules/agent-harness/contracts/generated/core/enums';
import { harnessModel } from '../../schema/harness-model.schema';
import type { SessionEventDocument } from '../document';

export type { SessionEventDocument } from '../document';

interface StreamCounterDocument {
  orgId: string;
  streamId: string;
  lastSeq: number;
  ts: Date;
}

export interface JournalPayloadDocument {
  _id: string;
  orgId: string;
  hash: string;
  size: number;
  bytes: Buffer;
  createdAt: Date;
  /** T5: who wrote it; absent on payloads stored before E7. */
  owner?: { sessionId: string; runId?: string };
  /** T5: a committed event points at it; false until one does, absent before E7. */
  referenced?: boolean;
}

export const PAYLOAD_OWNER_INDEX = 'org_owner_session';
export const PAYLOAD_GC_INDEX = 'unreferenced_created';
export const SESSION_EVENTS_BY_SESSION_INDEX = 'org_session_stream_seq';

const payloadRefSchema = new Schema<PayloadRef>(
  {
    ref: { type: String, required: true },
    hash: { type: String, required: true },
    size: { type: Number, required: true, min: 0 },
  },
  { _id: false },
);

const sessionEventSchema = new Schema<SessionEventDocument>(
  {
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    streamId: { type: String, required: true },
    seq: { type: Number, required: true },
    runId: { type: String },
    attemptId: { type: String },
    fence: { type: Number },
    type: { type: String, required: true },
    schemaVersion: { type: Number, required: true },
    critical: { type: Boolean, required: true },
    ts: { type: Date, required: true },
    writer: { type: String, enum: [...WRITERS], required: true },
    labels: { type: Schema.Types.Mixed },
    data: { type: Schema.Types.Mixed },
    payloadRef: { type: payloadRefSchema },
    sealed: { type: Schema.Types.Mixed },
    visibility: {
      type: String,
      enum: [...VISIBILITIES],
      required: true,
    },
    erased: { type: Boolean },
    contentBytes: { type: Number },
  },
  {
    collection: 'sessionEvents',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
sessionEventSchema.index({ orgId: 1, streamId: 1, seq: 1 }, { unique: true });
sessionEventSchema.index({ orgId: 1, runId: 1, seq: 1 });
// Erasure finds a session's events across its root and child streams (E7).
sessionEventSchema.index(
  { orgId: 1, sessionId: 1, streamId: 1, seq: 1 },
  { name: SESSION_EVENTS_BY_SESSION_INDEX },
);

// One counter per stream, not per session, so child runs never contend on
// the root stream's document [R-3].
const streamCounterSchema = new Schema<StreamCounterDocument>(
  {
    orgId: { type: String, required: true },
    streamId: { type: String, required: true },
    lastSeq: { type: Number, required: true },
    ts: { type: Date, required: true },
  },
  { collection: 'sessionStreamCounters', versionKey: false, autoIndex: false },
);
streamCounterSchema.index({ orgId: 1, streamId: 1 }, { unique: true });

const journalPayloadSchema = new Schema<JournalPayloadDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    hash: { type: String, required: true },
    size: { type: Number, required: true },
    bytes: { type: Buffer, required: true },
    createdAt: { type: Date, required: true },
    owner: {
      type: new Schema(
        {
          sessionId: { type: String, required: true },
          runId: { type: String },
        },
        { _id: false },
      ),
      default: undefined,
    },
    referenced: { type: Boolean },
  },
  { collection: 'agentJournalPayloads', versionKey: false, autoIndex: false },
);
journalPayloadSchema.index(
  { orgId: 1, 'owner.sessionId': 1 },
  {
    name: PAYLOAD_OWNER_INDEX,
    partialFilterExpression: { owner: { $exists: true } },
  },
);
journalPayloadSchema.index(
  { createdAt: 1 },
  { name: PAYLOAD_GC_INDEX, partialFilterExpression: { referenced: false } },
);

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const SessionEventModel = model('SessionEvent', sessionEventSchema);
export const SessionStreamCounterModel = model(
  'SessionStreamCounter',
  streamCounterSchema,
);
export const AgentJournalPayloadModel = model(
  'AgentJournalPayload',
  journalPayloadSchema,
);
