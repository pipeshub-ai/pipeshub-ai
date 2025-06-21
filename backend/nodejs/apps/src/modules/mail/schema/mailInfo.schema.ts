import mongoose, { Document, Schema, Types } from 'mongoose';

export interface MailInfo extends Document {
  orgId: Types.ObjectId;
  subject: string;
  from: string;
  to: string[];
  cc?: string[];
  emailTemplateType: string;
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
  },
  { timestamps: true },
);

// Create and export the model
export const MailModel = mongoose.model<MailInfo>(
  'mailInfo',
  mailSchema,
  'mailInfo',
);
