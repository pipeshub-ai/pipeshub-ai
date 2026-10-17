import { ZodError, ZodIssue, ZodIssueCode, defaultErrorMap } from 'zod';
import { ValidationErrorDetail } from '../types/validation.types';

export class ValidationUtils {
  static formatZodError(error: ZodError): ValidationErrorDetail[] {
    return error.errors.map((issue) => this.formatZodIssue(issue));
  }

  /** User-facing summary from formatted Zod issues (used as ValidationError.message). */
  static formatValidationErrorMessage(
    errors: ValidationErrorDetail[],
  ): string {
    const messages = errors
      .map((issue) => issue.message?.trim())
      .filter((message): message is string => Boolean(message));

    if (messages.length === 0) {
      return "Some of the information sent isn't valid. Check the form and try again."
    }

    return messages.join('\n')
  }

  private static formatZodIssue(issue: ZodIssue): ValidationErrorDetail {
    return {
      field: issue.path.join('.'),
      message: this.friendlyMessage(issue),
      code: this.getErrorCode(issue.code),
      value: '',
    };
  }

  /** Zod's stock wording names no field; schemas' own messages are kept as written. */
  private static friendlyMessage(issue: ZodIssue): string {
    const stock = defaultErrorMap(issue, { defaultError: issue.message, data: undefined }).message;
    if (issue.message !== stock) return issue.message;

    const label = this.fieldLabel(issue.path);
    switch (issue.code) {
      case ZodIssueCode.invalid_type:
        if (issue.received === 'undefined' || issue.received === 'null') {
          return `${label} is required.`;
        }
        return `${label} must be ${this.describeType(issue.expected)}.`;
      case ZodIssueCode.too_small:
        if (issue.type === 'string') {
          return Number(issue.minimum) <= 1
            ? `${label} can't be empty.`
            : `${label} must be at least ${issue.minimum} characters.`;
        }
        if (issue.type === 'array' || issue.type === 'set') {
          return `Add at least ${issue.minimum} ${Number(issue.minimum) === 1 ? 'item' : 'items'} to ${label.toLowerCase()}.`;
        }
        return `${label} must be at least ${issue.minimum}.`;
      case ZodIssueCode.too_big:
        if (issue.type === 'string') return `${label} must be at most ${issue.maximum} characters.`;
        if (issue.type === 'array' || issue.type === 'set') {
          return `${label} can have at most ${issue.maximum} ${Number(issue.maximum) === 1 ? 'item' : 'items'}.`;
        }
        return `${label} must be at most ${issue.maximum}.`;
      case ZodIssueCode.invalid_string:
        if (issue.validation === 'email') return `${label} must be a valid email address.`;
        if (issue.validation === 'url') return `${label} must be a valid web address (URL).`;
        return `${label} isn't in the right format.`;
      case ZodIssueCode.invalid_enum_value:
        return `${label} must be one of: ${issue.options.join(', ')}.`;
      case ZodIssueCode.unrecognized_keys:
        return `These fields aren't accepted here: ${issue.keys.join(', ')}.`;
      default:
        return `${label} isn't valid.`;
    }
  }

  /** `body.pageSize` → "Page size"; the request part the middleware validates is not a field name. */
  private static fieldLabel(path: (string | number)[]): string {
    const rest = ['body', 'query', 'params', 'headers'].includes(String(path[0])) ? path.slice(1) : path;
    const named = rest.filter((p): p is string => typeof p === 'string');
    const last = named[named.length - 1];
    if (!last) return 'The request';
    const words = last
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .replace(/[_-]+/g, ' ')
      .trim()
      .toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  private static describeType(expected: string): string {
    const names: Record<string, string> = {
      string: 'text',
      number: 'a number',
      bigint: 'a number',
      integer: 'a whole number',
      boolean: 'true or false',
      array: 'a list',
      object: 'a group of fields',
      date: 'a date',
    };
    return names[expected] ?? `a ${expected}`;
  }

  private static getErrorCode(zodCode: string): string {
    const codeMap: Record<string, string> = {
      invalid_type: 'INVALID_TYPE',
      invalid_literal: 'INVALID_LITERAL',
      invalid_enum_value: 'INVALID_ENUM',
      invalid_union: 'INVALID_UNION',
      invalid_union_discriminator: 'INVALID_DISCRIMINATOR',
      invalid_arguments: 'INVALID_ARGUMENTS',
      invalid_return_type: 'INVALID_RETURN_TYPE',
      invalid_date: 'INVALID_DATE',
      invalid_string: 'INVALID_STRING',
      too_small: 'TOO_SMALL',
      too_big: 'TOO_BIG',
      custom: 'CUSTOM',
      invalid_intersection_types: 'INVALID_INTERSECTION',
      not_multiple_of: 'NOT_MULTIPLE_OF',
      not_finite: 'NOT_FINITE',
    };

    return codeMap[zodCode] || 'VALIDATION_ERROR';
  }
}
