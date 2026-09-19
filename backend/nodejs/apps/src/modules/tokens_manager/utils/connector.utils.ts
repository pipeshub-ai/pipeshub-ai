import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  GatewayTimeoutError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  TooManyRequestsError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { BaseError } from '../../../libs/errors/base.error';
import {
  ConnectorServiceCommand,
  ConnectorServiceCommandOptions,
} from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { Response } from 'express';

const logger = Logger.getInstance({
  service: 'Connector Utils',
});

const CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE =
  'Connector Service is currently unavailable. Please check your network connection or try again later.';

/**
 * FastAPI validation errors (422) send `detail` as an array of
 * `{loc, msg, type}` objects rather than a string. Stringifying that array
 * directly (e.g. in a template literal) yields "[object Object]" since
 * Array.prototype.toString calls the default Object.toString on each entry.
 * This extracts a readable message instead.
 */
const stringifyErrorDetail = (detail: unknown): string => {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry) =>
        entry && typeof entry === 'object' && 'msg' in entry
          ? String((entry as { msg: unknown }).msg)
          : JSON.stringify(entry),
      )
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail);
  }
  return 'Unknown error';
};

// The error middleware relays this as the Retry-After header.
const retryAfterMetadata = (
  error: { headers?: Record<string, unknown> } | null | undefined,
): { retryAfter: string } | undefined => {
  const value: unknown = error?.headers?.['retry-after'];
  const text =
    typeof value === 'number' && Number.isFinite(value)
      ? String(value)
      : typeof value === 'string'
        ? value.trim()
        : '';
  return text ? { retryAfter: text } : undefined;
};

const MAX_RETRY_HINT_SECONDS = 120;

/**
 * Seconds to wait from a Retry-After value: whole seconds, or an HTTP date
 * still in the future. Undefined when invalid, past, or too far off to quote.
 */
export const retryAfterToSeconds = (
  value: string | undefined,
  now: number = Date.now(),
): number | undefined => {
  const text = value?.trim();
  if (!text) return undefined;
  let seconds: number;
  if (/^\d+$/.test(text)) {
    seconds = Number(text);
  } else {
    const at = Date.parse(text);
    if (Number.isNaN(at)) return undefined;
    seconds = Math.ceil((at - now) / 1000);
  }
  return seconds > 0 && seconds <= MAX_RETRY_HINT_SECONDS ? seconds : undefined;
};

// Shown when a busy or slow backend sends no message of its own.
const retryHint = (retry: { retryAfter: string } | undefined): string => {
  const seconds = retryAfterToSeconds(retry?.retryAfter);
  return seconds
    ? `Please try again in ${seconds} second${seconds === 1 ? '' : 's'}.`
    : 'Please try again in a few seconds.';
};

const TRANSIENT_FALLBACK: Record<429 | 503 | 504, string> = {
  429: 'PipesHub is handling a lot of requests right now.',
  503: 'This part of PipesHub is briefly unavailable.',
  504: 'This took longer than expected to respond.',
};

const transientError = (
  statusCode: 429 | 503 | 504,
  upstreamDetail: unknown,
  error: { headers?: Record<string, unknown> } | null | undefined,
): Error => {
  const retry = retryAfterMetadata(error);
  const message = upstreamDetail
    ? stringifyErrorDetail(upstreamDetail)
    : `${TRANSIENT_FALLBACK[statusCode]} ${retryHint(retry)}`;
  if (statusCode === 429) return new TooManyRequestsError(message, retry);
  if (statusCode === 503) return new ServiceUnavailableError(message, retry);
  return new GatewayTimeoutError(message, retry);
};

export const handleBackendError = (error: any, operation: string): Error => {
  // Already mapped (e.g. thrown by a pre-check and caught again); re-mapping
  // would turn any status outside the switch below into a 500.
  if (error instanceof BaseError) {
    return error;
  }
  if (error) {
    if (
      (error?.cause && error.cause.code === 'ECONNREFUSED') ||
      (typeof error?.message === 'string' &&
        error.message.includes('fetch failed'))
    ) {
      return new ServiceUnavailableError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    const { statusCode, data, message } = error;
    const errorDetail = stringifyErrorDetail(
      data?.detail || data?.reason || data?.message || message || 'Unknown error',
    );

    logger.error(`Backend error during ${operation}`, {
      statusCode,
      errorDetail,
      fullResponse: data,
    });

    if (errorDetail === 'ECONNREFUSED') {
      throw new ServiceUnavailableError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    switch (statusCode) {
      case 400:
        return new BadRequestError(errorDetail);
      case 401:
        return new UnauthorizedError(errorDetail);
      case 403:
        return new ForbiddenError(errorDetail);
      case 404:
        return new NotFoundError(errorDetail);
      case 409:
        return new ConflictError(errorDetail);
      case 422:
        return new BadRequestError(errorDetail);
      // Transient: the caller should retry, so they must not read as a 500.
      case 429:
      case 503:
      case 504:
        return transientError(
          statusCode,
          data?.detail || data?.reason || data?.message,
          error,
        );
      case 500:
        return new InternalServerError(errorDetail);
      default:
        return new InternalServerError(`Backend error: ${errorDetail}`);
    }
  }

  if (error.request) {
    logger.error(`No response from backend during ${operation}`);
    return new InternalServerError('Backend service unavailable');
  }

  return new InternalServerError(`${operation} failed: ${error.message}`);
};

// Helper function to execute connector service commands
export const executeConnectorCommand = async (
  uri: string,
  method: HttpMethod,
  headers: Record<string, string>,
  body?: any,
) => {
  const connectorCommandOptions: ConnectorServiceCommandOptions = {
    uri,
    method,
    headers: {
      ...headers,
      // Lowercase — sanitizeHeaders normalizes keys; avoid a second
      // Content-Type that fetch would join into "application/json, application/json".
      'content-type': 'application/json',
    },
    ...(body && { body }),
  };
  const connectorCommand = new ConnectorServiceCommand(connectorCommandOptions);
  return await connectorCommand.execute();
};

// Helper function to handle common connector response logic
export const handleConnectorResponse = (
  connectorResponse: any,
  res: Response,
  operation: string,
  failureMessage: string,
) => {
  const statusCode = connectorResponse?.statusCode;
  const isSuccess = statusCode >= 200 && statusCode < 300;
  if (connectorResponse && !isSuccess) {
    throw handleBackendError(connectorResponse, operation);
  }
  const connectorsData = connectorResponse.data;
  if (!connectorsData) {
    throw new NotFoundError(`${operation} failed: ${failureMessage}`);
  }
  res.status(statusCode ?? 200).json(connectorsData);
};
