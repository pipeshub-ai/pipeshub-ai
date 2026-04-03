import { z } from 'zod';
import { AuthMethodType } from '../schema/orgAuthConfiguration.schema';

/** GET /authMethods — no body/query/params; session validated by userValidator. */
export const GetAuthMethodsValidationSchema = z.object({
  body: z.object({}),
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

const setUpAuthConfigBody = z.object({
  contactEmail: z.string().email('Invalid email format'),
  registeredName: z.string().min(1, 'Registered name is required'),
  adminFullName: z.string().min(1, 'Admin full name required'),
  sendEmail: z.boolean().optional(),
});

/** POST / — initial org auth setup (create org + default auth config). */
export const SetUpAuthConfigValidationSchema = z.object({
  body: setUpAuthConfigBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

const authMethodSchema = z.object({
  type: z.nativeEnum(AuthMethodType),
});

const authStepSchema = z.object({
  order: z.number(),
  allowedMethods: z
    .array(authMethodSchema)
    .nonempty('At least one method is required')
    .superRefine((methods, ctx) => {
      const methodSet = new Set<string>();
      for (const method of methods) {
        if (methodSet.has(method.type)) {
          ctx.addIssue({
            code: 'custom',
            message: `Duplicate authentication method "${method.type}" in the same step`,
          });
        }
        methodSet.add(method.type);
      }
    }),
});

const authStepsSchema = z
  .array(authStepSchema)
  .min(1, 'At least one authentication step is required')
  .max(3, 'A maximum of 3 authentication steps is allowed')
  .superRefine((steps, ctx) => {
    const orderSet = new Set<number>();
    const globalMethodSet = new Set<string>();

    for (const step of steps) {
      if (orderSet.has(step.order)) {
        ctx.addIssue({
          code: 'custom',
          message: `Duplicate order found: ${step.order}`,
        });
      }
      orderSet.add(step.order);

      for (const method of step.allowedMethods) {
        if (globalMethodSet.has(method.type)) {
          ctx.addIssue({
            code: 'custom',
            message: `Authentication method "${method.type}" is repeated across multiple steps`,
          });
        }
        globalMethodSet.add(method.type);
      }
    }
  });

const authMethodValidationBody = z.object({
  authMethod: authStepsSchema,
});

/** POST /updateAuthMethod — replace org auth steps. */
export const UpdateAuthMethodValidationSchema = z.object({
  body: authMethodValidationBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

/** Allowed method entry in auth step responses (Mongoose may add fields; passthrough keeps them). */
const authMethodEntryResponseSchema = z
  .object({
    type: z.nativeEnum(AuthMethodType),
  })
  .passthrough();

/** Single auth step in GET /authMethods and POST /updateAuthMethod responses. */
export const authStepResponseSchema = z
  .object({
    order: z.number(),
    allowedMethods: z.array(authMethodEntryResponseSchema),
  })
  .passthrough();

/** GET /authMethods — 200 JSON body */
export const GetAuthMethodsResponseSchema = z.object({
  authMethods: z.array(authStepResponseSchema),
});

/** POST /updateAuthMethod — 200 JSON body */
export const UpdateAuthMethodResponseSchema = z.object({
  message: z.string().min(1),
  authMethod: z.array(authStepResponseSchema),
});
