import mongoose, { Document, Schema, Model } from "mongoose";

const { ObjectId } = Schema.Types;

export interface INotification extends Document {
  title: string;
  orgId: mongoose.Types.ObjectId;
  type: string;
  link: string;
  status: "Read" | "Unread" | "Archived";
  origin: "Internal Service" | "External Service" | "PipesHub";
  initiator?: mongoose.Types.ObjectId;
  externalInitiator?: string;
  assignedTo: mongoose.Types.ObjectId;
  appName?: string;
  appId?: string;
  payload?: Record<string, any>;
  isDeleted: boolean;
  deletedBy?: mongoose.Types.ObjectId;
  createdAt?: Date;
  updatedAt?: Date;
}

const notificationSchema = new Schema<INotification>(
  {
    title: {
      type: String,
      required: [true, "Notification title is required"],
    },
    orgId: {
      type: ObjectId,
      required: [true, "Organization ID is required"],
    },
    type: {
      type: String,
      required: [true, "Notification type is required"],
    },
    link: {
      type: String,
      required: [true, "Link is required"],
    },
    status: {
      type: String,
      enum: ["Read", "Unread", "Archived"],
      default: "Unread",
    },
    origin: {
      type: String,
      enum: ["Internal Service", "External Service", "PipesHub"],
      default: "Internal Service",
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
    appName: {
      type: String,
      required: false,
    },
    appId: {
      type: String,
      required: false,
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
notificationSchema.index({ assignedTo: 1, isDeleted: 1 });

export const Notifications: Model<INotification> = mongoose.model<INotification>("Notifications", notificationSchema);
