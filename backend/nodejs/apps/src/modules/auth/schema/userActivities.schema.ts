import mongoose, { Schema, Document, Model, Types } from 'mongoose';
import {
  USER_ACTIVITY_TYPES,
  USER_LOGIN_MODES,
  type UserActivityType,
  type UserLoginMode,
} from '../../../libs/utils/userActivities.utils';

// 🔹 Define TypeScript Interfaces
export interface IUserActivity extends Document {
  email: string;
  userId: Types.ObjectId;
  orgId?: Types.ObjectId;
  activityType: UserActivityType;
  loginMode?: UserLoginMode;
  ipAddress: string;
  isDeleted?: boolean;
  createdAt?: Date;
  updatedAt?: Date;
}

// 🔹 Define Mongoose Schema
const UserActivitySchema = new Schema<IUserActivity>(
  {
    email: {
      type: String,
      lowercase: true,
    },
    userId: {
      type: Schema.Types.ObjectId,
      ref: 'users',
    },
    orgId: {
      type: Schema.Types.ObjectId,
      ref: 'orgs',
    },
    // Both lists come from the utils, so a type or mode written there is never refused here (R46FIX-1).
    activityType: {
      type: String,
      enum: [...USER_ACTIVITY_TYPES],
      required: true,
    },
    loginMode: {
      type: String,
      enum: [...USER_LOGIN_MODES],
    },
    ipAddress: {
      type: String,
      required: true,
    },
    isDeleted: {
      type: Boolean,
      default: false,
    },
  },
  { timestamps: true },
);

// Every authenticated request looks up the caller's latest session-ending
// activity: equality on userId, orgId and isDeleted, activityType in a set,
// newest createdAt first. Without this each lookup scanned the collection.
UserActivitySchema.index(
  { userId: 1, orgId: 1, isDeleted: 1, activityType: 1, createdAt: -1 },
  { name: 'session_ending_activity_lookup' },
);

// 🔹 Ensure Validation Runs on `findOneAndUpdate`
UserActivitySchema.pre('findOneAndUpdate', function () {
  this.setOptions({ runValidators: true });
});

// 🔹 Create and Export Mongoose Model
export const UserActivities: Model<IUserActivity> =
  mongoose.model<IUserActivity>(
    'userActivities',
    UserActivitySchema,
    'userActivities',
  );