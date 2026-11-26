import {
  indexMigration,
  type HarnessMigration,
} from '../../../utils/mongo-index-migration';
import { LIVE_BINDING_STATUSES } from '../../../../modules/control-plane/domain/bindings';
import {
  BINDING_DUE_INDEX,
  BINDING_FOR_OWNER_INDEX,
  BINDING_INSTALLATION_INDEX,
  BINDING_LIVE_OWNER_INDEX,
  BINDING_OWNER_INDEX,
  CpBindingModel,
} from '../bindings/schema/bindings.schema';
import {
  CATALOG_IDENTITY_INDEX,
  CATALOG_ORDER_INDEX,
  CatalogItemModel,
} from '../catalog/schema/catalog.schema';
import {
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
  CpCapabilityLedgerModel,
  LEDGER_ORG_INDEX,
  GRANT_INSTALLATION_INDEX,
  INSTALLATION_HISTORY_INDEX,
  INSTALLATION_ITEM_INDEX,
  INSTALLATION_OWNER_INDEX,
  INSTALLATION_PARENT_INDEX,
  INSTALLATION_STATUS_INDEX,
} from '../installations/schema/installations.schema';
import {
  POLICY_HISTORY_SCOPE_INDEX,
  POLICY_LAYERS_ORG_INDEX,
  PolicyHistoryModel,
  PolicyLayerModel,
} from '../policy/schema/policy.schema';

const bindings = CpBindingModel.collection.collectionName;

/**
 * D2 for CPD-8: the indexes of 02-lld §4.2 for `cpBindings`. The first build
 * of this migration made `(orgId, installation, owner)` unique over every
 * row, so an owner whose binding was revoked could never bind again; it is
 * retired for the two below (unique over the binding that is not revoked).
 */
export const BINDING_MIGRATION = indexMigration(
  'control-plane-bindings',
  [
    {
      collection: bindings,
      name: BINDING_LIVE_OWNER_INDEX,
      key: { orgId: 1, installation: 1, owner: 1 },
      unique: true,
      partialFilterExpression: { status: { $in: [...LIVE_BINDING_STATUSES] } },
    },
    {
      collection: bindings,
      name: BINDING_INSTALLATION_INDEX,
      key: { orgId: 1, installation: 1, owner: 1, _id: 1 },
    },
    {
      collection: bindings,
      name: BINDING_DUE_INDEX,
      key: { status: 1, expiresAt: 1 },
    },
    {
      collection: bindings,
      name: BINDING_FOR_OWNER_INDEX,
      key: { orgId: 1, owner: 1 },
    },
  ],
  { retired: [{ collection: bindings, name: BINDING_OWNER_INDEX }] },
);

const items = CatalogItemModel.collection.collectionName;

/** D2 for CPD-2: an org's catalog rows, unique per version, listed in catalog order. */
export const CATALOG_MIGRATION = indexMigration('control-plane-catalog', [
  {
    collection: items,
    name: CATALOG_IDENTITY_INDEX,
    key: { orgId: 1, kind: 1, publisher: 1, name: 1, version: 1 },
    unique: true,
  },
  {
    collection: items,
    name: CATALOG_ORDER_INDEX,
    key: { orgId: 1, kind: 1, sortName: 1, ref: 1 },
  },
]);

const installations = CpInstallationModel.collection.collectionName;

/** D2 for CPD-7: the indexes of 02-lld §4.2 for installations, their history and grants. */
export const INSTALLATIONS_MIGRATION = indexMigration(
  'control-plane-installations',
  [
    {
      collection: installations,
      name: INSTALLATION_STATUS_INDEX,
      key: { orgId: 1, status: 1, kind: 1 },
    },
    {
      collection: installations,
      name: INSTALLATION_OWNER_INDEX,
      key: { orgId: 1, owner: 1 },
    },
    {
      collection: installations,
      name: INSTALLATION_PARENT_INDEX,
      key: { orgId: 1, installedVia: 1 },
      partialFilterExpression: { installedVia: { $type: 'string' } },
    },
    {
      collection: installations,
      name: INSTALLATION_ITEM_INDEX,
      key: { orgId: 1, item: 1 },
    },
    {
      collection: CpInstallationHistoryModel.collection.collectionName,
      name: INSTALLATION_HISTORY_INDEX,
      key: { orgId: 1, id: 1, revision: -1 },
    },
    {
      collection: CpGrantModel.collection.collectionName,
      name: GRANT_INSTALLATION_INDEX,
      key: { orgId: 1, 'entries.installation': 1 },
    },
    {
      collection: CpCapabilityLedgerModel.collection.collectionName,
      name: LEDGER_ORG_INDEX,
      key: { orgId: 1 },
    },
  ],
);

/** D2 for CPD-4: an org's layers by kind, and a scope's history newest first. */
export const POLICY_STORE_MIGRATION = indexMigration(
  'control-plane-policy-store',
  [
    {
      collection: PolicyLayerModel.collection.collectionName,
      name: POLICY_LAYERS_ORG_INDEX,
      key: { orgId: 1, kind: 1 },
    },
    {
      collection: PolicyHistoryModel.collection.collectionName,
      name: POLICY_HISTORY_SCOPE_INDEX,
      key: { orgId: 1, scope: 1, revision: -1 },
    },
  ],
);

/**
 * Every control-plane Mongo migration, in the order `up` runs them. The
 * schemas set `autoIndex: false`; C-15 holds models and these lists to each
 * other. A new collection or index is a new entry, never an edit to a shipped one.
 */
export const CONTROL_PLANE_MIGRATIONS: readonly HarnessMigration[] = [
  CATALOG_MIGRATION,
  INSTALLATIONS_MIGRATION,
  POLICY_STORE_MIGRATION,
  BINDING_MIGRATION,
];
