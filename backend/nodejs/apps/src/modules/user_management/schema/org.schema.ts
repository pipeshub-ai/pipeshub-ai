import { Address } from '../../../libs/utils/address.utils';
import { generateUniqueSlug } from '../controller/counters.controller';
import mongoose, { Document, Schema } from 'mongoose';

// Define the interface for Org Document

export type AccountType = 'individual' | 'business';
interface IOrg extends Document {
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
}

const orgSchema = new Schema<IOrg>(
  {
    slug: { type: String, unique: true },
    registeredName: {
      type: String,
      validate: {
        validator: function (this: IOrg, value: string) {
          // Required only when accountType is 'business'
          return this.accountType === 'business' ? !!value : true;
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
    isDeleted: { type: Boolean, default: false },
    deletedByUser: { type: String },
  },
  { timestamps: true },
);

orgSchema.pre<IOrg>('save', async function (next) {
  try {
    if (!this.slug) {
      this.slug = await generateUniqueSlug('Org');
    }
    next();
  } catch (error) {
    next(error as Error);
  }
});

export const Org = mongoose.model<IOrg>('org', orgSchema, 'org');
