import { z } from 'zod';
import { Request, Response, NextFunction } from 'express';

import { ValidationUtils } from '../utils/validation.utils';
import { ValidationError } from '../errors/validation.error';
import { BaseError } from '../errors/base.error';
import {
  ArrayGuardViolation,
  findArrayLengthViolation,
} from '../utils/array-length-guard';

export interface ValidatorOptions {
  stripUnknown?: boolean;
  /** Longest array accepted anywhere in body/query/params. Defaults to VALIDATION_MAX_ARRAY_LENGTH. */
  maxArrayLength?: number;
  /** Most keys in one object. Defaults to VALIDATION_MAX_OBJECT_KEYS. */
  maxObjectKeys?: number;
  /** Most array elements plus object keys in the whole request. Defaults to VALIDATION_MAX_TOTAL_ITEMS. */
  maxTotalItems?: number;
}

function describeGuardViolation(violation: ArrayGuardViolation): string {
  const limit = String(violation.limit);
  switch (violation.reason) {
    case 'array_too_long':
      return `${violation.path} can have at most ${limit} items.`;
    case 'too_many_keys':
      return `${violation.path} can have at most ${limit} fields.`;
    case 'too_many_items':
      return `The request holds too many items in total (the limit is ${limit}); the limit is reached at ${violation.path}.`;
    case 'too_deep':
      return `${violation.path} is nested more than ${limit} levels deep.`;
  }
}

/** A refusal the middleware words itself: no zod issue to format, and never the value sent. */
function refusal(
  field: string,
  message: string,
  code: string,
): ValidationError {
  const errors = [{ field, message, code, value: '' }];
  return new ValidationError(
    ValidationUtils.formatValidationErrorMessage(errors),
    errors,
  );
}

export const UNREADABLE_REQUEST_MESSAGE =
  "A value in the request can't be read. Check that each field has the type the API expects.";

/** Parses the request; every throw from the parse leaves as a classified error. */
async function parseRequest(
  schema: z.ZodObject,
  req: Request,
): Promise<z.output<z.ZodObject>> {
  try {
    // reportInput keeps the offending value on each issue so the messages
    // and codes can tell a missing value from a wrong one.
    return await schema.parseAsync(
      {
        body: req.body,
        query: req.query,
        params: req.params,
        headers: req.headers,
      },
      { reportInput: true },
    );
  } catch (error) {
    if (error instanceof z.ZodError) {
      const errors = ValidationUtils.formatZodError(error);
      throw new ValidationError(
        ValidationUtils.formatValidationErrorMessage(errors),
        errors,
      );
    }
    if (error instanceof RangeError && /call stack/i.test(error.message)) {
      // Safety net: zod overflows the stack once a request produces about a
      // million issues. The array guard should stop that first.
      throw refusal(
        '',
        'The request holds too many invalid values to check.',
        'TOO_BIG',
      );
    }
    // Our own classified error keeps its status (a 503 stays a 503).
    if (error instanceof BaseError) throw error;
    // A schema callback threw on the value sent: zod lets such throws through.
    // The text is fixed; the thrown message could quote the value.
    throw refusal('', UNREADABLE_REQUEST_MESSAGE, 'VALIDATION_ERROR');
  }
}

export class ValidationMiddleware {
  // First modify the validation method to include headers
  static validate(schema: z.ZodObject, options: ValidatorOptions = {}) {
    return async (req: Request, _res: Response, next: NextFunction) => {
      try {
        // Refuse oversized arrays before any schema walks them: a schema with no
        // `.max()` would record one issue per failing element.
        const violation = findArrayLengthViolation(
          { body: req.body, query: req.query, params: req.params },
          {
            ...(options.maxArrayLength === undefined
              ? {}
              : { maxArrayLength: options.maxArrayLength }),
            ...(options.maxObjectKeys === undefined
              ? {}
              : { maxObjectKeys: options.maxObjectKeys }),
            ...(options.maxTotalItems === undefined
              ? {}
              : { maxTotalItems: options.maxTotalItems }),
          },
        );
        if (violation) {
          next(
            refusal(
              violation.path,
              describeGuardViolation(violation),
              violation.reason === 'too_deep' ? 'TOO_DEEP' : 'TOO_BIG',
            ),
          );
          return;
        }

        // If stripUnknown is true, we modify the schema to strip unknown fields
        const finalSchema = options.stripUnknown
          ? schema.strip()
          : schema.loose();

        const validatedData = await parseRequest(finalSchema, req);

        // Replace request properties with validated data
        req.body = validatedData.body;
        req.query = validatedData.query as Request['query'];
        req.params = validatedData.params as Request['params'];
        // Note: req.headers is read-only, so we can't replace it
        // but we can still validate it

        next();
      } catch (error) {
        // Express 4 does not catch a rejected promise from an async handler.
        next(error);
      }
    };
  }
}
