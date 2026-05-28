import mongoose, { Document, Schema, Model } from "mongoose";
import { NOTIFICATION_RETENTION_DAYS } from "../utils/notification.utils";

const NOTIFICATION_TTL_SECONDS = NOTIFICATION_RETENTION_DAYS * 24 * 60 * 60;

const { ObjectId } = Schema.Types;

export interface INotification extends Document {
  orgId: mongoose.Types.ObjectId;
  type: string;
  severity: "info" | "warning" | "error" | "critical";
  status: "read" | "unread" | "archived";
  origin: "Connector Service" | "Indexing Service" | "AI Service" | "External Service";
  initiator?: mongoose.Types.ObjectId;
  externalInitiator?: string;
  assignedTo: mongoose.Types.ObjectId;
  payload?: INotificationPayload;
  isDeleted: boolean;
  deletedBy?: mongoose.Types.ObjectId;
  createdAt?: Date;
  updatedAt?: Date;
}

/** Shared fields for every notification payload variant. */
export interface IBaseNotificationPayload {
  title: string;
  message: string;
  redirectLink?: string;
  errorCode?: string;
}

/** Connector- and permission-related payload fields. */
export interface IConnectorNotificationPayload extends IBaseNotificationPayload {
  connectorId?: string;
  connectorName?: string;
  userEmail?: string;
  groupId?: string;
  extGroupId?: string;
  GroupName?: string;
  roleId?: string;
  extRoleId?: string;
  roleName?: string;
  rgId?: string;
  extRgId?: string;
  rgName?: string;
  recordId?: string;
  extRecordId?: string;
  recordName?: string;
  recordType?: string;
}

/** Union grows when IndexingNotificationPayload and other variants are added. */
export type INotificationPayload = IConnectorNotificationPayload;

const baseNotificationPayloadFields = {
  title: {
    type: String,
    required: [true, "Notification payload title is required"] as [boolean, string],
  },
  message: {
    type: String,
    required: [true, "Notification payload message is required"] as [boolean, string],
  },
  redirectLink: {
    type: String,
    required: false,
  },
  errorCode: {
    type: String,
    required: false,
  },
};

const connectorNotificationPayloadFields = {
  connectorId: {
    type: String,
    required: false,
  },
  connectorName: {
    type: String,
    required: false,
  },
  userEmail: {
    type: String,
    required: false,
  },
  groupId: {
    type: String,
    required: false,
  },
  extGroupId: {
    type: String,
    required: false,
  },
  GroupName: {
    type: String,
    required: false,
  },
  roleId: {
    type: String,
    required: false,
  },
  extRoleId: {
    type: String,
    required: false,
  },
  roleName: {
    type: String,
    required: false,
  },
  rgId: {
    type: String,
    required: false,
  },
  extRgId: {
    type: String,
    required: false,
  },
  rgName: {
    type: String,
    required: false,
  },
  recordId: {
    type: String,
    required: false,
  },
  extRecordId: {
    type: String,
    required: false,
  },
  recordName: {
    type: String,
    required: false,
  },
  recordType: {
    type: String,
    required: false,
  },
};

const notificationPayloadSchema = new Schema<INotificationPayload>(
  {
    ...baseNotificationPayloadFields,
    ...connectorNotificationPayloadFields,
  },
  { _id: false },
);

const notificationSchema = new Schema<INotification>(
  {
    orgId: {
      type: ObjectId,
      required: [true, "Organization ID is required"],
    },
    type: {
      type: String,
      required: [true, "Notification type is required"],
    },
    severity: {
      type: String,
      required: false,
      enum: ["info", "warning", "error", "critical"],
    },
    status: {
      type: String,
      enum: ["read", "unread", "archived"],
      default: "unread",
    },
    origin: {
      type: String,
      enum: ["Connector Service", "Indexing Service", "AI Service", "External Service"],
      default: "Connector Service",
    },
    assignedTo: {
      type: ObjectId,
      required: [true, "Assignee is required"],
    },
    payload: {
      type: notificationPayloadSchema,
      required: false,
    },
    isDeleted: {
      type: Boolean,
      default: false,
    },
    deletedBy: {
      type: ObjectId,
      required: false,
    },
  },
  { timestamps: true }
);

// Indexes for performance improvements
notificationSchema.index({ orgId: 1, status: 1 });
notificationSchema.index({ assignedTo: 1, isDeleted: 1, createdAt: -1, _id: -1 });
notificationSchema.index({ createdAt: 1 }, { expireAfterSeconds: NOTIFICATION_TTL_SECONDS });

export const Notifications: Model<INotification> = mongoose.model<INotification>("Notifications", notificationSchema);
