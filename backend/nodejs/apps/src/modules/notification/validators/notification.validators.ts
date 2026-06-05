import { z } from 'zod';

const OBJECT_ID_REGEX = /^[0-9a-fA-F]{24}$/;

const objectId = (label: string) =>
  z.string().regex(OBJECT_ID_REGEX, { message: `Invalid ${label}` });

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

/** `PATCH /:id/archive` */
export const archiveNotificationSchema = notificationIdParams;

/** `PATCH /:id/unarchive` */
export const unarchiveNotificationSchema = notificationIdParams;

/** `DELETE /:id` */
export const deleteNotificationSchema = notificationIdParams;
