import { z } from 'zod';

/**
 Zod validator for the server-driven Local FS sync.
 */
export const LocalFsPullEventsSchema = z.object({
  body: z.object({
    connectorId: z.string().min(1),
    runId: z.string().min(1),
    batchIndex: z.number().int().min(0),
    mode: z.enum(['FULL', 'INCREMENTAL']),
    cursor: z.string().nullable().optional(),
    maxEvents: z.number().int().min(1).max(1000),
    timeoutMs: z.number().int().min(1000).max(300_000),
  }),
});

export const LocalFsFetchContentSchema = z.object({
  body: z.object({
    connectorId: z.string().min(1),
    relPath: z.string().min(1),
    externalRecordId: z.string().min(1),
    sha256: z.string().nullable().optional(),
    timeoutMs: z.number().int().min(1000).max(300_000),
  }),
});