import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
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

export const handleBackendError = (error: any, operation: string): Error => {
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
  timeoutMs?: number,
) => {
  const connectorCommandOptions: ConnectorServiceCommandOptions = {
    uri,
    method,
    headers: {
      ...headers,
      'Content-Type': 'application/json',
    },
    ...(body && { body }),
    ...(timeoutMs && { timeoutMs }),
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
