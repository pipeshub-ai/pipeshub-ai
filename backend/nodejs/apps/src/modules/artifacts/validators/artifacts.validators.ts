import { z } from 'zod';

export const GALLERY_ARTIFACT_TYPES = [
  'CODE_OUTPUT',
  'CHART',
  'DOCUMENT',
  'IMAGE',
  'SPREADSHEET',
  'PRESENTATION',
  'DATA_FILE',
  'CODE',
] as const;

export const GALLERY_SORT_FIELDS = [
  'name',
  'createdAtTimestamp',
  'updatedAtTimestamp',
  'artifactType',
] as const;

const artifactTypesQuery = z
  .string()
  .max(200)
  .optional()
  .refine(
    (val) => {
      if (!val) return true;
      return val
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
        .every((item) =>
          (GALLERY_ARTIFACT_TYPES as readonly string[]).includes(item),
        );
    },
    {
      message:
        'artifactTypes must be a comma-separated list of gallery artifact types',
    },
  );

export const listArtifactsSchema = z.object({
  query: z.object({
    page: z
      .string()
      .optional()
      .refine(
        (val) => {
          const parsed = parseInt(val || '1', 10);
          return !isNaN(parsed) && parsed > 0;
        },
        { message: 'Page must be a positive number' },
      ),
    limit: z
      .string()
      .optional()
      .refine(
        (val) => {
          const parsed = parseInt(val || '50', 10);
          return !isNaN(parsed) && parsed > 0 && parsed <= 100;
        },
        { message: 'Limit must be a number between 1 and 100' },
      ),
    search: z.string().max(200).optional(),
    artifactTypes: artifactTypesQuery,
    conversationId: z.string().max(128).optional(),
    dateFrom: z
      .string()
      .optional()
      .refine((val) => !val || (!isNaN(Number(val)) && Number(val) >= 0), {
        message: 'dateFrom must be a non-negative epoch ms value',
      }),
    dateTo: z
      .string()
      .optional()
      .refine((val) => !val || (!isNaN(Number(val)) && Number(val) >= 0), {
        message: 'dateTo must be a non-negative epoch ms value',
      }),
    sortBy: z.enum(GALLERY_SORT_FIELDS).optional(),
    sortOrder: z.enum(['asc', 'desc']).optional(),
  }),
});

const ARTIFACT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

export const artifactIdParamsSchema = z.object({
  params: z.object({
    artifactId: z.string().min(1).max(128).regex(ARTIFACT_ID_PATTERN, {
      message: 'artifactId must be a single path-safe identifier',
    }),
  }),
});
