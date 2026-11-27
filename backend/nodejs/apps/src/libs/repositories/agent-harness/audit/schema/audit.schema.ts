import { Model, Schema } from 'mongoose';
import { AuditRecord } from '../../../../../modules/agent-harness/domain/audit';
import {
  AUDIT_HEADS,
  AUDIT_PENDING,
  AUDIT_PENDING_ORDER_INDEX,
  AUDIT_RECORDS,
  AuditPendingDocument,
} from '../../audit-linking/mongo';
import { harnessModel } from '../../schema/harness-model.schema';

type AuditRecordDocument = AuditRecord;

interface AuditHeadDocument {
  orgId: string;
  seq: number;
  hash: string;
  anchoredSeq: number;
}

interface AuditAnchorDocument {
  _id: string;
  orgId: string;
  seq: number;
  hash: string;
  anchoredAt: string;
}

const auditRecordSchema = new Schema<AuditRecordDocument>(
  {
    orgId: { type: String, required: true },
    seq: { type: Number, required: true },
    prevHash: { type: String, required: true },
    hash: { type: String, required: true },
    ts: { type: String, required: true },
    kind: { type: String, required: true },
    sessionId: { type: String, default: null },
    streamId: { type: String, required: true },
    runId: { type: String, default: null },
    journalSeq: { type: Number, required: true },
    writer: { type: String, required: true },
    attemptId: { type: String, default: null },
    fence: { type: Number, default: null },
    fields: { type: Schema.Types.Mixed, required: true },
    dataDigest: { type: String, required: true },
  },
  {
    collection: AUDIT_RECORDS,
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
auditRecordSchema.index({ orgId: 1, seq: 1 }, { unique: true });
// AUDITUI: an admin page reads one kind of an org's records, newest first.
auditRecordSchema.index({ orgId: 1, kind: 1, seq: -1 });
// The audit search (J-18): a person's or an agent's runs' records, newest first.
export const AUDIT_RECORDS_BY_RUN_INDEX = 'orgId_1_runId_1_seq_-1';
export const AUDIT_RECORDS_OF_RUNS = { runId: { $type: 'string' } } as const;
auditRecordSchema.index(
  { orgId: 1, runId: 1, seq: -1 },
  {
    name: AUDIT_RECORDS_BY_RUN_INDEX,
    partialFilterExpression: AUDIT_RECORDS_OF_RUNS,
  },
);

const auditHeadSchema = new Schema<AuditHeadDocument>(
  {
    orgId: { type: String, required: true },
    seq: { type: Number, required: true },
    hash: { type: String, required: true },
    anchoredSeq: { type: Number, required: true },
  },
  { collection: AUDIT_HEADS, versionKey: false, autoIndex: false },
);
auditHeadSchema.index({ orgId: 1 }, { unique: true });
auditHeadSchema.index({ anchoredSeq: 1 });

const auditPendingSchema = new Schema<Omit<AuditPendingDocument, '_id'>>(
  {
    orgId: { type: String, required: true },
    streamId: { type: String, required: true },
    journalSeq: { type: Number, required: true },
    ts: { type: String, required: true },
    entries: { type: Schema.Types.Mixed, required: true },
    digests: { type: [String], required: true },
  },
  {
    collection: AUDIT_PENDING,
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
// An org's appends stream by stream in journal order: what a link reads.
auditPendingSchema.index(
  { orgId: 1, streamId: 1, journalSeq: 1 },
  { name: AUDIT_PENDING_ORDER_INDEX },
);

// Written once per (org, seq), never updated: the local stand-in for WORM.
const auditAnchorSchema = new Schema<AuditAnchorDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    seq: { type: Number, required: true },
    hash: { type: String, required: true },
    anchoredAt: { type: String, required: true },
  },
  { collection: 'agentAuditAnchors', versionKey: false, autoIndex: false },
);
auditAnchorSchema.index({ orgId: 1, seq: 1 });

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const AgentAuditRecordModel = model(
  'AgentAuditRecord',
  auditRecordSchema,
);
export const AgentAuditPendingModel = model(
  'AgentAuditPending',
  auditPendingSchema,
);
export const AgentAuditHeadModel = model('AgentAuditHead', auditHeadSchema);
export const AgentAuditAnchorModel = model(
  'AgentAuditAnchor',
  auditAnchorSchema,
);
