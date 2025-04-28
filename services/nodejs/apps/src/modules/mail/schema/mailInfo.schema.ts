import mongoose, { Document, Schema, Types } from 'mongoose';

export interface MailInfo extends Document {
  orgId: Types.ObjectId;
  subject: string;
  from: string;
  to: string[];
  cc?: string[];
  emailTemplateType: string;
  createdAt: number;
  updatedAt: number;
}

// Define the schema
const mailSchema = new Schema<MailInfo>(
  {
    orgId: { type: Schema.Types.ObjectId },
    subject: {
      type: String,
    },
    from: {
      type: String,
      required: true,
    },
    to: {
      type: [String],
      required: true,
    },
    cc: {
      type: [String],
    },
    emailTemplateType: {
      type: String,
      required: true,
    },
    createdAt: {
      type: Number,
      default: Date.now,
    },
    updatedAt: {
      type: Number,
      default: Date.now,
    },
  },
  { timestamps: false },
);

// Pre-save hook to update updatedAt timestamp
mailSchema.pre<MailInfo>('save', function (next) {
  if (!this.isNew) {
    this.updatedAt = Date.now();
  }
  next();
});

// Create and export the model
export const MailModel = mongoose.model<MailInfo>(
  'mailInfo',
  mailSchema,
  'mailInfo',
);
