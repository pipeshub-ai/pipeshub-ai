import mongoose, { Document, Schema, model } from 'mongoose';

// Define the TypeScript interface for UserCredential document
export interface IUserCredentials extends Document {
  userId?: string | null;
  orgId: string;
  ipAddress: string;
  otpValidity?: number;
  hashedOTP?: string;
  hashedPassword?: string;
  forceNewPasswordGeneration: boolean;
  wrongCredentialCount: number;
  isBlocked: boolean;
  isDeleted: boolean;
  createdAt?: Date;
  updatedAt?: Date;
}

// Define the Mongoose schema
const userCredentialSchema = new Schema<IUserCredentials>(
  {
    userId: {
      type: String,
      ref: 'users',
      default: null,
    },
    orgId: {
      type: String,
      required: true,
      ref: 'orgs',
    },
    ipAddress: {
      type: String,
      required: true,
    },
    otpValidity: { type: Number },
    hashedOTP: { type: String },
    hashedPassword: { type: String },
    forceNewPasswordGeneration: {
      type: Boolean,
      default: false,
    },
    wrongCredentialCount: {
      type: Number,
      default: 0,
    },
    isBlocked: {
      type: Boolean,
      default: false,
    },
    isDeleted: {
      type: Boolean,
      default: false,
    },
  },
  { timestamps: true },
);

// Pre-hooks to enforce validation on update methods
userCredentialSchema.pre(
  'findOneAndUpdate',
  function (this: mongoose.Query<any, IUserCredentials>, next) {
    this.setOptions({ runValidators: true });
    next();
  },
);

// Create and export the model
export const UserCredentials = model<IUserCredentials>(
  'userCredentials',
  userCredentialSchema,
  'userCredentials',
);
