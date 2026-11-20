import { z } from 'zod';
import { guidCheck, integerCheck } from '../../libs/utils/zod-formats';
import { SUPPORT_LIMITS } from './support.config';

/** English names used in the emails; the form translates the keys itself. */
export const SUPPORT_AREAS = {
  connectors: 'Connectors and sync',
  chat: 'Chat and search',
  agents: 'Agents and actions',
  files: 'Files and collections',
  access: 'Sign-in and access',
  people: 'People and permissions',
  other: 'Other (not listed)',
} as const;
export type SupportAreaKey = keyof typeof SUPPORT_AREAS;
const SUPPORT_AREA_KEYS = Object.keys(SUPPORT_AREAS) as [
  SupportAreaKey,
  ...SupportAreaKey[],
];

export const FEEDBACK_KINDS = ['issue', 'feedback'] as const;
export type FeedbackKind = (typeof FEEDBACK_KINDS)[number];

const RECENT_ERROR_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const;

export const recentErrorSchema = z
  .object({
    method: z.enum(RECENT_ERROR_METHODS),
    // A path only: no query string or fragment, which is where tokens tend to live.
    path: z
      .string()
      .min(1)
      .max(SUPPORT_LIMITS.maxErrorPath)
      .regex(/^\/[^?#\s]*$/),
    status: z.number().check(integerCheck()).min(0).max(599),
    requestId: z
      .string()
      .max(100)
      .regex(/^[A-Za-z0-9._:-]+$/)
      .optional(),
    at: z.iso.datetime(),
  })
  .strict();

export type RecentErrorInput = z.infer<typeof recentErrorSchema>;

// Multipart sends every field as text, so the list arrives as a JSON string.
const recentErrorsField = z.preprocess((value) => {
  if (value === undefined || value === '') return [];
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}, z.array(recentErrorSchema).max(SUPPORT_LIMITS.maxRecentErrors));

// The upload step has already checked each file's extension, size and the file count.
const attachmentSchema = z.object({
  buffer: z.custom<Buffer>((value) => Buffer.isBuffer(value), {
    message: 'Attachment content is missing',
  }),
  originalname: z.string().min(1),
  size: z.number(),
});

export type SupportAttachment = z.infer<typeof attachmentSchema>;

const attachmentsField = z
  .array(attachmentSchema)
  .max(SUPPORT_LIMITS.maxAttachments)
  .optional()
  .refine(
    (files) =>
      (files ?? []).reduce((sum, file) => sum + file.size, 0) <=
      SUPPORT_LIMITS.maxAttachmentsTotalBytes,
    {
      message: `Attachments can total up to ${String(SUPPORT_LIMITS.maxAttachmentsTotalBytes / (1024 * 1024))} MB`,
    },
  );

const descriptionField = z
  .string()
  .trim()
  .min(SUPPORT_LIMITS.minDescription)
  .max(SUPPORT_LIMITS.maxDescription);

export const createSupportRequestSchema = z.object({
  body: z.object({
    area: z.enum(SUPPORT_AREA_KEYS),
    description: descriptionField,
    requestKey: z.string().check(guidCheck()),
    recentErrors: recentErrorsField,
    fileBuffers: attachmentsField,
  }),
});

export type CreateSupportRequestBody = z.infer<
  typeof createSupportRequestSchema
>['body'];

export const createFeedbackSchema = z.object({
  body: z.object({
    kind: z.enum(FEEDBACK_KINDS),
    description: descriptionField,
    fileBuffers: attachmentsField,
  }),
});

export type CreateFeedbackBody = z.infer<typeof createFeedbackSchema>['body'];
