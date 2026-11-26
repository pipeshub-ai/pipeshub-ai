import { Model, Schema } from 'mongoose';
import { guardedModel } from '../../../id-filter-guard/mongo';
import type { GrantEntry } from '../../../../../modules/control-plane/domain/objects';

/**
 * `cpInstallations` and `cpInstallationHistory` (02-lld §4.2). The row is the
 * installation's JSON text: a kind's `config` is free-form and may carry keys
 * Mongo field names do not take. Indexed fields are copies. `_id` is
 * `{orgId}|{id}` rather than 02-lld's bare id, because migrated ids are
 * legacy instance ids that no format makes unique across orgs.
 */
export interface InstallationDocument {
  _id: string;
  orgId: string;
  id: string;
  item: string;
  kind: string;
  owner: string;
  status: string;
  installedVia: string | null;
  revision: number;
  body: string;
}

export interface InstallationHistoryDocument {
  /** `{orgId}|{id}:{revision}`. */
  _id: string;
  orgId: string;
  id: string;
  revision: number;
  body: string;
}

export interface GrantDocument {
  /** `{orgId}:{subject}`. */
  _id: string;
  orgId: string;
  subject: string;
  entries: GrantEntry[];
  revision: number;
}

export interface LedgerDocument {
  /** `{orgId}|{object}|{id}`. */
  _id: string;
  orgId: string;
  object: 'installation' | 'grant';
  id: string;
  revision: number;
}

export const INSTALLATION_STATUS_INDEX = 'cp_installations_status';
export const INSTALLATION_OWNER_INDEX = 'cp_installations_owner';
export const INSTALLATION_PARENT_INDEX = 'cp_installations_parent';
export const INSTALLATION_ITEM_INDEX = 'cp_installations_item';
export const INSTALLATION_HISTORY_INDEX = 'cp_installation_history';
export const GRANT_INSTALLATION_INDEX = 'cp_grants_installation';
export const LEDGER_ORG_INDEX = 'cp_capability_ledger_org';

const installationSchema = new Schema<InstallationDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    id: { type: String, required: true },
    item: { type: String, required: true },
    kind: { type: String, required: true },
    owner: { type: String, required: true },
    status: { type: String, required: true },
    installedVia: { type: String, default: null },
    revision: { type: Number, required: true },
    body: { type: String, required: true },
  },
  {
    collection: 'cpInstallations',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
// The snapshot loader's `listForOrg` by status and kind.
installationSchema.index(
  { orgId: 1, status: 1, kind: 1 },
  { name: INSTALLATION_STATUS_INDEX },
);
installationSchema.index(
  { orgId: 1, owner: 1 },
  { name: INSTALLATION_OWNER_INDEX },
);
// Plugin cascade; only contributed installations carry a parent.
installationSchema.index(
  { orgId: 1, installedVia: 1 },
  {
    name: INSTALLATION_PARENT_INDEX,
    partialFilterExpression: { installedVia: { $type: 'string' } },
  },
);
installationSchema.index(
  { orgId: 1, item: 1 },
  { name: INSTALLATION_ITEM_INDEX },
);

const historySchema = new Schema<InstallationHistoryDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    id: { type: String, required: true },
    revision: { type: Number, required: true },
    body: { type: String, required: true },
  },
  { collection: 'cpInstallationHistory', versionKey: false, autoIndex: false },
);
historySchema.index(
  { orgId: 1, id: 1, revision: -1 },
  { name: INSTALLATION_HISTORY_INDEX },
);

const grantSchema = new Schema<GrantDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    subject: { type: String, required: true },
    entries: { type: Schema.Types.Mixed, default: [] },
    revision: { type: Number, required: true },
  },
  {
    collection: 'cpGrants',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
// `usersOf`: who uses an installation (impact preview).
grantSchema.index(
  { orgId: 1, 'entries.installation': 1 },
  { name: GRANT_INSTALLATION_INDEX },
);

const ledgerSchema = new Schema<LedgerDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    object: { type: String, required: true },
    id: { type: String, required: true },
    revision: { type: Number, required: true },
  },
  { collection: 'cpCapabilityLedger', versionKey: false, autoIndex: false },
);
ledgerSchema.index({ orgId: 1 }, { name: LEDGER_ORG_INDEX });

export const CpInstallationModel: Model<InstallationDocument> =
  guardedModel<InstallationDocument>('CpInstallation', installationSchema);
export const CpInstallationHistoryModel: Model<InstallationHistoryDocument> =
  guardedModel<InstallationHistoryDocument>(
    'CpInstallationHistory',
    historySchema,
  );
export const CpGrantModel: Model<GrantDocument> = guardedModel<GrantDocument>(
  'CpGrant',
  grantSchema,
);
export const CpCapabilityLedgerModel: Model<LedgerDocument> =
  guardedModel<LedgerDocument>('CpCapabilityLedger', ledgerSchema);
