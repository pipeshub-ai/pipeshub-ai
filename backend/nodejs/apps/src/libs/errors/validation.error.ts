import { ValidationErrorDetail } from '../types/validation.types';
import { BaseError } from './base.error';

export class ValidationError extends BaseError {
  public readonly errors: ValidationErrorDetail[];

  constructor(message: string, errors: ValidationErrorDetail[]) {
    super('VALIDATION_ERROR', message, 400, { errors });
    this.errors = errors;
  }

  public override toJSON() {
    return {
      ...super.toJSON(),
      errors: this.errors,
    };
  }
}
