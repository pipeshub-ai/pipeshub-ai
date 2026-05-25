import mongoose, { Document, Schema, Model } from "mongoose";

const { ObjectId } = Schema.Types;

export interface INotification extends Document {
  orgId: mongoose.Types.ObjectId;
  type: string;
  severity: "info" | "warning" | "error" | "critical";
  status: "Read" | "Unread" | "Archived";
  origin: "Connector Service" | "Indexing Service" | "AI Service" | "External Service" | "PipesHub";
  initiator?: mongoose.Types.ObjectId;
  externalInitiator?: string;
  assignedTo: mongoose.Types.ObjectId;
  payload?: INotificationPayload;
  isDeleted: boolean;
  deletedBy?: mongoose.Types.ObjectId;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface INotificationPayload {
  title: string;
  message: string;
  connectorId: string;
  connectorName: string;
  errorCode?: string;
  redirectLink?: string;
}

const notificationPayloadSchema = new Schema<INotificationPayload>(
  {
    title: {
      type: String,
      required: [true, "Notification payload title is required"],
    },
    message: {
      type: String,
      required: [true, "Notification payload message is required"],
    },
    connectorId: {
      type: String,
      required: [true, "Connector ID is required"],
    },
    connectorName: {
      type: String,
      required: [true, "Connector name is required"],
    },
    errorCode: {
      type: String,
      required: false,
    },
    redirectLink: {
      type: String,
      required: false,
    },
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
      enum: ["Read", "Unread", "Archived"],
      default: "Unread",
    },
    origin: {
      type: String,
      enum: ["Connector Service", "Indexing Service", "AI Service", "External Service", "PipesHub"],
      default: "Connector Service",
    },
    initiator: {
      type: ObjectId,
      required: false,
    },
    externalInitiator: {
      type: String,
      validate: {
        validator: (v: string | undefined) => !v || /\S+@\S+\.\S+/.test(v),
        message: "Invalid email format",
      },
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
notificationSchema.index({ assignedTo: 1, isDeleted: 1 });

export const Notifications: Model<INotification> = mongoose.model<INotification>("Notifications", notificationSchema);
