import { generateUniqueSlug } from '../controller/counters.controller';
import { Document, Schema, Types, model } from 'mongoose';

export const groupTypes = ['admin', 'standard', 'everyone', 'custom'];

interface UserGroup extends Document {
  slug?: string;
  name: string;
  type: 'admin' | 'standard' | 'custom' | 'everyone';
  orgId: Types.ObjectId;
  users: Array<Types.ObjectId>;
  isDeleted?: boolean;
  deletedBy?: Types.ObjectId;
  createdAt: number;
  updatedAt: number;
}

const userGroupsSchema = new Schema<UserGroup>(
  {
    slug: { type: String, unique: true },
    name: {
      type: String,
      required: [true, 'Group name is required'],
    },
    type: {
      type: String,
      enum: groupTypes,
      required: true,
    },
    orgId: {
      type: Schema.Types.ObjectId,
      ref: 'org',
      required: true,
    },
    users: [{ type: Schema.Types.ObjectId }],
    isDeleted: { type: Boolean, default: false },
    deletedBy: { type: Schema.Types.ObjectId, ref: 'users' },
    createdAt: { type: Number, default: Date.now },
    updatedAt: { type: Number, default: Date.now },
  },
  { timestamps: false },
);

userGroupsSchema.pre<UserGroup>('save', async function (next) {
  try {
    if (!this.slug) {
      this.slug = await generateUniqueSlug('UserGroup');
    }

    // Update the updatedAt timestamp if this is not a new document
    if (!this.isNew) {
      this.updatedAt = Date.now();
    }

    next();
  } catch (error) {
    next(error as Error); // Pass any errors to the next middleware
  }
});

export const UserGroups = model<UserGroup>(
  'userGroups',
  userGroupsSchema,
  'userGroups',
);
