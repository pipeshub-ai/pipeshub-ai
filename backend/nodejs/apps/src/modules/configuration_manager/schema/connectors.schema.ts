import mongoose, { Schema, Document, Types, Model } from 'mongoose';
import { ConnectorNames } from '../../../libs/types/connector.types';

// Interface for ConnectorsConfig
interface IConnectorsConfig extends Document {
  orgId: Types.ObjectId;
  name: ConnectorNames;
  isEnabled: boolean;
  lastUpdatedBy: Types.ObjectId;
  createdAt?: Date;
  updatedAt?: Date;
}

// Schema for ConnectorsConfig
const ConnectorsConfigSchema = new Schema<IConnectorsConfig>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true },
    name: { type: String, enum: Object.values(ConnectorNames), required: true },
    isEnabled: { type: Boolean, default: true },
    lastUpdatedBy: { type: Schema.Types.ObjectId, required: true },
  },
  { timestamps: true },
);

// Export the Mongoose Model
export const ConnectorsConfig: Model<IConnectorsConfig> =
  mongoose.model<IConnectorsConfig>(
    'connectorsConfig',
    ConnectorsConfigSchema,
    'connectorsConfig',
  );
