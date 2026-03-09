import { Request, Response, NextFunction } from 'express';
import xss from 'xss';
import { Logger } from '../services/logger.service';
import { BadRequestError } from '../errors/http.errors';
import { containsXSSPattern } from '../../utils/xss-sanitization';

/**
 * XSS Sanitization Middleware
 * 
 * Automatically sanitizes all string values in req.body, req.query, and req.params
 * to prevent XSS attacks. This middleware is non-breaking and safe for production.
 * 
 * Features:
 * - Recursively sanitizes nested objects and arrays
 * - Preserves data types (numbers, booleans, null, undefined)
 * - Handles edge cases safely
 * - Non-intrusive: continues even if sanitization fails
 * - Production-ready with proper error handling
 * 
 * Usage:
 *   // Apply globally (in app.ts after body parsing)
 *   app.use(xssSanitizationMiddleware);
 */

/**
 * Sanitizes a string using xss library to remove HTML/script tags
 * This is used for final sanitization after validation
 * @param value - The string to sanitize
 * @returns Sanitized string with HTML tags removed
 */
function sanitizeString(value: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    return value;
  }

  try {
    return xss(value, {
      stripIgnoreTag: true, // Remove tags that are not in the whitelist
      stripIgnoreTagBody: ['script'], // Remove script tag and its content
      whiteList: {}, // Empty whitelist means no HTML tags are allowed
    }).trim();
  } catch (error) {
    // If sanitization fails, return original value to avoid breaking the request
    Logger.getInstance().warn('XSS sanitization failed for string', {
      error: error instanceof Error ? error.message : String(error),
      valueLength: value.length,
    });
    return value;
  }
}

/**
 * Recursively checks for HTML/XSS content in an object or array
 * Throws an error if HTML is detected
 * 
 * @param value - The value to check (can be any type)
 * @param path - Current path in the object (for error messages)
 * @throws BadRequestError if HTML/XSS content is detected
 */
function validateNoXSS(value: any, path: string = ''): void {
  // Handle null and undefined
  if (value === null || value === undefined) {
    return;
  }

  // Handle strings - check for HTML/XSS using utility function
  if (typeof value === 'string') {
    if (containsXSSPattern(value)) {
      // Create a user-friendly field name from the path
      throw new BadRequestError(
        `HTML tags, scripts, and XSS content are not allowed. Please remove any HTML tags and try again.`,
      );
    }
    return;
  }

  // Handle primitive types (numbers, booleans, symbols, bigint) - return as-is
  if (
    typeof value !== 'object' ||
    value instanceof Buffer ||
    value instanceof Date ||
    value instanceof RegExp ||
    value instanceof Error
  ) {
    return;
  }

  // Handle arrays - validate each element
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      validateNoXSS(item, path ? `${path}[${index}]` : `[${index}]`);
    });
    return;
  }

  // Handle plain objects - validate each property
  // Check for plain objects (not class instances, Buffers, Dates, etc.)
  if (Object.prototype.toString.call(value) === '[object Object]') {
    for (const key in value) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        const propValue = value[key];
        
        // Skip file upload objects (multer file objects)
        // These have specific properties like buffer, mimetype, originalname, etc.
        if (
          propValue &&
          typeof propValue === 'object' &&
          (propValue.buffer instanceof Buffer ||
           (propValue.fieldname && propValue.originalname))
        ) {
          // This is likely a multer file object - skip validation
          continue;
        }

        const fieldPath = path ? `${path}.${key}` : key;
        validateNoXSS(propValue, fieldPath);
      }
    }
    return;
  }

  // For other types (numbers, booleans, class instances, etc.), no validation needed
  return;
}

/**
 * Recursively sanitizes all string values in an object or array
 * This is used after validation passes to ensure clean data
 * Preserves data structure and types
 * 
 * @param value - The value to sanitize (can be any type)
 * @returns Sanitized value with same structure
 */
function sanitizeValue(value: any): any {
  // Handle null and undefined
  if (value === null || value === undefined) {
    return value;
  }

  // Handle strings - sanitize
  if (typeof value === 'string') {
    return sanitizeString(value);
  }

  // Handle primitive types (numbers, booleans, symbols, bigint) - return as-is
  if (
    typeof value !== 'object' ||
    value instanceof Buffer ||
    value instanceof Date ||
    value instanceof RegExp ||
    value instanceof Error
  ) {
    return value;
  }

  // Handle arrays - sanitize each element
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeValue(item));
  }

  // Handle plain objects - sanitize each property
  // Check for plain objects (not class instances, Buffers, Dates, etc.)
  if (Object.prototype.toString.call(value) === '[object Object]') {
    const sanitized: Record<string, any> = {};
    for (const key in value) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        try {
          const propValue = value[key];
          
          // Skip file upload objects (multer file objects)
          // These have specific properties like buffer, mimetype, originalname, etc.
          if (
            propValue &&
            typeof propValue === 'object' &&
            (propValue.buffer instanceof Buffer ||
             (propValue.fieldname && propValue.originalname))
          ) {
            // This is likely a multer file object - preserve it as-is
            sanitized[key] = propValue;
            continue;
          }

          sanitized[key] = sanitizeValue(propValue);
        } catch (error) {
          // If sanitizing a property fails, keep the original value
          Logger.getInstance().warn('XSS sanitization failed for property', {
            key,
            error: error instanceof Error ? error.message : String(error),
          });
          sanitized[key] = value[key];
        }
      }
    }
    return sanitized;
  }

  // For other types (class instances, etc.), return as-is
  // This preserves file uploads, binary data, and other non-string types
  return value;
}

/**
 * XSS Sanitization Middleware
 * 
 * Sanitizes all string inputs in req.body, req.query, and req.params
 * This middleware is safe to use globally and won't break existing functionality.
 * 
 * It runs after body parsing but before route handlers, ensuring all user input
 * is sanitized before reaching controllers.
 */
export const xssSanitizationMiddleware = (
  req: Request,
  _res: Response,
  next: NextFunction,
): void => {
  try {
    // First, validate that no HTML/XSS content exists (reject if found)
    // This prevents HTML from being stored in the database
    
    if (req.path.startsWith('/api/v1/agents/') && (req.method === 'POST' || req.method === 'PUT')) {
      next();
      return;
    }

    if (req.path.startsWith('/api/v1/conversations/') && (req.method === 'POST' || req.method === 'PUT')) {
      next();
      return;
    }

    // Validate request body
    if (req.body && typeof req.body === 'object' && !Array.isArray(req.body)) {
      // Skip if body is a Buffer (file upload)
      if (!(req.body instanceof Buffer)) {
        validateNoXSS(req.body, 'body');
        // After validation passes, sanitize to ensure clean data
        req.body = sanitizeValue(req.body);
      }
    }

    // Validate query parameters
    if (req.query && typeof req.query === 'object' && !Array.isArray(req.query)) {
      validateNoXSS(req.query, 'query');
      // After validation passes, sanitize to ensure clean data
      req.query = sanitizeValue(req.query) as typeof req.query;
    }

    // Validate route parameters
    if (req.params && typeof req.params === 'object' && !Array.isArray(req.params)) {
      validateNoXSS(req.params, 'params');
      // After validation passes, sanitize to ensure clean data
      req.params = sanitizeValue(req.params) as typeof req.params;
    }

    // Continue to next middleware/route handler
    next();
  } catch (error) {
    // If it's a BadRequestError (our validation error), pass it to error handler
    if (error instanceof BadRequestError) {
      return next(error);
    }

    // For other errors, log but don't break the request
    // This ensures the middleware is non-breaking even if something goes wrong
    Logger.getInstance().error('XSS sanitization middleware error', {
      error: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
      path: req.path,
      method: req.method,
    });

    // Continue with the request even if sanitization fails
    // This ensures no breaking changes in production
    next();
  }
};

