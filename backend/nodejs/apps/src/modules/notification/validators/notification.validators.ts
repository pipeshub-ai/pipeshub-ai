import { z } from 'zod';
import { objectId as objectIdFormat } from '../../../libs/validators/zod-primitives';
import { TIP_IDS } from '../schema/user-notification-preferences.schema';

const objectId = (label: string): z.ZodString =>
  objectIdFormat(label, `Invalid ${label}`);

/** Coerce a query-string value to an integer page-size (1–100, default 20). */
const pageSizeSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().int().min(1).max(100).default(20),
);

/** `GET /` — list notifications */
export const listNotificationsSchema = z.object({
  query: z.object({
    status: z.enum(['read', 'unread', 'archived']).optional(),
    cursor: z.string().optional(),
    limit: pageSizeSchema.optional(),
  }),
});

/** `GET /stats` — no query/body/params required */
export const notificationStatsSchema = z.object({});

/** `PATCH /read-all` — no query/body/params required */
export const markAllReadSchema = z.object({});

/** Shared params schema for routes that operate on a single notification by id */
const notificationIdParams = z.object({
  params: z.object({
    id: objectId('notification id'),
  }),
});

/** `PATCH /:id/read` */
export const markReadSchema = notificationIdParams;

/** `PATCH /:id/unread` */
export const markUnreadSchema = notificationIdParams;

/** `PATCH /:id/archive` */
export const archiveNotificationSchema = notificationIdParams;

/** `PATCH /:id/unarchive` */
export const unarchiveNotificationSchema = notificationIdParams;

/** `DELETE /:id` */
export const deleteNotificationSchema = notificationIdParams;

/** `GET /preferences` */
export const getPreferencesSchema = z.object({});

/** `PATCH /preferences` — a partial update of the boolean switches; unknown keys are rejected. */
export const updatePreferencesSchema = z.object({
  body: z
    .object({
      email: z
        .object({
          chatShared: z.boolean().optional(),
          ownershipTransferred: z.boolean().optional(),
          chatMentioned: z.boolean().optional(),
        })
        .strict()
        .optional(),
      inApp: z
        .object({
          chatActivity: z.boolean().optional(),
          chatMentioned: z.boolean().optional(),
        })
        .strict()
        .optional(),
    })
    .strict()
    .refine(
      ({ email, inApp }) =>
        Object.values(email ?? {}).length + Object.values(inApp ?? {}).length >
        0,
      { message: 'At least one preference is required' },
    ),
});

/** `PUT|DELETE /preferences/muted-sessions/:sessionId` */
export const mutedSessionParamsSchema = z.object({
  params: z.object({
    sessionId: objectId('session id'),
  }),
});

/** `PATCH /preferences/tips` */
export const markTipSeenSchema = z.object({
  body: z.object({ tipId: z.enum(TIP_IDS) }).strict(),
});
