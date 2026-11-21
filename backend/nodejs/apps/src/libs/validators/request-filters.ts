import { z } from 'zod';
import { guidCheck, integerCheck } from '../utils/zod-formats';
import { redactTokens } from '../../modules/agent-harness/domain/token-redaction';

/**
 * The `filters` a client may send to search and chat: the one schema the
 * enterprise-search routes and the agent harness's run admission both apply,
 * so a durable run is held to the keys a chat turn is.
 */

const appOrKbIdSchema = z.string().check(guidCheck('Must be a valid UUID'));

// Built-in named-entity kinds; mirrors EntityKind in
// backend/python/app/modules/named_entities/domain/kinds.py.
export const NAMED_ENTITY_KINDS = [
  'person',
  'person_type',
  'organization',
  'event',
  'product',
  'skill',
  'location',
  'city',
  'state',
  'country_region',
  'address',
  'date',
  'date_range',
  'date_time',
  'duration',
  'currency',
  'percentage',
  'age',
  'dimension',
  'email',
  'url',
  'phone',
  'ip',
] as const;

const finiteNumber = z.number();

const rangeChecked = <T extends z.ZodRawShape>(
  shape: T,
  low: string,
  high: string,
): z.ZodObject<T, z.core.$strict> =>
  z
    .object(shape)
    .strict()
    .superRefine((value: Record<string, unknown>, ctx) => {
      const start = value[low];
      const end = value[high];
      if (typeof start === 'number' && typeof end === 'number' && start > end) {
        ctx.addIssue({
          code: 'custom',
          message: `${low} must be less than or equal to ${high}`,
        });
      }
    });

// Epoch milliseconds, or an ISO-8601 date / date-time (a date alone is UTC midnight).
const instant = z.union([
  z.number().check(integerCheck()),
  z
    .string()
    .trim()
    .min(1)
    .transform((value, ctx) => {
      const parsed = Date.parse(value);
      if (Number.isNaN(parsed)) {
        ctx.addIssue({
          code: 'custom',
          // Non-fatal, as in zod 3: the union reports this message instead of
          // a generic "isn't valid".
          continue: true,
          message: 'must be epoch milliseconds or an ISO-8601 date',
        });
        return z.NEVER;
      }
      return parsed;
    }),
]);

/**
 * Experimental. Every constraint must hold for the same record: an amount and
 * a date match a record that mentions both, not one entity that is both.
 */
const entityFiltersSchema = z
  .object({
    kinds: z
      .array(z.string().trim().toLowerCase().pipe(z.enum(NAMED_ENTITY_KINDS)))
      .min(1)
      .max(NAMED_ENTITY_KINDS.length)
      .optional(),
    name: z
      .string()
      .trim()
      .min(1, { message: 'name must not be blank' })
      .max(256)
      .optional(),
    entityIds: z
      .array(z.string().check(guidCheck()))
      .min(1)
      .max(100)
      .optional(),
    mentionedDate: rangeChecked(
      { from: instant.optional(), to: instant.optional() },
      'from',
      'to',
    ).optional(),
    amount: rangeChecked(
      {
        min: finiteNumber.optional(),
        max: finiteNumber.optional(),
        currency: z
          .string()
          .trim()
          .toUpperCase()
          .regex(/^[A-Z]{3}$/, {
            message: 'currency must be a three-letter ISO 4217 code',
          })
          .optional(),
      },
      'min',
      'max',
    ).optional(),
    quantity: rangeChecked(
      {
        min: finiteNumber.optional(),
        max: finiteNumber.optional(),
        dimension: z
          .string()
          .trim()
          .toLowerCase()
          .regex(/^[a-z_]{1,32}$/)
          .optional(),
      },
      'min',
      'max',
    ).optional(),
    // Fractions: 0.125 is 12.5%.
    percent: rangeChecked(
      { min: finiteNumber.optional(), max: finiteNumber.optional() },
      'min',
      'max',
    ).optional(),
  })
  .strict();

/** Most ids one request may select, across every filter key. */
const MAX_FILTER_IDS = 200;

const filtersShape = {
  apps: z.array(appOrKbIdSchema).optional(),
  kb: z.array(appOrKbIdSchema).optional(),
  entityFilters: entityFiltersSchema.optional(),
  // Reserved for the typed filter tree that will replace entityFilters.
  where: z
    .unknown()
    .refine((value) => value === undefined, {
      message: 'filters.where is not supported yet',
    })
    .optional(),
  recordGroups: z.array(appOrKbIdSchema).optional(),
  records: z.array(appOrKbIdSchema).optional(),
  recordsExact: z.array(appOrKbIdSchema).optional(),
};

/** Keys a client may send in `filters` (`where` is reserved and always refused). */
export const REQUEST_FILTER_KEYS = Object.keys(filtersShape).filter(
  (key) => key !== 'where',
);

/**
 * Filter object reused across search/message/regenerate flows: whole apps
 * (`apps`, with `kb` as its older name for collections) and selections below
 * app level — record groups (with the groups nested in them), folders or
 * records (with everything under them) and single records — plus `entityFilters`. Classification
 * names (departments, topics...) are internal-only for now. A key outside this list is refused with the allowed list.
 */
export const requestFiltersSchema = z
  .object(filtersShape)
  .loose()
  .superRefine((filters, ctx) => {
    const unknown = Object.keys(filters).filter(
      (key) => !Object.prototype.hasOwnProperty.call(filtersShape, key),
    );
    if (unknown.length > 0) {
      ctx.addIssue({
        code: 'custom',
        // The keys are the client's text, echoed into the response and the logs: a pasted token stays masked (QA-14).
        message: `Unknown filter key(s): ${redactTokens(unknown.join(', '))}. Allowed keys: ${REQUEST_FILTER_KEYS.join(', ')}`,
      });
    }
  })
  .refine(
    (filters) =>
      Object.values(filters).reduce<number>(
        (count, ids) => count + (Array.isArray(ids) ? ids.length : 0),
        0,
      ) <= MAX_FILTER_IDS,
    { message: `At most ${MAX_FILTER_IDS} ids may be selected in one request` },
  )
  .optional();
