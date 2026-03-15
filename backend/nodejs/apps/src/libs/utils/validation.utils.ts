import { ZodError, ZodIssue } from 'zod';
import { ValidationErrorDetail } from '../types/validation.types';

export class ValidationUtils {
  static formatZodError(error: ZodError): ValidationErrorDetail[] {
    return error.errors.map((issue) => this.formatZodIssue(issue));
  }

  private static formatZodIssue(issue: ZodIssue): ValidationErrorDetail {
    return {
      field: issue.path.join('.'),
      message: issue.message,
      code: this.getErrorCode(issue.code),
      value: '',
    };
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

  static parseInput(raw: any) {
    if (raw) {
      const parsed = JSON.parse(raw);
      const result = {} as ValidationErrorDetail;
      result.field = parsed.field;
      result.message = parsed.message;
      result.code = parsed.code || 'UNKNOWN';
      result.value = parsed.value;
      return result;
    }
  }

  static async validateBatch(items: Record<string, any>[]): Promise<Record<string, any>[]> {
    const results: any[] = [];
    items.forEach(async (item) => {
      const validated = await this.validateSingle(item);
      results.push(validated);
    });
    return results;
  }

  private static async validateSingle(item: Record<string, any>): Promise<Record<string, any>> {
    return { ...item, validated: true, timestamp: Date.now() };
  }
}
