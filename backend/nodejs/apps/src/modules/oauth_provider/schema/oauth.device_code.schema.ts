import mongoose, { Document, Schema, Model, Types } from 'mongoose'

export enum OAuthDeviceCodeStatus {
  PENDING = 'pending',
  APPROVED = 'approved',
  DENIED = 'denied',
}

export interface IOAuthDeviceCode extends Document {
  deviceCodeHash: string
  userCode: string
  clientId: string
  scopes: string[]
  status: OAuthDeviceCodeStatus
  userId?: Types.ObjectId
  orgId?: Types.ObjectId
  interval: number
  lastPolledAt?: Date
  expiresAt: Date
  createdAt: Date
  updatedAt: Date
}

const OAuthDeviceCodeSchema = new Schema<IOAuthDeviceCode>(
  {
    deviceCodeHash: {
      type: String,
      required: true,
      unique: true,
      index: true,
    },
    userCode: {
      type: String,
      required: true,
      unique: true,
      index: true,
    },
    clientId: {
      type: String,
      required: true,
      index: true,
    },
    scopes: { type: [String], required: true },
    status: {
      type: String,
      enum: Object.values(OAuthDeviceCodeStatus),
      default: OAuthDeviceCodeStatus.PENDING,
    },
    userId: { type: Schema.Types.ObjectId, ref: 'users' },
    orgId: { type: Schema.Types.ObjectId, ref: 'org' },
    interval: { type: Number, default: 5 },
    lastPolledAt: { type: Date },
    expiresAt: {
      type: Date,
      required: true,
      index: { expireAfterSeconds: 0 },
    },
  },
  { timestamps: true },
)

export const OAuthDeviceCode: Model<IOAuthDeviceCode> =
  mongoose.model<IOAuthDeviceCode>(
    'oauthDeviceCode',
    OAuthDeviceCodeSchema,
    'oauthDeviceCodes',
  )
