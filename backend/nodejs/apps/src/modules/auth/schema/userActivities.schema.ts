import mongoose, { Schema, Document, Model, Types } from 'mongoose';
import { userActivitiesType } from '../../../libs/utils/userActivities.utils';

// Destructure user activity types
const {
  LOGIN_ATTEMPT,
  LOGIN,
  LOGOUT,
  OTP_GENERATE,
  WRONG_OTP,
  WRONG_PASSWORD,
  REFRESH_TOKEN,
  PASSWORD_CHANGED,
  ROLE_CHANGED,
  ACCOUNT_BLOCKED,
  ACCOUNT_DELETED,
  ACCOUNT_RESTORED,
} = userActivitiesType;

// 🔹 Define TypeScript Interfaces
export interface IUserActivity extends Document {
  email: string;
  userId: Types.ObjectId;
  orgId?: Types.ObjectId;
  activityType:
    | typeof LOGIN
    | typeof LOGOUT
    | typeof LOGIN_ATTEMPT
    | typeof OTP_GENERATE
    | typeof WRONG_OTP
    | typeof WRONG_PASSWORD
    | typeof REFRESH_TOKEN
    | typeof PASSWORD_CHANGED
    | typeof ROLE_CHANGED
    | typeof ACCOUNT_BLOCKED
    | typeof ACCOUNT_DELETED
    | typeof ACCOUNT_RESTORED;
  loginMode?:
    | 'OTP'
    | 'PASSWORD'
    | 'AUTH SERVICE'
    | 'GOOGLE OAUTH'
    | 'MICROSOFT OAUTH'
    | 'AZUREAD OAUTH'
    | 'SSO'
    | 'OAUTH'
    | 'GITHUB OAUTH';
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
    activityType: {
      type: String,
      enum: [
        LOGIN,
        LOGOUT,
        LOGIN_ATTEMPT,
        OTP_GENERATE,
        WRONG_OTP,
        WRONG_PASSWORD,
        REFRESH_TOKEN,
        PASSWORD_CHANGED,
        ROLE_CHANGED,
        ACCOUNT_BLOCKED,
        ACCOUNT_DELETED,
        ACCOUNT_RESTORED,
      ],
      required: true,
    },
    loginMode: {
      type: String,
      enum: [
        'OTP',
        'PASSWORD',
        'AUTH SERVICE',
        'GOOGLE OAUTH',
        'MICROSOFT OAUTH',
        'SSO',
        'AZUREAD OAUTH',
        'OAUTH',
        'GITHUB OAUTH',
      ],
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
UserActivitySchema.pre('findOneAndUpdate', function (next) {
  this.setOptions({ runValidators: true });
  next();
});

// 🔹 Create and Export Mongoose Model
export const UserActivities: Model<IUserActivity> =
  mongoose.model<IUserActivity>(
    'userActivities',
    UserActivitySchema,
    'userActivities',
  );