import { Model, Schema } from 'mongoose';
import { guardedModel } from '../../../id-filter-guard/mongo';
import type { ConfigChanged } from '../../../../../modules/control-plane/domain/config-change';

/** The ordered-steps writer's hold on an org (standalone Mongo only). */
export interface OrgHeadLease {
  token: string;
  /** Epoch ms after which another writer may take the lease over. */
  until: number;
  /** Recorded before the write's effects; a takeover completes its audit. */
  intent: ConfigChanged | null;
}

export interface OrgHeadDocument {
  _id: string;
  revision: number;
  updatedAt: Date;
  lease: OrgHeadLease | null;
}

/**
 * `cpOrgHeads` (02-lld §4.2): one row per org whose revision rises once per
 * committed control-plane write, so an org's writes have one total order.
 */
const orgHeadSchema = new Schema<OrgHeadDocument>(
  {
    _id: { type: String, required: true },
    revision: { type: Number, required: true },
    updatedAt: { type: Date, required: true },
    lease: { type: Schema.Types.Mixed, default: null },
  },
  {
    collection: 'cpOrgHeads',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);

export const CpOrgHeadModel: Model<OrgHeadDocument> =
  guardedModel<OrgHeadDocument>('CpOrgHead', orgHeadSchema);
