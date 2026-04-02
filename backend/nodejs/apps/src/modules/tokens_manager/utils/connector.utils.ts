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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** Safe fields for logging caught errors from connector proxy handlers. */
export const getConnectorErrorLogFields = (
  error: unknown,
): { message: string; status?: number; data?: unknown } => {
  if (error instanceof Error) {
    const withResponse = error as Error & {
      response?: { status?: number; data?: unknown };
    };
    return {
      message: error.message,
      status: withResponse.response?.status,
      data: withResponse.response?.data,
    };
  }
  if (isRecord(error)) {
    const msg = typeof error.message === 'string' ? error.message : String(error);
    const resp = error.response;
    if (isRecord(resp)) {
      return {
        message: msg,
        status:
          typeof resp.status === 'number' ? resp.status : undefined,
        data: resp.data,
      };
    }
    return { message: msg };
  }
  return { message: String(error) };
};

export const handleBackendError = (error: unknown, operation: string): Error => {
  if (error) {
    const cause =
      isRecord(error) && isRecord(error.cause)
        ? (error.cause as { code?: string }).code
        : undefined;
    const errMessage =
      isRecord(error) && typeof error.message === 'string'
        ? error.message
        : error instanceof Error
          ? error.message
          : undefined;

    if (
      cause === 'ECONNREFUSED' ||
      (errMessage?.includes('fetch failed') ?? false)
    ) {
      return new ServiceUnavailableError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    if (isRecord(error)) {
      const statusCode = error.statusCode as number | undefined;
      const data = error.data as
        | {
            detail?: unknown;
            reason?: unknown;
            message?: unknown;
          }
        | undefined;
      const message = error.message as string | undefined;

      const rawErrorDetail =
        data?.detail ??
        data?.reason ??
        data?.message ??
        message ??
        'Unknown error';
      const errorDetail =
        typeof rawErrorDetail === 'string'
          ? rawErrorDetail
          : JSON.stringify(rawErrorDetail);

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
        case 422:
          return new BadRequestError(errorDetail);
        case 401:
          return new UnauthorizedError(errorDetail);
        case 403:
          return new ForbiddenError(errorDetail);
        case 404:
          return new NotFoundError(errorDetail);
        case 409:
          return new ConflictError(errorDetail);
        case 500:
          return new InternalServerError(errorDetail);
        default:
          return new InternalServerError(`Backend error: ${errorDetail}`);
      }
    }

    if (isRecord(error) && error.request) {
      logger.error(`No response from backend during ${operation}`);
      return new InternalServerError('Backend service unavailable');
    }
  }

  const fallback =
    error instanceof Error ? error.message : String(error ?? 'unknown');
  return new InternalServerError(`${operation} failed: ${fallback}`);
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
      'Content-Type': 'application/json',
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
