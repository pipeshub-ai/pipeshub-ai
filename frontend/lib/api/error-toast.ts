import { toast, useToastStore } from '@/lib/store/toast-store';
import { ErrorType, getUserFacingErrorMessage, ProcessedError } from './api-error';

interface ErrorToastConfig {
  title: string;
  description: string;
}

const activeErrorToasts = new Map<ErrorType, string>();

const BUSY_STATUSES = new Set([429, 503, 504]);

const ERROR_TOAST_MAP: Record<ErrorType, ErrorToastConfig | null> = {
  [ErrorType.AUTHENTICATION_ERROR]: null, // Handled by redirect
  [ErrorType.REQUEST_CANCELLED]: null, // Abort/superseded request — not user-actionable
  [ErrorType.AUTHORIZATION_ERROR]: {
    title: 'Access Denied',
    description: 'You don\'t have permission to perform this action.',
  },
  [ErrorType.VALIDATION_ERROR]: {
    title: 'Invalid Request',
    description: 'Please check your input and try again.',
  },
  [ErrorType.NOT_FOUND]: {
    title: 'Not Found',
    description: 'The requested resource was not found.',
  },
  [ErrorType.CONFLICT]: {
    title: 'Action Required',
    description: 'A conflict occurred. Please check the details and try again.',
  },
  [ErrorType.NETWORK_ERROR]: {
    title: 'Connection Error',
    description: 'Please check your internet connection and try again.',
  },
  [ErrorType.SERVER_ERROR]: {
    title: 'Server Error',
    description:
      'Something went wrong on our end. Please try again in a moment; if it keeps happening, contact your workspace admin.',
  },
  [ErrorType.TIMEOUT_ERROR]: {
    title: 'Request Timed Out',
    description: 'The server took too long to respond. Please try again.',
  },
  [ErrorType.UNKNOWN_ERROR]: {
    title: 'Something Went Wrong',
    description:
      'That didn\'t work. Please try again; if it keeps happening, contact your workspace admin.',
  },
};

export function showErrorToast(error: ProcessedError): void {
  const config = ERROR_TOAST_MAP[error.type];
  if (!config) return;

  // Deduplicate: skip if a toast for this error type is already showing
  const existingId = activeErrorToasts.get(error.type);
  if (existingId) {
    const stillExists = useToastStore.getState().toasts.some(
      (t) => t.id === existingId && !t.isExiting
    );
    if (stillExists) return;
    activeErrorToasts.delete(error.type);
  }

  // The server's words when they were written for a reader; otherwise the
  // sentence above, which always says what to do next.
  const base = getUserFacingErrorMessage(error, config.description);
  // Quoting the reference is how an admin finds this failure in the logs.
  const description = error.requestId
    ? `${base} Reference: ${error.requestId}`
    : base;
  // Busy or slow is worth a retry, not a "Server Error" scare.
  const title = BUSY_STATUSES.has(error.statusCode ?? 0) ? 'Please try again shortly' : config.title;

  const id = toast.error(title, {
    description,
    duration: null,
    showCloseButton: true,
  });

  activeErrorToasts.set(error.type, id);
}
