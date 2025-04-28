import mongoose, { Schema, Document, Types, Model } from 'mongoose';

// Enum for Connector Types
export enum ConnectorsType {
  GOOGLE_WORKSPACE = 'Google Workspace',
}

// Interface for ConnectorsConfig
interface IConnectorsConfig extends Document {
  orgId: Types.ObjectId;
  name: ConnectorsType;
  isEnabled: boolean;
  lastUpdatedBy: Types.ObjectId;
  createdAt?: number;
  updatedAt?: number;
}

// Schema for ConnectorsConfig
const ConnectorsConfigSchema = new Schema<IConnectorsConfig>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true },
    name: { type: String, enum: Object.values(ConnectorsType), required: true },
    isEnabled: { type: Boolean, default: true },
    lastUpdatedBy: { type: Schema.Types.ObjectId, required: true },
    createdAt: { type: Number, default: Date.now },
    updatedAt: { type: Number, default: Date.now },
  },
  { timestamps: false },
);
ConnectorsConfigSchema.pre<IConnectorsConfig>('save', function (next) {
  if (!this.isNew) {
    this.updatedAt = Date.now();
  }
  next();
});

// Pre-hook for findOneAndUpdate to update the timestamp
ConnectorsConfigSchema.pre('findOneAndUpdate', function (next) {
  this.set({ updatedAt: Date.now() });
  next();
});
// Export the Mongoose Model
export const ConnectorsConfig: Model<IConnectorsConfig> =
  mongoose.model<IConnectorsConfig>(
    'connectorsConfig',
    ConnectorsConfigSchema,
    'connectorsConfig',
  );
