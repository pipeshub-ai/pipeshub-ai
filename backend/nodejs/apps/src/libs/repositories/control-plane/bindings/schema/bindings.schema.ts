import { Model, Schema } from 'mongoose';
import { guardedModel } from '../../../id-filter-guard/mongo';
import { LIVE_BINDING_STATUSES } from '../../../../../modules/control-plane/domain/bindings';
import type { Binding } from '../../../../../modules/control-plane/domain/objects';

/**
 * Binding metadata (`cpBindings`, 02-lld §4.2), never a secret: the row is
 * the generated `Binding` itself, keyed by its id. Indexes are the
 * migration's (`binding-migration.ts`), never `autoIndex`.
 */
export type BindingDocument = Omit<Binding, 'id'> & {
  _id: string;
  /** The token refresher replica renewing it (QA23-F2); not part of the binding, cleared by any write. */
  refreshClaim?: { holder: string; until: number };
};

/** Until an owner could bind again after a revoke: unique over every row. The migration drops it. */
export const BINDING_OWNER_INDEX = 'cp_bindings_owner';
export const BINDING_LIVE_OWNER_INDEX = 'cp_bindings_live_owner';
export const BINDING_INSTALLATION_INDEX = 'cp_bindings_installation';
export const BINDING_DUE_INDEX = 'cp_bindings_due';
export const BINDING_FOR_OWNER_INDEX = 'cp_bindings_for_owner';

const bindingSchema = new Schema<BindingDocument>(
  {
    _id: { type: String, required: true },
    schemaVersion: { type: Number, required: true },
    orgId: { type: String, required: true },
    installation: { type: String, required: true },
    owner: { type: String, required: true },
    mode: { type: String, required: true },
    status: { type: String, required: true },
    secret: { type: Schema.Types.Mixed, default: null },
    scopes: { type: [String], default: [] },
    expiresAt: { type: Number, default: null },
    refreshedAt: { type: Number, default: null },
    lastError: { type: Schema.Types.Mixed, default: null },
    revision: { type: Number, required: true },
    refreshClaim: { type: Schema.Types.Mixed, default: undefined },
  },
  {
    collection: 'cpBindings',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
// One binding that is not revoked per installation and owner in an org; revoked ones stay as history.
bindingSchema.index(
  { orgId: 1, installation: 1, owner: 1 },
  {
    name: BINDING_LIVE_OWNER_INDEX,
    unique: true,
    partialFilterExpression: { status: { $in: [...LIVE_BINDING_STATUSES] } },
  },
);
// An installation's bindings by owner, then id: what its removal revokes, a page at a time.
bindingSchema.index(
  { orgId: 1, installation: 1, owner: 1, _id: 1 },
  { name: BINDING_INSTALLATION_INDEX },
);
// The refresher's scan: live bindings by expiry, every org.
bindingSchema.index({ status: 1, expiresAt: 1 }, { name: BINDING_DUE_INDEX });
// "My connections".
bindingSchema.index({ orgId: 1, owner: 1 }, { name: BINDING_FOR_OWNER_INDEX });

export const CpBindingModel: Model<BindingDocument> =
  guardedModel<BindingDocument>('ControlPlaneBinding', bindingSchema);
