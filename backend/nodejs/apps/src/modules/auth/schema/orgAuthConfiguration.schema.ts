import mongoose, { Schema, Document, Types, Model } from 'mongoose';
import { AuthMethodType, type IAuthMethod } from '../types/auth-method';

export { AuthMethodType };
export type { IAuthMethod };

interface IAuthStep {
  order: number;
  allowedMethods: IAuthMethod[];
}

interface IOrgAuthConfig extends Document {
  orgId: Types.ObjectId;
  domain?: string;
  authSteps: IAuthStep[];
  /** Methods the 1.0 upgrade took out of this policy (see removedSignInMethods.ts). */
  removedSignInMethods?: string[];
  isDeleted?: boolean;
  createdAt?: Date;
  updatedAt?: Date;
}

// 🔹 Define Mongoose Schemas
const AuthMethodSchema = new Schema<IAuthMethod>(
  {
    type: {
      type: String,
      enum: ['samlSso', 'otp', 'password', 'google', 'microsoft', 'azureAd', 'oauth', 'github'],
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
    removedSignInMethods: { type: [String], default: undefined },
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
