import { Address } from '../../../libs/utils/address.utils';
import { generateUniqueSlug } from '../../../libs/repositories/slug/mongo';
import mongoose, { Document, Schema, Model } from 'mongoose';

// Define the interface for Org Document

export type AccountType = 'individual' | 'business';
export interface IOrg extends Document {
  slug: string;
  registeredName: string;
  shortName?: string;
  domain: string;
  contactEmail: string;
  accountType:AccountType;
  phoneNumber?: string;
  permanentAddress?: Address;
  isDeleted: boolean;
  deletedByUser?: string;
  onBoardingStatus : string;
  /** Where the signup came from: a `ref` tag, or `direct`. */
  signupChannel?: string;
  /** For a `direct` signup, the referring site's domain, if any. */
  signupReferrerDomain?: string;
  /** Written during admin demotion txns to serialize last-admin checks. */
  adminRoleGuardAt?: Date;
}

const orgSchema = new Schema<IOrg>(
  {
    slug: { type: String, unique: true },
    registeredName: {
      type: String,
      validate: {
        // `this` is the document on save and the Query for update validators.
        validator: function (this: unknown, value: string) {
          const { accountType } = this as { accountType?: string };
          // Required only when accountType is 'business'
          return accountType === 'business' ? !!value : true;
        },
        message: 'Registered Name is required for business accounts',
      },
    },
    shortName: {
      type: String,
    },
    domain: { type: String, required: true },
    contactEmail: {
      type: String,
      required: [true, 'Email required'],
      lowercase: true,
    },
    accountType: {
      type: String,
      required: true,
      enum: ['individual', 'business'], // Ensuring only valid values
    },
    phoneNumber: {
      type: String,
    },
    permanentAddress: {
      type: {
        addressLine1: { type: String },
        city: { type: String },
        state: { type: String },
        postCode: { type: String },
        country: { type: String, },
      },
    },
    onBoardingStatus : {
      type : String,
      enum: ['configured', 'notConfigured','skipped'], 

    },
    signupChannel: { type: String },
    signupReferrerDomain: { type: String },
    isDeleted: { type: Boolean, default: false },
    deletedByUser: { type: String },
    adminRoleGuardAt: { type: Date },
  },
  { timestamps: true },
);

orgSchema.pre<IOrg>('save', async function () {
  // Mongoose 9: async hooks signal failure by throwing, with no next callback.
  if (!this.slug) {
    this.slug = await generateUniqueSlug('Org');
  }
});

export const Org: Model<IOrg> =
  (mongoose.models['org'] as Model<IOrg>) ||
  mongoose.model<IOrg>('org', orgSchema, 'org');
