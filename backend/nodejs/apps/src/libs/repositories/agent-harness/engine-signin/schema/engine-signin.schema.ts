import { Model, Schema } from 'mongoose';
import { harnessModel } from '../../schema/harness-model.schema';

/**
 * SIGNIN's records, apart from the user document so no user listing (an
 * admin's members page) ever carries a person's Claude email: one document
 * per person (`<orgId>:u:<userId>`) and one per org (`<orgId>:org`).
 */
export interface EngineSignInDocument {
  _id: string;
  orgId: string;
  userId: string | null;
  enabled?: boolean;
  email?: string;
  plan?: string;
  signedInAt?: number;
  disconnectedAt?: number;
  /** The org's switch, on the org's document only. */
  claude?: boolean;
}

const schema = new Schema<EngineSignInDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    userId: { type: String, default: null },
    enabled: { type: Boolean },
    email: { type: String, maxlength: 320 },
    plan: { type: String, maxlength: 64 },
    signedInAt: { type: Number },
    disconnectedAt: { type: Number },
    claude: { type: Boolean },
  },
  { collection: 'agentEngineSignIns', versionKey: false, autoIndex: false },
);

export const EngineSignInModel: Model<EngineSignInDocument> =
  harnessModel<EngineSignInDocument>('AgentHarnessEngineSignIn', schema);
