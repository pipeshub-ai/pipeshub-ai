import { z } from 'zod';
import { Request, Response, NextFunction } from 'express';

import { Logger } from '../services/logger.service';
import { ValidationUtils } from '../utils/validation.utils';
import { ValidationError } from '../errors/validation.error';
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

export class ValidationMiddleware {
  private static logger = Logger.getInstance();

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
          const message = describeGuardViolation(violation);
          const errors = [
            {
              field: violation.path,
              message,
              code: violation.reason === 'too_deep' ? 'TOO_DEEP' : 'TOO_BIG',
              value: '',
            },
          ];
          this.logger.error('Validation failed', {
            path: req.path,
            method: req.method,
            errors,
          });
          next(
            new ValidationError(
              ValidationUtils.formatValidationErrorMessage(errors),
              errors,
            ),
          );
          return;
        }

        // If stripUnknown is true, we modify the schema to strip unknown fields
        const finalSchema = options.stripUnknown
          ? schema.strip()
          : schema.loose();

        // Validate request data against schema, now including headers
        // reportInput keeps the offending value on each issue so the messages
        // and codes can tell a missing value from a wrong one.
        const validatedData = await finalSchema.parseAsync(
          {
            body: req.body,
            query: req.query,
            params: req.params,
            headers: req.headers, // Add headers validation
          },
          { reportInput: true },
        );

        // Replace request properties with validated data
        req.body = validatedData.body;
        req.query = validatedData.query as Request['query'];
        req.params = validatedData.params as Request['params'];
        // Note: req.headers is read-only, so we can't replace it
        // but we can still validate it

        this.logger.debug('Validation successful', {
          path: req.path,
          method: req.method,
        });

        next();
      } catch (error) {
        if (error instanceof RangeError && /call stack/i.test(error.message)) {
          // Safety net: zod overflows the stack once a request produces about a
          // million issues. The guard above should stop that first.
          const errors = [
            {
              field: '',
              message: 'The request holds too many invalid values to check.',
              code: 'TOO_BIG',
              value: '',
            },
          ];
          this.logger.error('Validation failed', {
            path: req.path,
            method: req.method,
            errors,
          });
          next(
            new ValidationError(
              ValidationUtils.formatValidationErrorMessage(errors),
              errors,
            ),
          );
          return;
        }
        if (error instanceof z.ZodError) {
          const errors = ValidationUtils.formatZodError(error);
          const validationError = new ValidationError(
            ValidationUtils.formatValidationErrorMessage(errors),
            errors,
          );

          this.logger.error('Validation failed', {
            path: req.path,
            method: req.method,
            errors,
          });

          next(validationError);
        } else {
          next(error);
        }
      }
    };
  }
}
