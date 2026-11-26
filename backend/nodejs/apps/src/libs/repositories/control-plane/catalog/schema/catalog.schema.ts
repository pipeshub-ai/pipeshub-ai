import { Model, Schema } from 'mongoose';
import { guardedModel } from '../../../id-filter-guard/mongo';

/**
 * An org's catalog rows (`cpCatalogItems`, 02-lld §4.2). The item is kept as
 * its JSON text: descriptors and config schemas carry JSON Schema, whose `$`
 * and dotted keys Mongo field names do not take. Indexed fields are copies.
 */
export interface CatalogItemDocument {
  /** `{orgId}|{ref}`. */
  _id: string;
  orgId: string;
  ref: string;
  kind: string;
  publisher: string;
  name: string;
  version: string;
  /** The display name lower-cased: the list order and `q` match. */
  sortName: string;
  createdAt: number;
  deprecated: boolean;
  body: string;
}

export const CATALOG_IDENTITY_INDEX = 'cp_catalog_identity';
export const CATALOG_ORDER_INDEX = 'cp_catalog_order';

const catalogItemSchema = new Schema<CatalogItemDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    ref: { type: String, required: true },
    kind: { type: String, required: true },
    publisher: { type: String, required: true },
    name: { type: String, required: true },
    version: { type: String, required: true },
    sortName: { type: String, required: true },
    createdAt: { type: Number, required: true },
    deprecated: { type: Boolean, required: true },
    body: { type: String, required: true },
  },
  { collection: 'cpCatalogItems', versionKey: false, autoIndex: false },
);
// One row per item version in an org.
catalogItemSchema.index(
  { orgId: 1, kind: 1, publisher: 1, name: 1, version: 1 },
  { name: CATALOG_IDENTITY_INDEX, unique: true },
);
// `list` in catalog order, by kind or across kinds.
catalogItemSchema.index(
  { orgId: 1, kind: 1, sortName: 1, ref: 1 },
  { name: CATALOG_ORDER_INDEX },
);

export const CatalogItemModel: Model<CatalogItemDocument> =
  guardedModel<CatalogItemDocument>(
    'ControlPlaneCatalogItem',
    catalogItemSchema,
  );
