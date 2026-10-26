import { z } from 'zod';
import { ValidationErrorDetail } from '../types/validation.types';

type ZodError = z.ZodError;
type ZodIssue = z.core.$ZodIssue;

const englishLocale = z.locales.en();

/**
 * zod 4 error option for a schema whose message should only replace the stock
 * "required" wording, i.e. what zod 3 called `required_error`. Any other
 * failure (wrong type, ...) keeps the formatted default.
 */
type IssueMessageOption = {
  error: (issue: { input?: unknown }) => string | undefined;
};

export const messageWhenMissing = (message: string): IssueMessageOption => ({
  error: (issue: { input?: unknown }): string | undefined =>
    issue.input === undefined ? message : undefined,
});

/**
 * The opposite split, zod 3's `invalid_type_error`: the message applies once a
 * value was supplied; a missing value keeps the formatted "is required" wording.
 */
export const messageWhenPresent = (message: string): IssueMessageOption => ({
  error: (issue: { input?: unknown }): string | undefined =>
    issue.input === undefined ? undefined : message,
});

const ZOD3_FORMAT_WORDING = new Set([
  'Invalid email',
  'Invalid url',
  'Invalid uuid',
  'Invalid datetime',
]);

/**
 * For an enum with a custom "must be one of" text: it applies to a string that
 * is not an option; a missing or non-string value keeps the "is required" /
 * "must be text" wording.
 */
export const messageForUnknownOption = (
  message: string,
): IssueMessageOption => ({
  error: (issue: { input?: unknown }): string | undefined =>
    typeof issue.input === 'string' ? message : undefined,
});

export class ValidationUtils {
  static formatZodError(error: ZodError): ValidationErrorDetail[] {
    return this.withoutFollowUps(
      error.issues.map((issue) => this.normalize(issue)),
    ).map((issue) => this.formatZodIssue(issue));
  }

  /** User-facing summary from formatted Zod issues (used as ValidationError.message). */
  static formatValidationErrorMessage(errors: ValidationErrorDetail[]): string {
    const messages = errors
      .map((issue) => issue.message?.trim())
      .filter((message): message is string => Boolean(message));

    if (messages.length === 0) {
      return "Some of the information sent isn't valid. Check the form and try again.";
    }

    return messages.join('\n');
  }

  /**
   * zod 4 reports a missing or non-string value for an enum as `invalid_value`;
   * zod 3 (and every client) saw a wrong-type error: "X is required." or
   * "X must be text.". The offending value is on the issue when the parse ran
   * with `reportInput`. (A literal given the wrong type reads the same way.)
   */
  private static normalize(issue: ZodIssue): ZodIssue {
    if (issue.code !== 'invalid_value' || !('input' in issue)) return issue;
    if (!issue.values.every((value) => typeof value === 'string')) return issue;
    // A literal with its own message keeps reading as a literal (zod 3 raised
    // invalid_literal for a missing value there); an enum with its own message
    // lists several values.
    if (issue.values.length === 1 && !this.isStockMessage(issue)) return issue;

    const input: unknown = issue.input;
    if (typeof input === 'string') return issue;

    const received = input === null ? 'null' : typeof input;
    const normalized: z.core.$ZodIssueInvalidType = {
      code: 'invalid_type',
      expected: issue.values
        .map((value) => `'${typeof value === 'string' ? value : ''}'`)
        .join(' | '),
      path: issue.path,
      message: this.isStockMessage(issue)
        ? `Invalid input: expected string, received ${received}`
        : issue.message,
      input,
    };
    return normalized;
  }

  /**
   * zod 3 stopped checking a value once its type was wrong; zod 4 keeps going
   * and adds "can't be empty" style lines after a type error, and can repeat
   * the same line. Keep the first of each and drop checks that follow a type
   * error on the same field.
   */
  private static withoutFollowUps(issues: ZodIssue[]): ZodIssue[] {
    const typeFailed = new Set<string>();
    for (const issue of issues) {
      // A fraction sent to a whole-number field is reported next to the other checks, as before.
      if (issue.code === 'invalid_type' && issue.expected !== 'int')
        typeFailed.add(issue.path.map(String).join('\u0000'));
    }
    const seen = new Set<string>();
    return issues.filter((issue) => {
      const path = issue.path.map(String).join('\u0000');
      const followsTypeError =
        issue.code !== 'invalid_type' &&
        (issue.code === 'too_small' ||
          issue.code === 'too_big' ||
          issue.code === 'invalid_format' ||
          issue.code === 'not_multiple_of') &&
        typeFailed.has(path);
      if (followsTypeError) return false;
      const key = `${issue.code}\u0001${path}\u0001${issue.message}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  private static formatZodIssue(issue: ZodIssue): ValidationErrorDetail {
    return {
      field: issue.path.map(String).join('.'),
      message: this.friendlyMessage(issue),
      code: this.getErrorCode(issue),
      value: '',
    };
  }

  /**
   * True when the message is zod's own English wording rather than text a
   * schema supplied. A wrong-type issue only carries the received type inside
   * its message (the input is not kept on the issue), so it is matched by shape.
   */
  private static isStockMessage(issue: ZodIssue): boolean {
    if (issue.code === 'invalid_type') {
      return /^Invalid input: expected .+, received \S+$/.test(issue.message);
    }
    // zod 4 words an unmatched discriminator itself; no schema here sets that text.
    if (issue.code === 'invalid_union') return true;
    // The wording zod 3 used for formats, which some schemas repeat as their own message.
    if (
      issue.code === 'invalid_format' &&
      ZOD3_FORMAT_WORDING.has(issue.message)
    ) {
      return true;
    }
    const stock = englishLocale.localeError(
      issue as unknown as Parameters<typeof englishLocale.localeError>[0],
    );
    const text = typeof stock === 'string' ? stock : stock?.message;
    return issue.message === text;
  }

  /**
   * z.literal and z.enum both report `invalid_value`; the issue keeps no trace of
   * which one made it. With zod's own wording the text tells them apart
   * ("Invalid option: ..." is an enum). A schema that supplied the message is
   * read as an enum when it says "must be one of" (the wording every
   * single-value enum in this app uses) and as a literal otherwise.
   */
  private static isLiteralIssue(issue: ZodIssue): boolean {
    if (issue.code !== 'invalid_value') return false;
    if (this.isStockMessage(issue)) {
      return !issue.message.startsWith('Invalid option');
    }
    return issue.values.length === 1 && !/must be one of/i.test(issue.message);
  }

  /** A Date object holding NaN: zod 3 called it `invalid_date`, zod 4 a wrong type "received Date". */
  private static isInvalidDate(issue: ZodIssue): boolean {
    return (
      issue.code === 'invalid_type' &&
      issue.expected === 'date' &&
      issue.message.endsWith('received Date')
    );
  }

  /** Zod's stock wording names no field; schemas' own messages are kept as written. */
  private static friendlyMessage(issue: ZodIssue): string {
    if (!this.isStockMessage(issue)) return issue.message;

    const label = this.fieldLabel(issue.path);
    switch (issue.code) {
      case 'invalid_type': {
        const received = /received (\S+)$/.exec(issue.message)?.[1];
        if (received === 'undefined' || received === 'null') {
          return `${label} is required.`;
        }
        if (this.isInvalidDate(issue)) return `${label} isn't valid.`;
        return `${label} must be ${this.describeType(issue.expected)}.`;
      }
      case 'too_small':
        if (issue.origin === 'string') {
          return Number(issue.minimum) <= 1
            ? `${label} can't be empty.`
            : `${label} must be at least ${String(issue.minimum)} characters.`;
        }
        if (issue.origin === 'array' || issue.origin === 'set') {
          return `Add at least ${String(issue.minimum)} ${Number(issue.minimum) === 1 ? 'item' : 'items'} to ${label.toLowerCase()}.`;
        }
        return `${label} must be at least ${String(issue.minimum)}.`;
      case 'too_big':
        if (issue.origin === 'string')
          return `${label} must be at most ${String(issue.maximum)} characters.`;
        if (issue.origin === 'array' || issue.origin === 'set') {
          return `${label} can have at most ${String(issue.maximum)} ${Number(issue.maximum) === 1 ? 'item' : 'items'}.`;
        }
        return `${label} must be at most ${String(issue.maximum)}.`;
      case 'invalid_format':
        if (issue.format === 'email')
          return `${label} must be a valid email address.`;
        if (issue.format === 'url')
          return `${label} must be a valid web address (URL).`;
        return `${label} isn't in the right format.`;
      case 'invalid_value':
        if (this.isLiteralIssue(issue)) return `${label} isn't valid.`;
        return `${label} must be one of: ${issue.values.join(', ')}.`;
      case 'unrecognized_keys':
        return `These fields aren't accepted here: ${issue.keys.join(', ')}.`;
      default:
        return `${label} isn't valid.`;
    }
  }

  /** `body.pageSize` → "Page size"; the request part the middleware validates is not a field name. */
  private static fieldLabel(path: PropertyKey[]): string {
    const rest = ['body', 'query', 'params', 'headers'].includes(
      String(path[0]),
    )
      ? path.slice(1)
      : path;
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
      int: 'a whole number',
      record: 'a group of fields',
      map: 'a group of fields',
      boolean: 'true or false',
      array: 'a list',
      object: 'a group of fields',
      date: 'a date',
    };
    return names[expected] ?? `a ${expected}`;
  }

  private static getErrorCode(issue: ZodIssue): string {
    if (issue.code === 'invalid_value') {
      return this.isLiteralIssue(issue) ? 'INVALID_LITERAL' : 'INVALID_ENUM';
    }

    if (this.isInvalidDate(issue)) return 'INVALID_DATE';
    if (
      issue.code === 'invalid_union' &&
      'note' in issue &&
      issue.note === 'No matching discriminator'
    ) {
      return 'INVALID_DISCRIMINATOR';
    }

    const codeMap: Record<string, string> = {
      invalid_type: 'INVALID_TYPE',
      invalid_union: 'INVALID_UNION',
      invalid_format: 'INVALID_STRING',
      too_small: 'TOO_SMALL',
      too_big: 'TOO_BIG',
      custom: 'CUSTOM',
      not_multiple_of: 'NOT_MULTIPLE_OF',
    };

    return codeMap[issue.code] ?? 'VALIDATION_ERROR';
  }
}
