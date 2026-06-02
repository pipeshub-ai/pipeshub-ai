import mongoose, { Document, Schema, Model } from "mongoose";
import { NOTIFICATION_RETENTION_DAYS } from "../utils/notification-api.utils";

const NOTIFICATION_TTL_SECONDS = NOTIFICATION_RETENTION_DAYS * 24 * 60 * 60;

const { ObjectId } = Schema.Types;

export interface INotification extends Document {
  title: string;
  message: string;
  type: string;
  orgId: mongoose.Types.ObjectId;
  originService: "Connector Service" | "Indexing Service" | "AI Service" | "External Service";
  severity: "info" | "warning" | "error" | "critical" | "success";
  status: "read" | "unread" | "archived";
  assignedTo: mongoose.Types.ObjectId;
  redirectLink?: string;
  payload?: Record<string, unknown>;
  isDeleted: boolean;
  deletedBy?: mongoose.Types.ObjectId;
  createdAt?: Date;
  updatedAt?: Date;
}

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
    title: {
      type: String,
      required: false,
    },
    message: {
      type: String,
      required: false,
    },
    redirectLink: {
      type: String,
      required: false,
    },
    severity: {
      type: String,
      required: false,
      enum: ["info", "warning", "error", "critical", "success"],
    },
    status: {
      type: String,
      enum: ["read", "unread", "archived"],
      default: "unread",
    },
    originService: {
      type: String,
      enum: ["Connector Service", "Indexing Service", "AI Service", "External Service"],
    },
    assignedTo: {
      type: ObjectId,
      required: [true, "Assignee is required"],
    },
    payload: {
      type: Schema.Types.Mixed,
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
