import { AnyZodObject, z } from 'zod';
import { Request, Response, NextFunction } from 'express';

import { Logger } from '../services/logger.service';
import { ValidationUtils } from '../utils/validation.utils';
import { ValidationError } from '../errors/validation.error';

export interface ValidatorOptions {
  stripUnknown?: boolean;
}

export class ValidationMiddleware {
  private static logger = Logger.getInstance();

  // First modify the validation method to include headers
  static validate(schema: AnyZodObject, options: ValidatorOptions = {}) {
    return async (req: Request, _res: Response, next: NextFunction) => {
      try {
        // If stripUnknown is true, we modify the schema to strip unknown fields
        const finalSchema = options.stripUnknown
          ? schema.strip()
          : schema.passthrough();

        // Validate request data against schema, now including headers
        const validatedData = await finalSchema.parseAsync({
          body: req.body,
          query: req.query,
          params: req.params,
          headers: req.headers, // Add headers validation
        });

        // Replace request properties with validated data
        req.body = validatedData.body;
        req.query = validatedData.query;
        req.params = validatedData.params;
        // Note: req.headers is read-only, so we can't replace it
        // but we can still validate it

        this.logger.debug('Validation successful', {
          path: req.path,
          method: req.method,
        });

        next();
      } catch (error) {
        if (error instanceof z.ZodError) {
          const errors = ValidationUtils.formatZodError(error);
          const validationError = new ValidationError(
            'Validation failed',
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
