import mongoose, { Document, Schema, Types, Model } from 'mongoose';

export const feedbackKinds = ['issue', 'feedback'] as const;
export type FeedbackKind = (typeof feedbackKinds)[number];

export const feedbackMimeTypes = [
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/gif',
  'application/pdf',
  'text/plain',
  'text/csv',
] as const;
export type FeedbackMimeType = (typeof feedbackMimeTypes)[number];

/** Browsers may send image/jpg; store and validate as image/jpeg. */
export const feedbackUploadMimeTypes: string[] = [...feedbackMimeTypes, 'image/jpg'];

export const feedbackAllowedExtensions = [
  'jpg',
  'jpeg',
  'png',
  'webp',
  'gif',
  'pdf',
  'txt',
  'log',
  'csv',
] as const;

export const feedbackMimeTypeByExtension: Record<string, FeedbackMimeType> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  gif: 'image/gif',
  pdf: 'application/pdf',
  txt: 'text/plain',
  log: 'text/plain',
  csv: 'text/csv',
};

export const MAX_FEEDBACK_ATTACHMENTS = 5;
export const MAX_FEEDBACK_ATTACHMENT_BYTES = 5 * 1024 * 1024;
export const MAX_FEEDBACK_ATTACHMENTS_TOTAL_BYTES = 12 * 1024 * 1024;

export interface IFeedbackAttachment {
  fileName: string;
  mimeType: FeedbackMimeType;
  sizeInBytes: number;
  documentId: Types.ObjectId;
}

export interface IFeedback extends Document {
  kind: FeedbackKind;
  description: string;
  orgId: Types.ObjectId;
  createdBy: Types.ObjectId;
  attachments: IFeedbackAttachment[];
  createdAt?: Date;
  updatedAt?: Date;
}

const attachmentSchema = new Schema<IFeedbackAttachment>(
  {
    fileName: { type: String, required: true, trim: true },
    mimeType: { type: String, required: true, enum: feedbackMimeTypes },
    sizeInBytes: { type: Number, required: true, min: 1 },
    documentId: { type: Schema.Types.ObjectId, ref: 'Document', required: true },
  },
  { _id: false },
);

const feedbackSchema = new Schema<IFeedback>(
  {
    kind: { type: String, enum: feedbackKinds, required: true },
    description: {
      type: String,
      required: true,
      trim: true,
      minlength: 10,
      maxlength: 5000,
    },
    orgId: { type: Schema.Types.ObjectId, ref: 'orgs', required: true },
    createdBy: { type: Schema.Types.ObjectId, ref: 'users', required: true },
    attachments: {
      type: [attachmentSchema],
      default: [],
      validate: [
        {
          validator: (value: IFeedbackAttachment[]) =>
            value.length <= MAX_FEEDBACK_ATTACHMENTS,
          message: `At most ${MAX_FEEDBACK_ATTACHMENTS} attachments are allowed`,
        },
      ],
    },
  },
  { timestamps: true },
);

feedbackSchema.index({ orgId: 1, createdAt: -1 });
feedbackSchema.index({ orgId: 1, createdBy: 1, createdAt: -1 });

export const Feedbacks: Model<IFeedback> =
  (mongoose.models['feedbacks'] as Model<IFeedback>) ||
  mongoose.model<IFeedback>('feedbacks', feedbackSchema, 'feedbacks');
