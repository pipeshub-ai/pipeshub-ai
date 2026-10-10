import { z } from 'zod';
import { OBJECT_ID_REGEX } from '../../../libs/validators/zod-primitives';

const prefixedId = (prefix: string): z.ZodEffects<z.ZodString> =>
  z
    .string()
    .refine(
      (v) =>
        v.startsWith(`${prefix}:`) &&
        OBJECT_ID_REGEX.test(v.slice(prefix.length + 1)),
      { message: `Expected ${prefix}:<id>` },
    );

/** The id part of a validated `<type>:<id>` reference. */
export const refId = (ref: string): string => {
  // Query parameters can arrive as arrays; only a single string reference is valid here.
  if (typeof ref !== 'string') throw new TypeError('Expected a single <type>:<id> reference');
  return ref.slice(ref.indexOf(':') + 1);
};

const chatResource = prefixedId('chat');

/** Strict: a client-supplied `teamIds` is refused, never trusted (PH-03 gate note). */
export const explainSchema = z.object({
  query: z
    .object({
      resource: chatResource,
      subject: prefixedId('user').optional(),
    })
    .strict(),
});

export const previewSchema = z.object({
  body: z
    .object({
      resource: chatResource,
      change: z.discriminatedUnion('type', [
        z
          .object({
            type: z.literal('link'),
            projectId: z.string().regex(OBJECT_ID_REGEX),
          })
          .strict(),
        z.object({ type: z.literal('unlink') }).strict(),
        z
          .object({
            type: z.literal('visibility'),
            visibility: z.enum(['private', 'project']),
          })
          .strict(),
      ]),
    })
    .strict(),
});
