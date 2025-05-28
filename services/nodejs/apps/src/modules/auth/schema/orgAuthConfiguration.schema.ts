import mongoose, { Schema, Document, Types, Model } from 'mongoose';

export interface IAuthMethod {
  type: 'samlSso' | 'otp' | 'password' | 'google' | 'microsoft' | 'azureAd' | 'oauth';
}

export enum AuthMethodType {
  SAML_SSO = 'samlSso',
  OTP = 'otp',
  PASSWORD = 'password',
  GOOGLE = 'google',
  MICROSOFT = 'microsoft',
  AZURE_AD = 'azureAd',
  OAUTH = 'oauth',
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
  createdAt?: Date;
  updatedAt?: Date;
}

// 🔹 Define Mongoose Schemas
const AuthMethodSchema = new Schema<IAuthMethod>(
  {
    type: {
      type: String,
      enum: ['samlSso', 'otp', 'password', 'google', 'microsoft', 'azureAd', 'oauth'],
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
  },
  { timestamps: true },
);

// 🔹 Create and Export Mongoose Model
export const OrgAuthConfig: Model<IOrgAuthConfig> =
  mongoose.model<IOrgAuthConfig>(
    'orgAuthConfig',
    OrgAuthConfigSchema,
    'orgAuthConfig',
  );
