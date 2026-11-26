import { HttpError } from '../../../libs/errors/http.errors';
import { ControlPlaneError } from '../domain/errors';

/** A control-plane refusal as the error middleware answers it (the catalogue's code and status); anything else unchanged. */
export const controlPlaneHttpError = (error: unknown): unknown =>
  error instanceof ControlPlaneError
    ? new HttpError(error.code, error.message, error.status)
    : error;
