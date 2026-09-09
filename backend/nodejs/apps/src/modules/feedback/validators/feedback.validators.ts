import { z } from 'zod';
import {
  feedbackKinds,
  feedbackMimeTypes,
  MAX_FEEDBACK_ATTACHMENTS,
  MAX_FEEDBACK_ATTACHMENT_BYTES,
  MAX_FEEDBACK_ATTACHMENTS_TOTAL_BYTES,
} from '../schema/feedback.schema';

const fileBufferSchema = z
  .object({
    buffer: z.instanceof(Buffer),
    originalname: z.string().min(1),
    mimetype: z
      .string()
      .transform((value) => (value === 'image/jpg' ? 'image/jpeg' : value))
      .pipe(z.enum(feedbackMimeTypes)),
  })
  .transform((file) => ({
    ...file,
    size: file.buffer.length,
  }))
  .superRefine((file, ctx) => {
    if (file.size < 1 || file.size > MAX_FEEDBACK_ATTACHMENT_BYTES) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Attachment exceeds the per-file size limit',
        path: ['buffer'],
      });
    }
  });

export const createFeedbackSchema = z.object({
  body: z
    .object({
      kind: z.enum(feedbackKinds),
      description: z.string().trim().min(10).max(5000),
      fileBuffers: z.array(fileBufferSchema).max(MAX_FEEDBACK_ATTACHMENTS).optional(),
      fileBuffer: fileBufferSchema.optional(),
    })
    .superRefine((body, ctx) => {
      const files = body.fileBuffers?.length
        ? body.fileBuffers
        : body.fileBuffer
          ? [body.fileBuffer]
          : [];
      const total = files.reduce((sum, file) => sum + file.size, 0);
      if (total > MAX_FEEDBACK_ATTACHMENTS_TOTAL_BYTES) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'Total attachment size exceeds the limit',
          path: ['fileBuffers'],
        });
      }
    }),
});
