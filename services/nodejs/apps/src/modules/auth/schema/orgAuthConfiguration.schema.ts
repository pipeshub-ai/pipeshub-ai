import mongoose, { Schema, Document, Types, Model } from 'mongoose';

export interface IAuthMethod {
  type: 'samlSso' | 'otp' | 'password' | 'google' | 'microsoft' | 'azureAd';
}

export enum AuthMethodType {
  SAML_SSO = 'samlSso',
  OTP = 'otp',
  PASSWORD = 'password',
  GOOGLE = 'google',
  MICROSOFT = 'microsoft',
  AZURE_AD = 'azureAd',
}

interface IAuthStep {
  order: number;
  allowedMethods: IAuthMethod[];
}

interface IOrgAuthConfig extends Document {
  orgId: Types.ObjectId;
  domain?: string;
  authSteps: IAuthStep[];
  isDeleted?: boolean;
  createdAt?: number; // Changed from Date to number
  updatedAt?: number; // Changed from Date to number
}

// 🔹 Define Mongoose Schemas
const AuthMethodSchema = new Schema<IAuthMethod>(
  {
    type: {
      type: String,
      enum: ['samlSso', 'otp', 'password', 'google', 'microsoft', 'azureAd'],
      required: true,
    },
  },
  { _id: false },
);

const AuthStepSchema = new Schema<IAuthStep>(
  {
    order: { type: Number, required: true },
    allowedMethods: [AuthMethodSchema],
  },
  { _id: false },
);

const OrgAuthConfigSchema = new Schema<IOrgAuthConfig>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true },
    domain: { type: String },
    authSteps: {
      type: [AuthStepSchema],
      validate: [
        {
          validator: function (steps: IAuthStep[]) {
            return steps.length > 0 && steps.length <= 3;
          },
          message: 'Must have between 1 and 3 authentication steps',
        },
        {
          validator: function (steps: IAuthStep[]) {
            const orders = steps.map((step) => step.order);
            return new Set(orders).size === steps.length;
          },
          message: 'Each step must have a unique order',
        },
      ],
    },
    isDeleted: { type: Boolean, default: false },
    createdAt: {
      type: Number,
      default: Date.now,
    },
    updatedAt: {
      type: Number,
      default: Date.now,
    },
  },
  { timestamps: false },
);

// Pre-save hook to update the updatedAt field
OrgAuthConfigSchema.pre<IOrgAuthConfig>('save', function (next) {
  if (!this.isNew) {
    this.updatedAt = Date.now();
  }
  next();
});

// Pre-hook for findOneAndUpdate to update the timestamp
OrgAuthConfigSchema.pre('findOneAndUpdate', function (next) {
  this.set({ updatedAt: Date.now() });
  next();
});

// 🔹 Create and Export Mongoose Model
export const OrgAuthConfig: Model<IOrgAuthConfig> =
  mongoose.model<IOrgAuthConfig>(
    'orgAuthConfig',
    OrgAuthConfigSchema,
    'orgAuthConfig',
  );
