import { Model, Schema } from 'mongoose';
import { harnessModel } from '../../schema/harness-model.schema';

export interface DataKeyDocument {
  /** The key id (random); a key recreated after shredding never reuses one. */
  _id: string;
  orgId: string;
  version: number;
  kekId: string;
  wrapped: string;
  createdAt: number;
  /** Seals reserved against this key (DP-2); absent on keys created before counting. */
  seals?: number;
}

const dataKeySchema = new Schema<DataKeyDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    version: { type: Number, required: true, min: 1 },
    kekId: { type: String, required: true },
    wrapped: { type: String, required: true },
    createdAt: { type: Number, required: true },
    seals: { type: Number, required: false, min: 0 },
  },
  { collection: 'agentHarnessDataKeys', versionKey: false, autoIndex: false },
);
dataKeySchema.index({ orgId: 1, version: 1 }, { unique: true });

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const AgentHarnessDataKeyModel = model(
  'AgentHarnessDataKey',
  dataKeySchema,
);
