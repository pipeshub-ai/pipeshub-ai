// What the explorer imports from '@/lib/api': a client whose requests go to the in-browser fake backend.
import { handle } from '../fake-graph-server';

export enum ErrorType {
  AUTHENTICATION_ERROR = 'AUTHENTICATION_ERROR',
  AUTHORIZATION_ERROR = 'AUTHORIZATION_ERROR',
  VALIDATION_ERROR = 'VALIDATION_ERROR',
  NOT_FOUND = 'NOT_FOUND',
  CONFLICT = 'CONFLICT',
  NETWORK_ERROR = 'NETWORK_ERROR',
  SERVER_ERROR = 'SERVER_ERROR',
  TIMEOUT_ERROR = 'TIMEOUT_ERROR',
  UNKNOWN_ERROR = 'UNKNOWN_ERROR',
  REQUEST_CANCELLED = 'REQUEST_CANCELLED',
}

export interface ProcessedError {
  type: ErrorType;
  message: string;
  statusCode?: number;
}

export const apiClient = {
  async get<T>(url: string, _config?: unknown): Promise<{ data: T }> {
    return { data: (await handle(url, undefined)) as T };
  },
  async post<T>(url: string, body: unknown, _config?: unknown): Promise<{ data: T }> {
    return { data: (await handle(url, body)) as T };
  },
};
