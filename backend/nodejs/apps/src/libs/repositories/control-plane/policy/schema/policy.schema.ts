import { Model, Schema } from 'mongoose';
import { guardedModel } from '../../../id-filter-guard/mongo';

/**
 * Policy layers per org and scope (`cpPolicyLayers`, 02-lld §4.2). The layer
 * is kept as its JSON text: approval rules key tools by ids with dots, which
 * Mongo field names do not take safely. `body` null is a deleted layer.
 */
export interface PolicyLayerDocument {
  /** `{orgId}:{scope}`. */
  _id: string;
  orgId: string;
  scope: string;
  kind: string;
  revision: number;
  body: string | null;
  updatedAt: Date;
}

/** One row per committed revision of a scope (`cpPolicyHistory`), append-only. */
export interface PolicyHistoryDocument {
  /** `{orgId}:{scope}:{revision}`. */
  _id: string;
  orgId: string;
  scope: string;
  revision: number;
  body: string | null;
  at: Date;
}

/** One org a data migration has applied (`cpMigrationLedger`), what its revert reads. */
export interface MigrationLedgerDocument {
  /** `{migrationId}:{orgId}`. */
  _id: string;
  migrationId: string;
  orgId: string;
  appliedAt: Date;
  entry: string;
}

export const POLICY_LAYERS_ORG_INDEX = 'cp_policy_layers_org_kind';
export const POLICY_HISTORY_SCOPE_INDEX = 'cp_policy_history_scope';

const layerSchema = new Schema<PolicyLayerDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    scope: { type: String, required: true },
    kind: { type: String, required: true },
    revision: { type: Number, required: true },
    body: { type: String, default: null },
    updatedAt: { type: Date, required: true },
  },
  { collection: 'cpPolicyLayers', versionKey: false, autoIndex: false },
);
// An org's layers of one kind (its agents' layers for the admin views).
layerSchema.index({ orgId: 1, kind: 1 }, { name: POLICY_LAYERS_ORG_INDEX });

const historySchema = new Schema<PolicyHistoryDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    scope: { type: String, required: true },
    revision: { type: Number, required: true },
    body: { type: String, default: null },
    at: { type: Date, required: true },
  },
  { collection: 'cpPolicyHistory', versionKey: false, autoIndex: false },
);
// `history(org, scope, limit)`: newest first.
historySchema.index(
  { orgId: 1, scope: 1, revision: -1 },
  { name: POLICY_HISTORY_SCOPE_INDEX },
);

const ledgerSchema = new Schema<MigrationLedgerDocument>(
  {
    _id: { type: String, required: true },
    migrationId: { type: String, required: true },
    orgId: { type: String, required: true },
    appliedAt: { type: Date, required: true },
    entry: { type: String, required: true },
  },
  { collection: 'cpMigrationLedger', versionKey: false, autoIndex: false },
);

const modelOf = <T>(name: string, schema: Schema<T>): Model<T> =>
  guardedModel<T>(name, schema);

export const PolicyLayerModel: Model<PolicyLayerDocument> = modelOf(
  'ControlPlanePolicyLayer',
  layerSchema,
);
export const PolicyHistoryModel: Model<PolicyHistoryDocument> = modelOf(
  'ControlPlanePolicyHistory',
  historySchema,
);
export const MigrationLedgerModel: Model<MigrationLedgerDocument> = modelOf(
  'ControlPlaneMigrationLedger',
  ledgerSchema,
);
