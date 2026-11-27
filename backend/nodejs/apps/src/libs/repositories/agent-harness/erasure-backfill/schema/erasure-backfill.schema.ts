import { Model, Schema } from 'mongoose';
import {
  BackfillCounts,
  BackfillCursor,
  BackfillItemState,
  BackfillOrgSummary,
  BackfillPlanState,
} from '../../../../../modules/agent-harness/domain/erasure-backfill';
import { harnessModel } from '../../schema/harness-model.schema';

export interface ErasureBackfillPlanDocument {
  /** The plan's digest. */
  _id: string;
  orgFilter: string | null;
  operator: string;
  createdAt: number;
  state: BackfillPlanState;
  orgs: BackfillOrgSummary[];
  items: number;
  cursor: BackfillCursor;
  staleReason: string | null;
}

export interface ErasureBackfillItemDocument {
  /** `digest:sessionId`: one item per conversation per plan. */
  _id: string;
  digest: string;
  orgId: string;
  sessionId: string;
  batch: number;
  fingerprint: string;
  by: string | null;
  updatedAt: number;
  counts: BackfillCounts;
  state: BackfillItemState;
}

export interface ErasureBackfillMarkerDocument {
  /** The org id: one completion per org, ever. */
  _id: string;
  digest: string;
  operator: string;
  completedAt: number;
  erased: number;
  withdrawn: number;
  skipped: number;
}

export const BACKFILL_ITEMS_BY_BATCH_INDEX = 'plan_org_batch';
export const BACKFILL_ITEMS_BY_SESSION_INDEX = 'plan_session';

const options = {
  versionKey: false,
  autoIndex: false,
  minimize: false,
} as const;

const planSchema = new Schema<ErasureBackfillPlanDocument>(
  {
    _id: { type: String, required: true },
    orgFilter: { type: String, default: null },
    operator: { type: String, required: true },
    createdAt: { type: Number, required: true },
    state: {
      type: String,
      enum: ['persisting', 'ready', 'stale', 'completed'],
      required: true,
    },
    orgs: { type: Schema.Types.Mixed, required: true },
    items: { type: Number, required: true },
    cursor: {
      _id: false,
      org: { type: Number, required: true },
      batch: { type: Number, required: true },
      phase: {
        type: String,
        enum: ['next', 'intended', 'enqueued'],
        required: true,
      },
    },
    staleReason: { type: String, default: null },
  },
  { ...options, collection: 'agentErasureBackfillPlans' },
);

const itemSchema = new Schema<ErasureBackfillItemDocument>(
  {
    _id: { type: String, required: true },
    digest: { type: String, required: true },
    orgId: { type: String, required: true },
    sessionId: { type: String, required: true },
    batch: { type: Number, required: true },
    fingerprint: { type: String, required: true },
    by: { type: String, default: null },
    updatedAt: { type: Number, required: true },
    counts: { type: Schema.Types.Mixed, required: true },
    state: {
      type: String,
      enum: [
        'planned',
        'enqueued',
        'erased',
        'withdrawn',
        'skipped_absent',
        'skipped_queued',
      ],
      required: true,
    },
  },
  { ...options, collection: 'agentErasureBackfillItems' },
);
itemSchema.index(
  { digest: 1, orgId: 1, batch: 1, sessionId: 1 },
  { name: BACKFILL_ITEMS_BY_BATCH_INDEX },
);
itemSchema.index(
  { digest: 1, sessionId: 1 },
  { name: BACKFILL_ITEMS_BY_SESSION_INDEX },
);

const markerSchema = new Schema<ErasureBackfillMarkerDocument>(
  {
    _id: { type: String, required: true },
    digest: { type: String, required: true },
    operator: { type: String, required: true },
    completedAt: { type: Number, required: true },
    erased: { type: Number, required: true },
    withdrawn: { type: Number, required: true },
    skipped: { type: Number, required: true },
  },
  { ...options, collection: 'agentErasureBackfillMarkers' },
);

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const AgentErasureBackfillPlanModel = model(
  'AgentErasureBackfillPlan',
  planSchema,
);
export const AgentErasureBackfillItemModel = model(
  'AgentErasureBackfillItem',
  itemSchema,
);
export const AgentErasureBackfillMarkerModel = model(
  'AgentErasureBackfillMarker',
  markerSchema,
);
