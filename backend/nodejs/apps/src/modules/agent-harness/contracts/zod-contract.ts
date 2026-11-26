import { z } from 'zod';
import type { z as z3 } from 'zod/v3';

/**
 * A contract schema inside a request schema.
 *
 * The generated contract schemas, and the domain schemas built from them, use the zod 3 API (`zod/v3`); the
 * request schemas use zod 4, which the platform's `ValidationMiddleware` and `ValidationUtils` read. A zod 4
 * object cannot hold a zod 3 schema, so a request schema takes the contract through here: the contract parses
 * the value, and each of its issues is reported at its own path with its own message. The key is optional to zod 4
 * so that the contract alone decides whether it may be absent (and fills its defaults).
 */
export function contract<Output>(
  schema: z3.ZodType<Output, z3.ZodTypeDef, unknown>,
): z.ZodType<Output> {
  return z
    .unknown()
    .optional()
    .transform((value, ctx): Output => {
      const parsed = schema.safeParse(value);
      if (parsed.success) return parsed.data;
      for (const issue of parsed.error.issues) {
        ctx.addIssue({
          code: 'custom',
          message: issue.message,
          path: issue.path,
          // Not aborting: a union still reports this option's own issues, as zod 3 did, not just "no option matched".
          continue: true,
        });
      }
      return z.NEVER;
    });
}
